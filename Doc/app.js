/* Keep sidebar highlight in sync with clicks and scroll position. */
const links = Array.from(document.querySelectorAll(".nav a"));
const pairs = links
  .map((link) => {
    const target = document.querySelector(link.getAttribute("href"));
    return target ? { link, target } : null;
  })
  .filter(Boolean);

function setActive(hash) {
  links.forEach((link) => {
    link.classList.toggle("active", link.getAttribute("href") === hash);
  });
}

function updateActiveFromScroll() {
  if (!pairs.length) return;
  const offset = 24;
  const y = window.scrollY + offset;
  let current = pairs[0];

  for (const pair of pairs) {
    if (y >= pair.target.offsetTop) {
      current = pair;
    } else {
      break;
    }
  }
  setActive(current.link.getAttribute("href"));
}

let ticking = false;
function onScroll() {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    updateActiveFromScroll();
    ticking = false;
  });
}

links.forEach((link) => {
  link.addEventListener("click", () => {
    setActive(link.getAttribute("href"));
  });
});

window.addEventListener("scroll", onScroll, { passive: true });
window.addEventListener("resize", updateActiveFromScroll);
window.addEventListener("hashchange", () => {
  if (location.hash) {
    setActive(location.hash);
  } else {
    updateActiveFromScroll();
  }
});

if (location.hash && document.querySelector(location.hash)) {
  setActive(location.hash);
} else {
  updateActiveFromScroll();
}

/* Image-derived Gaussian splat layer behind the landing hero. */
(function initHeroImageSplatLayer() {
  const hero = document.querySelector(".landing-hero");
  const canvas = hero ? hero.querySelector(".hero-canvas") : null;
  const debugToggle = hero ? hero.querySelector(".hero-debug-toggle") : null;
  if (!hero || !canvas) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const splatData = window.QUBIT_HERO_SPLAT_DATA || {};
  const BACKGROUND_IMAGE_URL =
    splatData.sourceImage || "assets/MatrixRainInception_Thumbnail_006_Contrast_L.png";
  const SOURCE_ASPECT = 2560 / 1440;
  const TAU = Math.PI * 2;
  const mouse = { x: 0, y: 0, active: false };
  const heroImage = new Image();
  const lightCanvas = document.createElement("canvas");
  const lightCtx = lightCanvas.getContext("2d");
  const motionQuery = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false };

  let width = 0;
  let height = 0;
  let dpr = 1;
  let cover = null;
  let splats = [];
  let animationId = 0;
  let reducedMotion = motionQuery.matches;
  let debugSplats = false;
  let heroImageReady = false;

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function sampleLimit() {
    const area = Math.max(1, width * height);
    return Math.round(clamp(area / 31, 7000, 42000));
  }

  function activeSampleLimit() {
    return debugSplats ? 42000 : sampleLimit();
  }

  function resizeCanvas() {
    const rect = hero.getBoundingClientRect();
    width = Math.max(1, Math.round(rect.width));
    height = Math.max(1, Math.round(rect.height));
    dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    lightCanvas.width = width;
    lightCanvas.height = height;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    updateCover();
    restartAnimation();
  }

  function updateCover() {
    const containerAspect = width / height;
    let drawWidth = width;
    let drawHeight = height;
    let x = 0;
    let y = 0;

    if (containerAspect > SOURCE_ASPECT) {
      drawHeight = width / SOURCE_ASPECT;
      y = (height - drawHeight) * 0.5;
    } else {
      drawWidth = height * SOURCE_ASPECT;
      x = (width - drawWidth) * 0.5;
    }

    cover = {
      x,
      y,
      width: drawWidth,
      height: drawHeight,
      scale: drawHeight / 2,
    };
  }

  function splatsFromEmbeddedData(data, desiredCount) {
    if (!data || !data.values || !data.stride) return [];

    const values = data.values;
    const stride = Number(data.stride);
    const sourceCount = Math.floor(values.length / stride);
    const sampleCount = Math.min(sourceCount, desiredCount);
    const result = [];

    if (!Number.isFinite(stride) || stride < 12 || sampleCount <= 0) {
      return result;
    }

    for (let i = 0; i < sampleCount; i += 1) {
      const sourceIndex = Math.min(
        sourceCount - 1,
        Math.floor(((i + 0.5) * sourceCount) / sampleCount)
      );
      const o = sourceIndex * stride;
      result.push({
        u: values[o],
        v: values[o + 1],
        red: values[o + 2],
        green: values[o + 3],
        blue: values[o + 4],
        alpha: values[o + 5],
        axisX: values[o + 6],
        axisY: values[o + 7],
        angle: values[o + 8],
        phase: values[o + 9],
        speed: values[o + 10],
        stream: values[o + 11],
      });
    }

    return result;
  }

  function drawFlashlight(now) {
    if (
      !mouse.active ||
      debugSplats ||
      !heroImageReady ||
      !cover ||
      !lightCtx ||
      lightCanvas.width <= 0 ||
      lightCanvas.height <= 0
    ) {
      return;
    }

    const pulse = Math.sin(now * 0.0024) * 0.035;
    const radius = Math.max(130, Math.min(width, height) * (0.32 + pulse));
    lightCtx.clearRect(0, 0, width, height);

    lightCtx.save();
    lightCtx.beginPath();
    lightCtx.arc(mouse.x, mouse.y, radius, 0, TAU);
    lightCtx.clip();
    lightCtx.filter = "brightness(1.86) contrast(1.12) saturate(1.08)";
    lightCtx.globalAlpha = 0.92;
    lightCtx.drawImage(heroImage, cover.x, cover.y, cover.width, cover.height);
    lightCtx.restore();

    const mask = lightCtx.createRadialGradient(
      mouse.x,
      mouse.y,
      radius * 0.12,
      mouse.x,
      mouse.y,
      radius
    );
    mask.addColorStop(0, "rgba(0, 0, 0, 0.92)");
    mask.addColorStop(0.56, "rgba(0, 0, 0, 0.46)");
    mask.addColorStop(1, "rgba(0, 0, 0, 0)");

    lightCtx.save();
    lightCtx.globalCompositeOperation = "destination-in";
    lightCtx.fillStyle = mask;
    lightCtx.fillRect(0, 0, width, height);
    lightCtx.restore();

    ctx.save();
    ctx.globalCompositeOperation = "source-over";
    ctx.drawImage(lightCanvas, 0, 0, width, height);
    ctx.restore();
  }

  function drawSplats(now) {
    if (!cover || !splats.length) return;

    const debug = debugSplats;
    const t = now * 0.001;
    const motion = debug ? 0 : reducedMotion ? 0.16 : 1;
    const cursorRadius = Math.max(150, Math.min(width, height) * 0.44);
    const streamRange = Math.max(8, Math.min(22, cover.height * 0.045));

    ctx.save();
    ctx.globalCompositeOperation = "source-over";

    for (const splat of splats) {
      const baseX = cover.x + splat.u * cover.width;
      const baseY = cover.y + splat.v * cover.height;
      const flow =
        debug
          ? 0
          : ((t * (2.8 + splat.speed * 3.8) + splat.stream * streamRange) % streamRange) -
            streamRange * 0.5;
      const waveX =
        debug
          ? 0
          : Math.sin(t * (0.62 + splat.speed * 0.18) + splat.phase) *
            (0.45 + splat.stream * 1.1);
      const waveY = debug
        ? 0
        : Math.cos(t * (0.42 + splat.speed * 0.12) + splat.phase * 1.7) * 0.35;
      let x = baseX + waveX * motion;
      let y = baseY + (flow + waveY) * motion;
      let cursorReveal = 0;

      if (!debug && mouse.active) {
        const dx = x - mouse.x;
        const dy = y - mouse.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const influence = clamp(1 - dist / cursorRadius, 0, 1);
        cursorReveal = influence * influence;
        const push = cursorReveal * (76 + splat.stream * 72);
        x += (dx / dist) * push;
        y += (dy / dist) * push;
      }

      const pulse = debug
        ? 1
        : 0.92 + Math.sin(t * (1.2 + splat.speed) + splat.phase) * 0.12 * motion;
      let rx = clamp(splat.axisX * cover.scale * 3.1, 1.05, 11);
      let ry = clamp(splat.axisY * cover.scale * 3.1, 1.05, 11);
      rx *= pulse * (1 + cursorReveal * 0.18);
      ry *= (1.04 - (pulse - 0.92) * 0.45) * (1 + cursorReveal * 0.1);

      const boost = debug ? 1 : 1.04 + splat.stream * 0.22;
      const red = Math.round(clamp(splat.red * boost, 0, 1) * 255);
      const green = Math.round(clamp(splat.green * (debug ? boost : boost + 0.08), 0, 1) * 255);
      const blue = Math.round(clamp(splat.blue * boost, 0, 1) * 255);
      const baseAlpha = clamp(0.085 + splat.alpha * (0.24 + splat.stream * 0.14), 0, 0.68);
      const revealAlpha = clamp(1 - cursorReveal * 0.9, 0.06, 1);
      const alpha = debug ? clamp(splat.alpha, 0, 1) : baseAlpha * revealAlpha;

      if (!debug && motion > 0.2 && cursorReveal < 0.86) {
        const trailAlpha = alpha * (0.08 + splat.stream * 0.08);
        ctx.globalCompositeOperation = "lighter";
        ctx.fillStyle = `rgba(${red}, ${green}, ${blue}, ${trailAlpha})`;
        ctx.beginPath();
        ctx.ellipse(x - waveX * 0.25, y - 5 - splat.speed * 3, rx * 0.42, ry * (1.55 + splat.speed * 0.28), splat.angle, 0, TAU);
        ctx.fill();
      }

      ctx.globalCompositeOperation = "source-over";
      ctx.fillStyle = `rgba(${red}, ${green}, ${blue}, ${alpha})`;
      ctx.beginPath();
      ctx.ellipse(x, y, rx, ry, splat.angle, 0, TAU);
      ctx.fill();
    }

    ctx.restore();
  }

  function paint(now) {
    ctx.clearRect(0, 0, width, height);
    drawFlashlight(now);
    drawSplats(now);
  }

  function animate(now) {
    paint(now);
    animationId = requestAnimationFrame(animate);
  }

  function restartAnimation() {
    if (animationId) {
      cancelAnimationFrame(animationId);
      animationId = 0;
    }
    paint(performance.now());
    if (!document.hidden && splats.length && !debugSplats) {
      animationId = requestAnimationFrame(animate);
    }
  }

  function loadSplats() {
    splats = splatsFromEmbeddedData(splatData, activeSampleLimit());
    restartAnimation();
  }

  function setDebugSplats(enabled) {
    debugSplats = Boolean(enabled);
    hero.classList.toggle("splat-debug", debugSplats);
    if (debugToggle) {
      debugToggle.setAttribute("aria-pressed", debugSplats ? "true" : "false");
      debugToggle.textContent = debugSplats ? "Debug On" : "Debug Off";
      debugToggle.title = debugSplats
        ? "Disable static splat alignment view"
        : "Enable static splat alignment view";
    }
    loadSplats();
  }

  function debugRequestedFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const value = (params.get("debugSplats") || params.get("splatDebug") || "").toLowerCase();
    return value === "1" || value === "true" || window.location.hash === "#splat-debug";
  }

  function updateMouse(event) {
    const rect = hero.getBoundingClientRect();
    mouse.x = event.clientX - rect.left;
    mouse.y = event.clientY - rect.top;
    mouse.active = true;
    hero.style.setProperty("--hero-light-x", `${mouse.x}px`);
    hero.style.setProperty("--hero-light-y", `${mouse.y}px`);
    hero.style.setProperty("--hero-light-radius", `${Math.max(130, Math.min(width, height) * 0.32)}px`);
    hero.classList.add("flashlight-active");
  }

  hero.addEventListener("pointermove", updateMouse, { passive: true });
  hero.addEventListener("pointerenter", updateMouse, { passive: true });
  hero.addEventListener("pointerleave", () => {
    mouse.active = false;
    hero.classList.remove("flashlight-active");
  });
  if (debugToggle) {
    debugToggle.addEventListener("click", () => {
      setDebugSplats(!debugSplats);
    });
  }
  window.addEventListener("keydown", (event) => {
    if (!event.ctrlKey || !event.shiftKey || event.key.toLowerCase() !== "d") return;
    event.preventDefault();
    setDebugSplats(!debugSplats);
  });

  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(resizeCanvas);
    observer.observe(hero);
  } else {
    window.addEventListener("resize", resizeCanvas);
  }

  function handleMotionPreference(event) {
    reducedMotion = event.matches;
    restartAnimation();
  }

  if (typeof motionQuery.addEventListener === "function") {
    motionQuery.addEventListener("change", handleMotionPreference);
  } else if (typeof motionQuery.addListener === "function") {
    motionQuery.addListener(handleMotionPreference);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && animationId) {
      cancelAnimationFrame(animationId);
      animationId = 0;
      return;
    }
    if (!document.hidden && !animationId) {
      restartAnimation();
    }
  });

  heroImage.onload = () => {
    heroImageReady = true;
    restartAnimation();
  };
  heroImage.src = BACKGROUND_IMAGE_URL;

  resizeCanvas();
  setDebugSplats(debugRequestedFromUrl());
})();
