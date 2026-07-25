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

/* Living neural-splat field behind the landing hero. */
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
  const neighborOffsets = [
    [-1, 0],
    [1, 0],
    [0, -1],
    [0, 1],
    [-1, -1],
    [-1, 1],
    [1, -1],
    [1, 1],
  ];

  let width = 0;
  let height = 0;
  let dpr = 1;
  let cellSize = 56;
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
    const colShift = Math.cos(cell.col * 1.07 + cell.cluster) * cellSize * 0.11;
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
      colShift +
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
    cellSize = width < 560 ? 46 : width < 900 ? 52 : 60;
    rows = Math.ceil(height / cellSize) + 4;
    cols = Math.ceil(width / cellSize) + 4;
    scrollOffset = random(0, rows * cellSize);

    const total = rows * cols;
    state = Array.from({ length: total }, (_, i) => {
      const row = Math.floor(i / cols);
      const col = i % cols;
      const current = Math.sin(row * 0.87 + col * 1.36) > 0.86;
      return Math.random() > 0.86 || (current && Math.random() > 0.55);
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
        energy: state[i] ? random(0.18, 0.72) : random(0, 0.04),
        birth: state[i] ? random(0.45, 1) : 0,
        charge: random(-1, 1),
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

    for (const [dr, dc] of neighborOffsets) {
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
        cells[i].energy = Math.max(cells[i].energy, 0.08);
        cells[i].birth = Math.max(cells[i].birth, 0.04);
        cells[i].angle = random(-0.55, 0.55);
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

  function applyNeighborForces(cell, i, dt) {
    const row = cell.row;
    const col = cell.col;
    const alive = state[i];

    for (const [dr, dc] of neighborOffsets) {
      const nextRow = row + dr;
      const nextCol = col + dc;
      if (nextRow < 0 || nextRow >= rows || nextCol < 0 || nextCol >= cols) {
        continue;
      }

      const j = index(nextRow, nextCol);
      const other = cells[j];
      const otherLevel = Math.max(other.energy, other.cursor * 0.72);
      if (otherLevel < 0.035) continue;

      const dx = cell.x - other.x;
      const dy = cell.y - other.y;
      const dist = Math.max(1, Math.hypot(dx, dy));
      const linked = alive && state[j];
      const desired = cellSize * (linked ? 0.84 : 1.12);
      const pressure = clamp((desired - dist) / desired, -0.95, 1.35);
      const force = pressure * (linked ? 92 : 42) * otherLevel;
      const tangent = linked ? Math.sin(lastTime * 0.004 + i * 0.17 + j * 0.09) * 18 : 0;
      const nx = dx / dist;
      const ny = dy / dist;

      cell.vx += (nx * force - ny * tangent) * dt;
      cell.vy += (ny * force + nx * tangent) * dt;
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
      applyNeighborForces(cell, i, dt);
      cell.vx += (influence.dx / influence.dist) * push * 2450 * dt;
      cell.vy += (influence.dy / influence.dist) * push * 2450 * dt;
      cell.vx += (-influence.dy / influence.dist) * push * 180 * dt;
      cell.vy += (influence.dx / influence.dist) * push * 180 * dt;

      const damping = Math.pow(0.018, dt);
      cell.vx *= damping;
      cell.vy *= damping;
      cell.x += cell.vx * dt;
      cell.y += cell.vy * dt;

      const birthTarget = alive ? 1 : 0;
      const birthRate = (alive ? 2.9 : 1.55) * Math.max(0.001, dt);
      cell.birth += (birthTarget - cell.birth) * clamp(birthRate, 0, 1);

      const energyTarget = alive ? clamp(0.5 + bias.count * 0.075, 0, 1) : 0;
      const energyRate = (alive ? 4.2 : 1.65) * Math.max(0.001, dt);
      cell.energy += (energyTarget - cell.energy) * clamp(energyRate, 0, 1);

      const level = Math.max(cell.energy, cell.cursor * 0.92);
      if (level > 0.025 && isVisible(cell, 42)) {
        activeIndices.push(i);
      }
    }
  }

  function drawSplatGlyph(cell, i, now) {
    const alive = state[i];
    const liveLevel = cell.energy * clamp(cell.birth, 0, 1);
    const level = Math.max(liveLevel, cell.cursor * 0.92);
    if (level < 0.025) return;

    const t = now * 0.0019 * cell.speed + cell.phase;
    const speed = Math.hypot(cell.vx, cell.vy);
    const velocityAngle = speed > 4 ? Math.atan2(cell.vy, cell.vx) : cell.angle;
    const cursorAngle = Math.atan2(cell.y - mouse.y, cell.x - mouse.x);
    const angle = mouse.active && cell.cursor > 0.03
      ? velocityAngle * (1 - cell.cursor) + cursorAngle * cell.cursor
      : velocityAngle;
    const size = clamp(cellSize * (0.095 + level * 0.13), 5, 15);
    const stretch = 1 + clamp(speed / 72, 0, 0.48) + cell.cursor * 1.05;
    const squash = clamp(0.52 + liveLevel * 0.24 - cell.cursor * 0.1, 0.38, 0.86);
    const main = alive ? colors.green : colors.cyan;
    const outerAlpha = clamp(0.035 + level * 0.12, 0, 0.22);
    const coreAlpha = clamp(0.12 + level * 0.38, 0, 0.62);

    ctx.save();
    ctx.translate(cell.x, cell.y);
    ctx.rotate(angle + Math.sin(t) * 0.08 + cell.spin * level);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = 0.7 + level * 0.55;

    ctx.fillStyle = rgba(main, outerAlpha);
    ctx.beginPath();
    ctx.ellipse(0, 0, size * 1.85 * stretch, size * 1.05 * squash, 0, 0, TAU);
    ctx.fill();

    ctx.fillStyle = rgba(main, outerAlpha * 1.45);
    ctx.beginPath();
    ctx.ellipse(0, 0, size * 1.16 * stretch, size * 0.62 * squash, 0, 0, TAU);
    ctx.fill();

    ctx.fillStyle = rgba(alive ? colors.white : colors.cyan, coreAlpha);
    ctx.beginPath();
    ctx.ellipse(0, 0, size * 0.43 * stretch, size * 0.27 * squash, 0, 0, TAU);
    ctx.fill();

    const pixelCount = 4 + cell.form;
    for (let pixel = 0; pixel < pixelCount; pixel += 1) {
      const a = t * (0.21 + pixel * 0.03) + (pixel / pixelCount) * TAU;
      const scatter = size * (0.66 + pixel * 0.1 + cell.cursor * 0.78);
      const px = Math.cos(a) * scatter * stretch;
      const py = Math.sin(a + cell.charge) * scatter * squash;
      const side = clamp(size * (0.12 + level * 0.08), 1.6, 3.2);
      const alpha = clamp(0.08 + level * 0.22 + cell.cursor * 0.18, 0, 0.48);

      ctx.fillStyle = rgba(pixel % 3 === 0 ? colors.amber : main, alpha);
      ctx.fillRect(px - side * 0.5, py - side * 0.5, side, side);
    }

    ctx.strokeStyle = rgba(main, clamp(0.06 + level * 0.18, 0, 0.32));
    ctx.beginPath();
    ctx.moveTo(-size * 1.25 * stretch, 0);
    ctx.lineTo(size * 1.25 * stretch, 0);
    ctx.stroke();

    ctx.restore();
  }

  function drawConnections(now) {
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

        if (state[i] || state[j] || a.cursor + b.cursor > 0.2) {
          const pulse = (Math.sin(now * 0.004 + i * 0.31 + j * 0.17) + 1) * 0.5;
          const px = a.x + (b.x - a.x) * pulse;
          const py = a.y + (b.y - a.y) * pulse;
          ctx.fillStyle = rgba(colors.amber, alpha * 0.75);
          ctx.beginPath();
          ctx.arc(px, py, 1.1 + (aLevel + bLevel) * 0.55, 0, TAU);
          ctx.fill();
        }
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
    drawConnections(now);
    for (const i of activeIndices) {
      drawSplatGlyph(cells[i], i, now);
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
