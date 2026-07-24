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

/* Living bio-qubit field behind the landing hero. */
(function initHeroLivingField() {
  const hero = document.querySelector(".landing-hero");
  const canvas = hero ? hero.querySelector(".hero-canvas") : null;
  if (!hero || !canvas) return;

  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const motionQuery = window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : { matches: false };
  const mouse = { x: 0, y: 0, active: false };
  const TAU = Math.PI * 2;
  const colors = {
    green: "126, 255, 189",
    cyan: "56, 189, 248",
    amber: "245, 213, 118",
    white: "214, 226, 236",
  };

  let width = 0;
  let height = 0;
  let dpr = 1;
  let cellSize = 50;
  let rows = 0;
  let cols = 0;
  let cells = [];
  let state = [];
  let nextState = [];
  let activeIndices = [];
  let animationId = 0;
  let lastTime = 0;
  let lastLifeStep = 0;
  let scrollOffset = 0;
  let reducedMotion = motionQuery.matches;

  function rgba(rgb, alpha) {
    return `rgba(${rgb}, ${alpha})`;
  }

  function random(min, max) {
    return min + Math.random() * (max - min);
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function index(row, col) {
    return row * cols + col;
  }

  function wrap(value, max) {
    return (value + max) % max;
  }

  function setLife(row, col, value) {
    if (row < 0 || row >= rows || col < 0 || col >= cols) return;
    state[index(row, col)] = value;
  }

  function lifeAt(row, col) {
    return state[index(wrap(row, rows), wrap(col, cols))];
  }

  function isVisible(cell, margin) {
    return (
      cell.x >= -margin &&
      cell.x <= width + margin &&
      cell.y >= -margin &&
      cell.y <= height + margin
    );
  }

  function motionScale() {
    return reducedMotion ? 0.7 : 1;
  }

  function basePosition(cell, now) {
    const totalHeight = rows * cellSize;
    const drift = scrollOffset % totalHeight;
    const rowShift = Math.sin(cell.row * 1.21 + cell.cluster) * cellSize * 0.16;
    const breathing = Math.sin(now * 0.00034 * cell.speed + cell.phase);
    const x =
      cell.col * cellSize -
      cellSize +
      (cell.row % 2) * cellSize * 0.42 +
      rowShift +
      cell.jitterX +
      breathing * cell.wander;
    const y =
      ((cell.row * cellSize + drift) % totalHeight) -
      cellSize +
      cell.jitterY +
      Math.cos(now * 0.00029 * cell.speed + cell.phase) * cell.wander * 0.55;

    return { x, y };
  }

  function seedLifePatterns() {
    const patterns = [
      [
        [0, 1],
        [1, 2],
        [2, 0],
        [2, 1],
        [2, 2],
      ],
      [
        [0, 1],
        [1, 1],
        [2, 1],
      ],
      [
        [0, 0],
        [0, 1],
        [1, 0],
        [1, 1],
      ],
      [
        [0, 1],
        [1, 0],
        [1, 1],
        [1, 2],
        [2, 1],
      ],
    ];
    const count = Math.max(4, Math.floor((rows * cols) / 110));

    for (let i = 0; i < count; i += 1) {
      const pattern = patterns[i % patterns.length];
      const row = Math.floor(random(1, Math.max(2, rows - 3)));
      const col = Math.floor(random(1, Math.max(2, cols - 3)));
      for (const [dr, dc] of pattern) {
        setLife(row + dr, col + dc, true);
      }
    }
  }

  function buildField() {
    cellSize = width < 560 ? 42 : width < 900 ? 46 : 52;
    rows = Math.ceil(height / cellSize) + 4;
    cols = Math.ceil(width / cellSize) + 4;
    scrollOffset = random(0, rows * cellSize);

    const total = rows * cols;
    state = Array.from({ length: total }, (_, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      const current = Math.sin(row * 0.87 + col * 1.36) > 0.82;
      return Math.random() > 0.82 || (current && Math.random() > 0.4);
    });
    nextState = new Array(total).fill(false);
    seedLifePatterns();

    cells = Array.from({ length: total }, (_, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      const cell = {
        row,
        col,
        x: 0,
        y: 0,
        vx: random(-1.2, 1.2),
        vy: random(-1.2, 1.2),
        form: Math.floor(random(0, 3)),
        phase: random(0, TAU),
        angle: random(-0.55, 0.55),
        spin: random(-0.16, 0.16),
        speed: random(0.75, 1.28),
        cluster: random(0, TAU),
        jitterX: random(-cellSize * 0.31, cellSize * 0.31),
        jitterY: random(-cellSize * 0.27, cellSize * 0.27),
        wander: random(cellSize * 0.08, cellSize * 0.24),
        energy: state[i] ? random(0.55, 1) : random(0, 0.08),
        cursor: 0,
      };
      const point = basePosition(cell, performance.now());
      cell.x = point.x;
      cell.y = point.y;
      return cell;
    });
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
    buildField();
    restartAnimation();
  }

  function cursorInfluence(x, y, radius) {
    if (!mouse.active) return { amount: 0, dx: 0, dy: 0, dist: radius };
    const dx = x - mouse.x;
    const dy = y - mouse.y;
    const dist = Math.max(1, Math.hypot(dx, dy));
    const amount = clamp(1 - dist / radius, 0, 1);
    return { amount, dx, dy, dist };
  }

  function neighborBias(row, col) {
    let x = 0;
    let y = 0;
    let count = 0;
    const dirs = [
      [-1, 0],
      [1, 0],
      [0, -1],
      [0, 1],
      [-1, -1],
      [-1, 1],
      [1, -1],
      [1, 1],
    ];

    for (const [dr, dc] of dirs) {
      if (!lifeAt(row + dr, col + dc)) continue;
      x += dc;
      y += dr;
      count += 1;
    }

    return { x, y, count };
  }

  function stepLife(now) {
    const interval = reducedMotion ? 280 : 180;
    if (now - lastLifeStep < interval) return;
    lastLifeStep = now;

    let aliveCount = 0;
    for (let row = 0; row < rows; row += 1) {
      for (let col = 0; col < cols; col += 1) {
        let neighbors = 0;
        for (let dr = -1; dr <= 1; dr += 1) {
          for (let dc = -1; dc <= 1; dc += 1) {
            if (dr === 0 && dc === 0) continue;
            if (lifeAt(row + dr, col + dc)) neighbors += 1;
          }
        }

        const i = index(row, col);
        const alive = state[i];
        let nextAlive = alive
          ? neighbors === 2 || neighbors === 3
          : neighbors === 3;

        if (!nextAlive && Math.random() < 0.0045) nextAlive = true;
        if (nextAlive && Math.random() < 0.0015) nextAlive = false;

        nextState[i] = nextAlive;
        if (nextAlive) aliveCount += 1;
      }
    }

    for (let i = 0; i < state.length; i += 1) {
      const born = nextState[i] && !state[i];
      state[i] = nextState[i];
      if (born) {
        cells[i].form = (cells[i].form + 1 + Math.floor(random(0, 2))) % 3;
        cells[i].energy = Math.max(cells[i].energy, 1);
      }
    }

    if (aliveCount < state.length * 0.07) {
      seedLifePatterns();
    } else if (aliveCount > state.length * 0.38) {
      for (let i = 0; i < state.length; i += 1) {
        if (state[i] && Math.random() < 0.18) state[i] = false;
      }
    }
  }

  function updateCells(now, dt) {
    const scale = motionScale();
    scrollOffset = (scrollOffset + 34 * dt * scale) % (rows * cellSize);
    activeIndices = [];

    for (let i = 0; i < cells.length; i += 1) {
      const cell = cells[i];
      const alive = state[i];
      const bias = neighborBias(cell.row, cell.col);
      const base = basePosition(cell, now);
      const pulse = Math.sin(now * 0.0022 * cell.speed + cell.phase);
      const targetX = base.x + bias.x * (2.4 + pulse * 1.5) * scale;
      const targetY = base.y + bias.y * (2.1 - pulse * 1.1) * scale;

      if (
        Math.abs(cell.y - targetY) > height * 0.58 ||
        Math.abs(cell.x - targetX) > width * 0.65
      ) {
        cell.x = targetX;
        cell.y = targetY;
        cell.vx = 0;
        cell.vy = 0;
      }

      const influence = cursorInfluence(cell.x, cell.y, 218);
      const push = influence.amount * influence.amount;
      cell.cursor += (influence.amount - cell.cursor) * clamp(dt * 18, 0, 1);

      const spring = (alive ? 11 : 7.2) + bias.count * 0.38;
      cell.vx += (targetX - cell.x) * spring * dt;
      cell.vy += (targetY - cell.y) * spring * dt;
      cell.vx += (influence.dx / influence.dist) * push * 2450 * dt;
      cell.vy += (influence.dy / influence.dist) * push * 2450 * dt;
      cell.vx += (-influence.dy / influence.dist) * push * 180 * dt;
      cell.vy += (influence.dx / influence.dist) * push * 180 * dt;

      const damping = Math.pow(0.018, dt);
      cell.vx *= damping;
      cell.vy *= damping;
      cell.x += cell.vx * dt;
      cell.y += cell.vy * dt;

      const energyTarget = alive ? 1 : 0;
      const energyRate = (alive ? 7.5 : 2.5) * Math.max(0.001, dt);
      cell.energy += (energyTarget - cell.energy) * clamp(energyRate, 0, 1);

      const level = Math.max(cell.energy, cell.cursor * 0.92);
      if (level > 0.055 && isVisible(cell, 42)) {
        activeIndices.push(i);
      }
    }
  }

  function drawBioGlyph(cell, i, now) {
    const alive = state[i];
    const level = Math.max(cell.energy, cell.cursor * 0.92);
    if (level < 0.055) return;

    const size = clamp(cellSize * (0.15 + level * 0.12), 7, 14);
    const t = now * 0.0018 * cell.speed + cell.phase;
    const main = alive ? colors.green : colors.cyan;
    const faint = clamp(0.11 + level * 0.52, 0, 0.82);

    ctx.save();
    ctx.translate(cell.x, cell.y);
    ctx.rotate(cell.angle + Math.sin(t) * 0.12 + cell.spin * level);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = 0.75 + level * 0.9;

    ctx.strokeStyle = rgba(main, faint);
    ctx.fillStyle = rgba(main, 0.025 + level * 0.055);
    ctx.beginPath();
    ctx.ellipse(0, 0, size * 1.08, size * 0.62, Math.sin(t) * 0.16, 0, TAU);
    ctx.fill();
    ctx.stroke();

    ctx.strokeStyle = rgba(colors.white, 0.07 + level * 0.18);
    ctx.beginPath();
    ctx.ellipse(
      0,
      0,
      size * 0.68,
      size * 0.31,
      Math.cos(t * 0.8) * 0.42,
      0,
      TAU
    );
    ctx.stroke();

    ctx.strokeStyle = rgba(main, 0.12 + level * 0.34);
    ctx.beginPath();
    ctx.arc(0, 0, size * 0.84, t, t + 1.25);
    ctx.arc(0, 0, size * 0.84, t + Math.PI, t + Math.PI + 1.25);
    ctx.stroke();

    const podCount = 2 + cell.form;
    ctx.strokeStyle = rgba(main, 0.14 + level * 0.3);
    ctx.fillStyle = rgba(colors.white, 0.08 + level * 0.22);
    for (let pod = 0; pod < podCount; pod += 1) {
      const angle = t * 0.34 + (pod / podCount) * TAU + cell.form * 0.42;
      const x1 = Math.cos(angle) * size * 0.68;
      const y1 = Math.sin(angle) * size * 0.42;
      const x2 = Math.cos(angle) * size * (0.9 + cell.form * 0.04);
      const y2 = Math.sin(angle) * size * 0.58;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x2, y2, size * 0.08, 0, TAU);
      ctx.fill();
    }

    ctx.fillStyle = rgba(colors.amber, 0.18 + level * 0.36);
    ctx.beginPath();
    ctx.arc(
      Math.cos(t * 0.92) * size * 0.2,
      Math.sin(t * 0.7) * size * 0.15,
      size * (0.11 + level * 0.03),
      0,
      TAU
    );
    ctx.fill();

    ctx.restore();
  }

  function drawConnections() {
    const offsets = [
      [0, 1],
      [1, 0],
      [1, 1],
      [1, -1],
    ];
    const maxDist = cellSize * 1.9;

    ctx.save();
    ctx.lineWidth = 0.85;
    ctx.lineCap = "round";

    for (const i of activeIndices) {
      const row = Math.floor(i / cols);
      const col = i % cols;
      const a = cells[i];
      const aLevel = Math.max(a.energy, a.cursor * 0.82);

      for (const [dr, dc] of offsets) {
        const nextRow = row + dr;
        const nextCol = col + dc;
        if (nextRow < 0 || nextRow >= rows || nextCol < 0 || nextCol >= cols) {
          continue;
        }

        const j = index(nextRow, nextCol);
        const b = cells[j];
        const bLevel = Math.max(b.energy, b.cursor * 0.82);
        if (bLevel < 0.055 || !isVisible(b, 42)) continue;

        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (dist < 5 || dist > maxDist) continue;

        const livePair = state[i] && state[j] ? 1 : 0.55;
        const alpha = clamp(
          (1 - dist / maxDist) *
            (0.08 + (aLevel + bLevel) * 0.16 + (a.cursor + b.cursor) * 0.1) *
            livePair,
          0,
          0.34
        );
        if (alpha <= 0.01) continue;

        ctx.strokeStyle = rgba(state[i] && state[j] ? colors.green : colors.cyan, alpha);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    for (const i of activeIndices) {
      const cell = cells[i];
      const level = Math.max(cell.energy, cell.cursor * 0.7);
      ctx.fillStyle = rgba(
        colors.white,
        clamp(0.05 + level * 0.2 + cell.cursor * 0.16, 0, 0.42)
      );
      ctx.beginPath();
      ctx.arc(cell.x, cell.y, 1 + level * 1.4 + cell.cursor * 1.2, 0, TAU);
      ctx.fill();
    }

    ctx.restore();
  }

  function drawMouseField() {
    if (!mouse.active) return;
    ctx.save();
    ctx.lineWidth = 1;
    ctx.strokeStyle = rgba(colors.green, 0.24);
    ctx.beginPath();
    ctx.arc(mouse.x, mouse.y, 36, 0, TAU);
    ctx.stroke();
    ctx.strokeStyle = rgba(colors.cyan, 0.14);
    ctx.beginPath();
    ctx.arc(mouse.x, mouse.y, 90, 0, TAU);
    ctx.stroke();
    ctx.restore();
  }

  function paint(now) {
    ctx.clearRect(0, 0, width, height);
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    drawConnections();
    for (const i of activeIndices) {
      drawBioGlyph(cells[i], i, now);
    }
    drawMouseField();
    ctx.restore();
  }

  function render(now, dt) {
    stepLife(now);
    updateCells(now, dt);
    paint(now);
  }

  function animate(now) {
    const dt = Math.min(0.04, Math.max(0.001, (now - lastTime) / 1000 || 0.016));
    lastTime = now;
    render(now, dt);
    animationId = requestAnimationFrame(animate);
  }

  function restartAnimation() {
    if (animationId) {
      cancelAnimationFrame(animationId);
      animationId = 0;
    }
    lastTime = performance.now();
    render(lastTime, 0.016);
    if (!document.hidden) {
      animationId = requestAnimationFrame(animate);
    }
  }

  function updateMouse(event) {
    const rect = hero.getBoundingClientRect();
    mouse.x = event.clientX - rect.left;
    mouse.y = event.clientY - rect.top;
    mouse.active = true;
  }

  hero.addEventListener("pointermove", updateMouse, { passive: true });
  hero.addEventListener("pointerenter", updateMouse, { passive: true });
  hero.addEventListener("pointerleave", () => {
    mouse.active = false;
  });

  if (typeof ResizeObserver !== "undefined") {
    const observer = new ResizeObserver(resizeCanvas);
    observer.observe(hero);
  } else {
    window.addEventListener("resize", resizeCanvas);
  }

  function handleMotionPreference(event) {
    reducedMotion = event.matches;
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

  resizeCanvas();
})();
