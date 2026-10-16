/* A bounded Canvas 2D field: no image payload, per-particle blur, or GPU dependency. */
(function initHeroField() {
  const hero = document.querySelector(".landing-hero");
  const canvas = hero?.querySelector(".hero-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const toggle = hero.querySelector(".hero-motion-toggle");
  const hint = hero.querySelector(".hero-field-hint");
  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const TAU = Math.PI * 2;
  const pointer = { x: 0, y: 0, active: false };
  const atom = { teleport: null, chargedAt: -Infinity, spin: 0 };
  // Hardware hints only choose the initial budget. Measured paint time can lower it.
  const modestDevice = (navigator.hardwareConcurrency || 8) <= 4 ||
    (navigator.deviceMemory || 8) <= 4;
  const trailSteps = modestDevice ? 6 : 10;
  // Cache the fade colors. Highlights follow the orbit curves without blur filters.
  const trailColors = [
    { halo: "83, 241, 179", line: "185, 255, 225" },
    { halo: "86, 195, 255", line: "195, 241, 255" },
    { halo: "243, 127, 91", line: "255, 197, 163" },
  ].map(({ halo, line }) => Array.from({ length: trailSteps }, (_, index) => {
    const strength = ((index + 1) / trailSteps) ** 1.5;
    return {
      halo: `rgba(${halo}, ${strength * 0.18})`,
      line: `rgba(${line}, ${strength * 0.94})`,
    };
  }));
  const glow = document.createElement("canvas");
  glow.width = glow.height = 96;
  const glowCtx = glow.getContext("2d");
  if (glowCtx) {
    const gradient = glowCtx.createRadialGradient(48, 48, 0, 48, 48, 48);
    gradient.addColorStop(0, "rgba(171, 255, 219, 0.85)");
    gradient.addColorStop(0.12, "rgba(73, 238, 178, 0.45)");
    gradient.addColorStop(0.4, "rgba(40, 192, 167, 0.1)");
    gradient.addColorStop(1, "rgba(40, 192, 167, 0)");
    glowCtx.fillStyle = gradient;
    glowCtx.fillRect(0, 0, 96, 96);
  }
  const warmGlow = document.createElement("canvas");
  warmGlow.width = warmGlow.height = 96;
  const warmGlowCtx = warmGlow.getContext("2d");
  if (warmGlowCtx) {
    const gradient = warmGlowCtx.createRadialGradient(48, 48, 0, 48, 48, 48);
    gradient.addColorStop(0, "rgba(255, 211, 181, 0.85)");
    gradient.addColorStop(0.12, "rgba(244, 136, 91, 0.45)");
    gradient.addColorStop(0.4, "rgba(204, 79, 62, 0.1)");
    gradient.addColorStop(1, "rgba(204, 79, 62, 0)");
    warmGlowCtx.fillStyle = gradient;
    warmGlowCtx.fillRect(0, 0, 96, 96);
  }

  let width = 0;
  let height = 0;
  let columns = 0;
  let rows = 0;
  let coreX = 0;
  let coreY = 0;
  let homeX = 0;
  let homeY = 0;
  let coreAlpha = 1;
  let coreScale = 1;
  let coreEnergy = 0;
  let rowColor = null;
  let columnColor = null;
  let dotColor = null;
  let radius = 0;
  let vertices = [];
  let positions = new Float32Array(0);
  let ripples = [];
  // Start with motion enabled; the toggle can pause it at any time.
  let paused = false;
  let visible = false;
  let timerId = 0;
  let frameId = 0;
  let fieldTime = 0;
  let lastTime = 0;
  let lastPaintTime = 0;
  let paintAverage = 0;
  let slowFrames = 0;
  let frameBudget = modestDevice ? 1000 / 20 : 1000 / 30;

  function canInteract() {
    return visible && !document.hidden;
  }

  function canAnimate() {
    return canInteract() && !paused;
  }

  function stop() {
    window.clearTimeout(timerId);
    cancelAnimationFrame(frameId);
    timerId = frameId = 0;
    lastTime = 0;
  }

  function queueFrame(delay = 0) {
    if (!canAnimate() || timerId || frameId) return;
    // Sleep between paints instead of waking at the display's full refresh rate.
    timerId = window.setTimeout(() => {
      timerId = 0;
      frameId = requestAnimationFrame(frame);
    }, delay);
  }

  function requestPaint() {
    if (!canInteract() || canAnimate() || timerId || frameId) return;
    // Static mode paints only in response to input, with the same frame limit.
    timerId = window.setTimeout(() => {
      timerId = 0;
      frameId = requestAnimationFrame(() => {
        frameId = 0;
        if (canInteract()) paint();
      });
    }, Math.max(0, frameBudget - (performance.now() - lastPaintTime)));
  }

  function frame(now) {
    frameId = 0;
    if (!canAnimate()) return;
    if (lastTime) {
      const delta = Math.min(0.08, (now - lastTime) / 1000);
      fieldTime += delta;
      atom.spin += delta * coreEnergy * 3;
    }
    lastTime = now;
    const started = performance.now();
    paint();
    const cost = performance.now() - started;
    paintAverage = paintAverage * 0.9 + cost * 0.1;
    slowFrames = paintAverage > 7 ? slowFrames + 1 : 0;
    if (slowFrames >= 30 && frameBudget < 1000 / 15) {
      frameBudget = 1000 / 15;
      slowFrames = 0;
    }
    queueFrame(Math.max(0, frameBudget - cost - 2));
  }

  function syncAnimation() {
    stop();
    pointer.active = false;
    ripples = [];
    if (visible && !document.hidden) paint();
    queueFrame();
  }

  function resize() {
    const rect = hero.getBoundingClientRect();
    const nextWidth = Math.max(1, Math.round(rect.width));
    const nextHeight = Math.max(1, Math.round(rect.height));
    if (nextWidth === width && nextHeight === height) return;
    width = nextWidth;
    height = nextHeight;
    // Bound the backing store even on high DPI, ultrawide, and zoomed displays.
    const scale = Math.min(window.devicePixelRatio || 1, modestDevice ? 1 : 1.25,
      Math.sqrt(900000 / (width * height)));
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    ctx.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
    columns = modestDevice || width < 600 ? 26 : 36;
    rows = modestDevice || width < 600 ? 12 : 16;
    homeX = width * 0.77;
    homeY = height * 0.46;
    radius = Math.min(width * 0.22, height * 0.27, 142);
    atom.teleport = null;
    // Fade only the mesh behind the copy; the moving atom stays visible everywhere.
    function meshColor(rgb, leftAlpha, rightAlpha) {
      const gradient = ctx.createLinearGradient(0, 0, width, 0);
      gradient.addColorStop(0, `rgba(${rgb}, ${leftAlpha})`);
      gradient.addColorStop(0.35, `rgba(${rgb}, ${leftAlpha * 1.5})`);
      gradient.addColorStop(1, `rgba(${rgb}, ${rightAlpha})`);
      return gradient;
    }
    rowColor = meshColor("66, 218, 170", 0.04, 0.3);
    columnColor = meshColor("71, 176, 211", 0.025, 0.16);
    dotColor = meshColor("155, 255, 218", 0.08, 0.65);
    vertices = [];
    for (let row = 0; row <= rows; row += 1) {
      for (let col = 0; col <= columns; col += 1) {
        const x = (col / columns * 1.12 - 0.06) * width;
        const y = (row / rows * 1.05 + 0.02) * height;
        vertices.push({
          x,
          y,
          phase: col * 0.28 + row * 0.35,
          weight: 0,
        });
      }
    }
    positions = new Float32Array(vertices.length * 2);
    syncAnimation();
  }

  function updateAtom() {
    const driftX = Math.sin(fieldTime * 0.65) * Math.min(14, width * 0.025);
    const driftY = Math.sin(fieldTime * 0.53) * 10;
    coreX = homeX + driftX;
    coreY = homeY + driftY;
    coreAlpha = coreScale = 1;
    const chargeAge = fieldTime - atom.chargedAt;
    coreEnergy = Math.max(0, 1 - chargeAge / 2.2) ** 2;
    const jump = atom.teleport;
    if (!jump) return;
    const age = Math.max(0, fieldTime - jump.started);
    if (age < 0.16) {
      // Flash and collapse at the departure point before appearing at the target.
      coreX = jump.fromX;
      coreY = jump.fromY;
      coreAlpha = 1 - age / 0.16;
      coreScale = 1 - age / 0.16 * 0.65;
    } else {
      const arrival = age - 0.16;
      const remaining = Math.exp(-Math.max(0, arrival - 0.28) * 0.62);
      coreX = jump.toX * remaining + (homeX + driftX) * (1 - remaining);
      coreY = jump.toY * remaining + (homeY + driftY) * (1 - remaining);
      coreAlpha = Math.min(1, arrival / 0.12);
      coreScale = 0.45 + coreAlpha * 0.55;
      coreEnergy = Math.max(coreEnergy, Math.max(0, 1 - arrival / 0.8));
      if (remaining < 0.002) atom.teleport = null;
    }
  }

  function updatePositions() {
    const reach = Math.min(220, width * 0.5);
    ripples = ripples.filter((ripple) => fieldTime - ripple.born < 1.8);
    for (let i = 0; i < vertices.length; i += 1) {
      const vertex = vertices[i];
      const coreDx = vertex.x - coreX;
      const coreDy = vertex.y - coreY;
      const weight = Math.exp(-(coreDx * coreDx + coreDy * coreDy * 1.8) / (radius * radius * 3));
      vertex.weight = weight;
      let x = vertex.x + coreDx * weight * 0.22 + Math.cos(fieldTime * 0.5 + vertex.phase) * weight * 9;
      let y = vertex.y + coreDy * weight * 0.48 + Math.sin(fieldTime * 0.7 + vertex.phase) * (4 + weight * 12);
      if (pointer.active) {
        const dx = x - pointer.x;
        const dy = y - pointer.y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        if (distance < reach && distance > 0) {
          const push = (1 - distance / reach) ** 2 * 64;
          x += dx / distance * push;
          y += dy / distance * push;
        }
      }
      for (const ripple of ripples) {
        const dx = x - ripple.x;
        const dy = y - ripple.y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        const age = fieldTime - ripple.born;
        const band = (distance - age * 290) / 42;
        if (Math.abs(band) < 1 && distance > 0) {
          const push = Math.cos(band * Math.PI / 2) * (1 - age / 1.8) * 38;
          x += dx / distance * push;
          y += dy / distance * push;
        }
      }
      positions[i * 2] = x;
      positions[i * 2 + 1] = y;
    }
  }

  function traceGridCurve(start, stride, count) {
    let current = start;
    ctx.moveTo(positions[current], positions[current + 1]);
    // Catmull-Rom tangents keep both sides of each grid point smoothly aligned.
    // The cubic passes through the points, so crossing lines and dots still meet.
    for (let point = 0; point < count - 1; point += 1) {
      const previous = point === 0 ? current : current - stride;
      const next = current + stride;
      const after = point + 2 < count ? next + stride : next;
      ctx.bezierCurveTo(
        positions[current] + (positions[next] - positions[previous]) / 6,
        positions[current + 1] + (positions[next + 1] - positions[previous + 1]) / 6,
        positions[next] - (positions[after] - positions[current]) / 6,
        positions[next + 1] - (positions[after + 1] - positions[current + 1]) / 6,
        positions[next], positions[next + 1]
      );
      current = next;
    }
  }

  function drawMesh() {
    ctx.lineWidth = 0.8;
    ctx.strokeStyle = rowColor;
    ctx.beginPath();
    for (let row = 0; row <= rows; row += 1) {
      traceGridCurve(row * (columns + 1) * 2, 2, columns + 1);
    }
    ctx.stroke();
    ctx.strokeStyle = columnColor;
    ctx.beginPath();
    for (let col = 0; col <= columns; col += 1) {
      traceGridCurve(col * 2, (columns + 1) * 2, rows + 1);
    }
    ctx.stroke();
    ctx.fillStyle = dotColor;
    ctx.beginPath();
    for (let row = 0; row <= rows; row += 2) {
      for (let col = 0; col <= columns; col += 3) {
        const i = (row * (columns + 1) + col) * 2;
        const size = vertices[i / 2].weight > 0.5 ? 1.7 : 1.1;
        ctx.moveTo(positions[i] + size, positions[i + 1]);
        ctx.arc(positions[i], positions[i + 1], size, 0, TAU);
      }
    }
    ctx.fill();
  }

  function drawOrbitHighlight(orbit, angle, phase) {
    const colors = trailColors[orbit];
    const length = 1.05 + coreEnergy * 0.3;
    const cosAngle = Math.cos(angle);
    const sinAngle = Math.sin(angle);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.lineCap = "round";
    // The same ellipse and phase as the point keep light attached to the line.
    // Mirror the fade ahead of and behind the point, brightest at its center.
    for (let step = 0; step < trailSteps; step += 1) {
      const start = phase - length + length * step / trailSteps;
      const end = phase - length + length * (step + 1) / trailSteps;
      ctx.beginPath();
      ctx.ellipse(0, 0, radius, radius * 0.32, angle, start, end);
      const frontStart = 2 * phase - end;
      const frontEnd = 2 * phase - start;
      const x = Math.cos(frontStart) * radius;
      const y = Math.sin(frontStart) * radius * 0.32;
      // Start a separate subpath so the highlights never form a chord across the orbit.
      ctx.moveTo(x * cosAngle - y * sinAngle, x * sinAngle + y * cosAngle);
      ctx.ellipse(0, 0, radius, radius * 0.32, angle, frontStart, frontEnd);
      ctx.strokeStyle = colors[step].halo;
      ctx.lineWidth = 6 + coreEnergy * 3;
      ctx.stroke();
      ctx.strokeStyle = colors[step].line;
      ctx.lineWidth = 1.8 + coreEnergy * 0.7;
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawCore() {
    const aimX = pointer.active ? (pointer.x / width - 0.5) * 2 : 0;
    const aimY = pointer.active ? (pointer.y / height - 0.5) * 2 : 0;
    const tilt = -0.45 + Math.sin(fieldTime * 0.3) * 0.25 + aimX * 0.7 + aimY * 0.4 + atom.spin * 0.2;
    ctx.save();
    ctx.translate(coreX, coreY);
    ctx.scale(coreScale, coreScale);
    ctx.globalAlpha = coreAlpha;
    if (glowCtx) {
      const halo = radius * (1.25 + coreEnergy * 0.65);
      ctx.drawImage(glow, -halo, -halo, halo * 2, halo * 2);
      if (coreEnergy > 0) {
        ctx.save();
        ctx.globalAlpha *= coreEnergy;
        ctx.globalCompositeOperation = "lighter";
        ctx.drawImage(glow, -radius, -radius, radius * 2, radius * 2);
        ctx.restore();
      }
    }
    ctx.lineWidth = 1 + coreEnergy * 0.9;
    ctx.strokeStyle = `rgba(116, 245, 204, ${0.5 + coreEnergy * 0.45})`;
    ctx.beginPath();
    ctx.arc(0, 0, radius, 0, TAU);
    ctx.stroke();
    // Three intersecting orbits suggest a Bloch sphere and entangled signals.
    for (let orbit = 0; orbit < 3; orbit += 1) {
      const angle = tilt + orbit * Math.PI / 3;
      ctx.strokeStyle = orbit === 1
        ? `rgba(98, 204, 245, ${0.6 + coreEnergy * 0.35})`
        : orbit === 2
          ? `rgba(238, 157, 119, ${0.4 + coreEnergy * 0.5})`
          : `rgba(88, 235, 179, ${0.4 + coreEnergy * 0.5})`;
      ctx.beginPath();
      ctx.ellipse(0, 0, radius, radius * 0.32, angle, 0, TAU);
      ctx.stroke();
      const phase = fieldTime * (0.36 + orbit * 0.08) + orbit * TAU / 3 + aimX * 0.8 + atom.spin;
      drawOrbitHighlight(orbit, angle, phase);
      const x = Math.cos(phase) * radius;
      const y = Math.sin(phase) * radius * 0.32;
      const px = x * Math.cos(angle) - y * Math.sin(angle);
      const py = x * Math.sin(angle) + y * Math.cos(angle);
      const pointGlow = orbit === 2 && warmGlowCtx ? warmGlow : glow;
      if (glowCtx) ctx.drawImage(pointGlow, px - 14, py - 14, 28, 28);
      ctx.fillStyle = orbit === 1 ? "#a1e7ff" : orbit === 2 ? "#ffb18d" : "#b5ffe0";
      ctx.beginPath();
      ctx.arc(px, py, 3.4, 0, TAU);
      ctx.fill();
    }
    ctx.fillStyle = "#c4ffe4";
    ctx.beginPath();
    ctx.arc(0, 0, 3 + coreEnergy * 3, 0, TAU);
    ctx.fill();
    ctx.restore();
  }

  function drawTeleportFlash() {
    const jump = atom.teleport;
    if (!jump) return;
    const age = fieldTime - jump.started;
    if (age < 0 || age > 0.55) return;
    const departing = age < 0.16;
    const strength = departing ? Math.sin(Math.PI * age / 0.16) : Math.max(0, 1 - (age - 0.16) / 0.39);
    const x = departing ? jump.fromX : jump.toX;
    const y = departing ? jump.fromY : jump.toY;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    ctx.globalAlpha = strength;
    if (glowCtx) {
      const size = radius * (1.2 + strength * 0.7);
      ctx.drawImage(glow, x - size, y - size, size * 2, size * 2);
    }
    ctx.strokeStyle = "#d8fff1";
    ctx.lineWidth = 1.5;
    const flare = radius * strength;
    ctx.beginPath();
    ctx.moveTo(x - flare, y);
    ctx.lineTo(x + flare, y);
    ctx.moveTo(x, y - flare * 0.65);
    ctx.lineTo(x, y + flare * 0.65);
    ctx.stroke();
    ctx.restore();
  }

  function drawPointer() {
    if (!pointer.active) return;
    ctx.save();
    ctx.strokeStyle = "rgba(111, 236, 227, 0.45)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 7]);
    ctx.beginPath();
    ctx.moveTo(pointer.x, pointer.y);
    ctx.lineTo(coreX, coreY);
    ctx.stroke();
    ctx.setLineDash([]);
    if (glowCtx) ctx.drawImage(glow, pointer.x - 32, pointer.y - 32, 64, 64);
    ctx.strokeStyle = "rgba(174, 255, 231, 0.95)";
    ctx.beginPath();
    ctx.arc(pointer.x, pointer.y, 14, 0, TAU);
    ctx.moveTo(pointer.x - 21, pointer.y);
    ctx.lineTo(pointer.x - 10, pointer.y);
    ctx.moveTo(pointer.x + 10, pointer.y);
    ctx.lineTo(pointer.x + 21, pointer.y);
    ctx.moveTo(pointer.x, pointer.y - 21);
    ctx.lineTo(pointer.x, pointer.y - 10);
    ctx.moveTo(pointer.x, pointer.y + 10);
    ctx.lineTo(pointer.x, pointer.y + 21);
    ctx.stroke();
    ctx.restore();
  }

  function paint() {
    if (!width || !height) return;
    lastPaintTime = performance.now();
    ctx.clearRect(0, 0, width, height);
    updateAtom();
    updatePositions();
    drawMesh();
    drawCore();
    drawTeleportFlash();
    drawPointer();
    for (const ripple of ripples) {
      const age = fieldTime - ripple.born;
      ctx.strokeStyle = `rgba(150, 255, 223, ${(1 - age / 1.8) * 0.85})`;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(ripple.x, ripple.y, Math.max(1, age * 290), 0, TAU);
      ctx.stroke();
    }
  }

  function updateToggle() {
    toggle.hidden = false;
    toggle.setAttribute("aria-pressed", String(!paused));
    toggle.setAttribute("aria-label", "Animate field");
    toggle.textContent = paused ? "Enable motion" : "Pause motion";
    hint.hidden = false;
  }

  function updatePointer(event) {
    if (!canInteract() || (event.pointerType === "touch" && event.type === "pointermove")) return;
    if (event.target.closest("a, button, .hero-display-controls")) {
      clearPointer();
      return;
    }
    const rect = canvas.getBoundingClientRect();
    pointer.x = event.clientX - rect.left;
    pointer.y = event.clientY - rect.top;
    pointer.active = true;
    requestPaint();
  }

  hero.addEventListener("pointermove", updatePointer, { passive: true });
  function clearPointer() {
    pointer.active = false;
    if (paused) ripples = [];
    requestPaint();
  }
  hero.addEventListener("pointerleave", clearPointer, { passive: true });
  hero.addEventListener("pointercancel", clearPointer, { passive: true });
  hero.addEventListener("pointerup", (event) => {
    if (!canInteract() || event.target.closest("a, button, .hero-display-controls") || event.button !== 0) return;
    updatePointer(event);
    const onAtom = Math.hypot(pointer.x - coreX, pointer.y - coreY) <= radius * coreScale && coreAlpha > 0.25;
    if (onAtom) {
      atom.chargedAt = fieldTime;
    } else {
      atom.teleport = {
        fromX: coreX, fromY: coreY,
        toX: pointer.x, toY: pointer.y,
        started: fieldTime - (paused ? 0.32 : 0),
      };
    }
    if (ripples.length >= 3) ripples.shift();
    // In static mode, show a pulse snapshot without starting an animation loop.
    ripples.push({ x: pointer.x, y: pointer.y, born: fieldTime - (paused ? 0.28 : 0) });
    if (event.pointerType === "touch") pointer.active = false;
    requestPaint();
  }, { passive: true });
  toggle.addEventListener("click", () => {
    paused = !paused;
    updateToggle();
    syncAnimation();
  });
  motionQuery.addEventListener("change", (event) => {
    paused = event.matches;
    updateToggle();
    syncAnimation();
  });
  document.addEventListener("visibilitychange", syncAnimation);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(resize).observe(hero);
  else window.addEventListener("resize", resize, { passive: true });
  if (typeof IntersectionObserver !== "undefined") {
    new IntersectionObserver((entries) => {
      visible = entries.some((entry) => entry.isIntersecting);
      syncAnimation();
    }, { threshold: 0 }).observe(hero);
  } else {
    visible = true;
  }
  resize();
  updateToggle();
})();
