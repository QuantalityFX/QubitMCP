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

/* Animated qubit/binary field behind the landing hero. */
(function initHeroQuantumField() {
  const hero = document.querySelector(".landing-hero");
  const canvas = hero ? hero.querySelector(".hero-canvas") : null;
  if (!hero || !canvas) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const tokens = ["0", "1", "|0>", "|1>", "H", "X", "Z", "+", "-"];
  const mouse = { x: 0, y: 0, active: false };

  let width = 0;
  let height = 0;
  let dpr = 1;
  let streams = [];
  let particles = [];
  let animationId = 0;
  let lastTime = 0;
  let reducedMotion = motionQuery.matches;

  function random(min, max) {
    return min + Math.random() * (max - min);
  }

  function buildStreams() {
    const step = 27;
    const count = Math.ceil(width / step) + 4;
    streams = Array.from({ length: count }, (_, index) => ({
      x: index * step + random(-10, 10),
      y: random(-height, height),
      speed: random(26, 78),
      gap: random(18, 30),
      length: Math.floor(random(7, 15)),
      tokenOffset: Math.floor(random(0, tokens.length)),
    }));
  }

  function buildParticles() {
    const count = Math.max(42, Math.min(110, Math.floor((width * height) / 8500)));
    particles = Array.from({ length: count }, () => ({
      x: random(0, width),
      y: random(0, height),
      vx: random(-9, 9),
      vy: random(-7, 7),
      size: random(1.2, 2.8),
      tokenOffset: Math.floor(random(0, tokens.length)),
    }));
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
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    buildStreams();
    buildParticles();
    restartAnimation();
  }

  function cursorInfluence(x, y, radius) {
    if (!mouse.active) return { amount: 0, dx: 0, dy: 0, dist: radius };
    const dx = x - mouse.x;
    const dy = y - mouse.y;
    const dist = Math.max(1, Math.hypot(dx, dy));
    const amount = Math.max(0, 1 - dist / radius);
    return { amount, dx, dy, dist };
  }

  function drawStreams(now, dt) {
    ctx.save();
    ctx.font = '13px "Cascadia Mono", "Consolas", monospace';
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";

    for (const stream of streams) {
      if (!reducedMotion) {
        stream.y += stream.speed * dt;
        if (stream.y - stream.length * stream.gap > height + 70) {
          stream.y = random(-180, -20);
          stream.x = random(-20, width + 20);
        }
      }

      for (let i = 0; i < stream.length; i += 1) {
        const baseY = stream.y - i * stream.gap;
        if (baseY < -30 || baseY > height + 30) continue;

        const influence = cursorInfluence(stream.x, baseY, 180);
        const bend = influence.amount * influence.amount * 54;
        const x = stream.x + (influence.dx / influence.dist) * bend;
        const y = baseY + (influence.dy / influence.dist) * bend * 0.28;
        const head = i === 0 ? 1 : 0;
        const fade = 1 - i / Math.max(1, stream.length);
        const alpha = Math.min(0.82, 0.14 + fade * 0.34 + influence.amount * 0.42 + head * 0.12);
        const token = tokens[(stream.tokenOffset + i + Math.floor(now / 260)) % tokens.length];

        ctx.fillStyle = head || influence.amount > 0.3
          ? `rgba(126, 255, 189, ${alpha})`
          : `rgba(56, 189, 248, ${alpha})`;
        ctx.fillText(token, x, y);
      }
    }

    ctx.restore();
  }

  function updateParticle(particle, dt) {
    if (reducedMotion) return;

    const influence = cursorInfluence(particle.x, particle.y, 190);
    if (influence.amount > 0) {
      const force = influence.amount * influence.amount * 68 * dt;
      particle.vx += (influence.dx / influence.dist) * force;
      particle.vy += (influence.dy / influence.dist) * force;
    }

    particle.vx *= 0.992;
    particle.vy *= 0.992;
    particle.x += particle.vx * dt;
    particle.y += particle.vy * dt;

    if (particle.x < -20) particle.x = width + 20;
    if (particle.x > width + 20) particle.x = -20;
    if (particle.y < -20) particle.y = height + 20;
    if (particle.y > height + 20) particle.y = -20;
  }

  function drawParticleLinks(now) {
    const maxDist = 124;
    ctx.save();
    ctx.lineWidth = 1;

    for (let i = 0; i < particles.length; i += 1) {
      const a = particles[i];
      for (let j = i + 1; j < particles.length; j += 1) {
        const b = particles[j];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (dist > maxDist) continue;

        const midX = (a.x + b.x) * 0.5;
        const midY = (a.y + b.y) * 0.5;
        const influence = cursorInfluence(midX, midY, 210);
        const alpha = (1 - dist / maxDist) * (0.08 + influence.amount * 0.18);
        ctx.strokeStyle = `rgba(56, 189, 248, ${alpha})`;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    ctx.font = '10px "Cascadia Mono", "Consolas", monospace';
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (let i = 0; i < particles.length; i += 1) {
      const p = particles[i];
      const influence = cursorInfluence(p.x, p.y, 170);
      const alpha = 0.22 + influence.amount * 0.52;
      ctx.fillStyle = `rgba(125, 249, 183, ${alpha})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size + influence.amount * 1.8, 0, Math.PI * 2);
      ctx.fill();

      if ((i + Math.floor(now / 900)) % 7 === 0 || influence.amount > 0.5) {
        const token = tokens[(p.tokenOffset + Math.floor(now / 520)) % tokens.length];
        ctx.fillStyle = `rgba(214, 226, 236, ${0.15 + influence.amount * 0.55})`;
        ctx.fillText(token, p.x + 10, p.y - 10);
      }
    }

    ctx.restore();
  }

  function drawMouseField() {
    if (!mouse.active) return;
    ctx.save();
    ctx.lineWidth = 1;
    ctx.strokeStyle = "rgba(126, 255, 189, 0.26)";
    ctx.beginPath();
    ctx.arc(mouse.x, mouse.y, 34, 0, Math.PI * 2);
    ctx.stroke();
    ctx.strokeStyle = "rgba(56, 189, 248, 0.16)";
    ctx.beginPath();
    ctx.arc(mouse.x, mouse.y, 78, 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
  }

  function paint(now, dt) {
    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    drawStreams(now, dt);
    for (const particle of particles) {
      updateParticle(particle, dt);
    }
    drawParticleLinks(now);
    drawMouseField();
    ctx.restore();
  }

  function animate(now) {
    const dt = Math.min(0.05, Math.max(0.001, (now - lastTime) / 1000 || 0.016));
    lastTime = now;
    paint(now, dt);
    if (!reducedMotion) {
      animationId = requestAnimationFrame(animate);
    }
  }

  function restartAnimation() {
    if (animationId) {
      cancelAnimationFrame(animationId);
      animationId = 0;
    }
    lastTime = performance.now();
    paint(lastTime, 0.016);
    if (!reducedMotion) {
      animationId = requestAnimationFrame(animate);
    }
  }

  function updateMouse(event) {
    const rect = hero.getBoundingClientRect();
    mouse.x = event.clientX - rect.left;
    mouse.y = event.clientY - rect.top;
    mouse.active = true;
    if (reducedMotion) {
      paint(performance.now(), 0);
    }
  }

  hero.addEventListener("pointermove", updateMouse, { passive: true });
  hero.addEventListener("pointerenter", updateMouse, { passive: true });
  hero.addEventListener("pointerleave", () => {
    mouse.active = false;
    if (reducedMotion) {
      paint(performance.now(), 0);
    }
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
    if (!document.hidden) {
      restartAnimation();
    }
  });

  resizeCanvas();
})();
