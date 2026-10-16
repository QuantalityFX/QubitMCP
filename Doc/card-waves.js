/* Seeded slab sampler adapted from tools/WebElements/Qubit_Field_True_Seeded_Original_Style.
   Each card keeps its own stable slice; gradients remain fixed while the SVG drifts. */
(function initCardWaves() {
  "use strict";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const REFERENCE_SEED = 1142;
  const BRIGHTNESS_KEY = "qubit-doc-card-brightness";
  const brightnessInput = document.getElementById("card-header-brightness");
  const brightnessOutput = document.getElementById("card-header-brightness-value");

  function applyBrightness(value) {
    const number = Number(value);
    const level = value !== null && value !== "" && Number.isFinite(number)
      ? Math.round(clamp(number, 0, 100)) : 50;
    document.documentElement.style.setProperty("--card-header-brightness", String(level / 100));
    if (brightnessInput) {
      brightnessInput.value = String(level);
      brightnessInput.style.setProperty("--slider-fill", `${level}%`);
      brightnessInput.setAttribute("aria-valuetext", `${level}%`);
    }
    if (brightnessOutput) brightnessOutput.value = `${level}%`;
    return level;
  }

  let savedBrightness = null;
  try { savedBrightness = localStorage.getItem(BRIGHTNESS_KEY); } catch (_) { /* Local files may restrict storage. */ }
  applyBrightness(savedBrightness);
  if (brightnessInput) {
    brightnessInput.closest(".hero-brightness-control").hidden = false;
    brightnessInput.addEventListener("input", () => applyBrightness(brightnessInput.value));
    brightnessInput.addEventListener("change", () => {
      const level = applyBrightness(brightnessInput.value);
      try { localStorage.setItem(BRIGHTNESS_KEY, String(level)); } catch (_) { /* The slider also works without storage. */ }
    });
  }

  const headers = Array.from(document.querySelectorAll(".card > h3:first-child"));
  if (!headers.length) return;
  const cards = new Map(headers.map((header, index) => [header, { header, index, svg: null }]));

  function integer(value, fallback) {
    const number = Number(value);
    return value !== undefined && Number.isFinite(number) ? Math.floor(number) : fallback;
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function smooth(t) { return t * t * (3 - 2 * t); }

  function lattice(x, y, seed) {
    let hash = Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(seed, 1442695041);
    hash = Math.imul(hash ^ (hash >>> 13), 1274126177);
    return ((hash ^ (hash >>> 16)) >>> 0) / 4294967295 * 2 - 1;
  }

  function noise2D(seed, x, y) {
    const ix = Math.floor(x), iy = Math.floor(y);
    const u = smooth(x - ix), v = smooth(y - iy);
    const a = lattice(ix, iy, seed);
    const b = lattice(ix + 1, iy, seed);
    const c = lattice(ix, iy + 1, seed);
    const d = lattice(ix + 1, iy + 1, seed);
    return (a + (b - a) * u) * (1 - v) + (c + (d - c) * u) * v;
  }

  function slabField(seed, x, y) {
    return 0.72 * noise2D(seed, x / 330, y / 420)
      + 0.28 * noise2D(seed + 981, x / 740, y / 940);
  }

  function smoothCurves(seed, offset) {
    const v = (x) => slabField(seed, x, offset) - slabField(REFERENCE_SEED, x, 0);
    const fixed = (number) => number.toFixed(2);
    const leftStartY = clamp(91 + 57 * v(35), 42, 142);
    const leftC1X = clamp(240 + 115 * v(220), 116, 345);
    const leftC1Y = clamp(76 + 96 * v(375), 28, 133);
    const leftC2X = clamp(328 + 108 * v(480), leftC1X + 48, 485);
    const leftC2Y = clamp(218 + 70 * v(610), 173, 250);
    const leftEndX = clamp(630 + 105 * v(750), leftC2X + 65, 734);
    const rightStartX = clamp(660 + 112 * v(915), Math.max(635, leftEndX + 29), 860);
    const rightC1X = clamp(950 + 106 * v(1065), rightStartX + 92, 1075);
    const rightC1Y = clamp(210 + 90 * v(1210), 159, 232);
    const rightC2X = clamp(1220 + 98 * v(1415), rightC1X + 85, 1330);
    const rightC2Y = clamp(141 + 113 * v(1605), 62, 184);
    const rightEndY = clamp(-22 + 88 * v(1790), -64, 80);
    return {
      left: `M -20 ${fixed(leftStartY)} C ${fixed(leftC1X)} ${fixed(leftC1Y)}, ${fixed(leftC2X)} ${fixed(leftC2Y)}, ${fixed(leftEndX)} 234`,
      right: `M ${fixed(rightStartX)} 234 C ${fixed(rightC1X)} ${fixed(rightC1Y)}, ${fixed(rightC2X)} ${fixed(rightC2Y)}, 1435 ${fixed(rightEndY)}`,
    };
  }

  function markup(uid, { left, right }) {
    return `<defs>
      <linearGradient id="${uid}-left" x1="0" y1="0" x2="1" y2="1">
        <stop stop-color="#03a8bc" stop-opacity=".35"/><stop offset=".4" stop-color="#00c2c1" stop-opacity=".19"/><stop offset="1" stop-color="#006a8e" stop-opacity=".015"/>
      </linearGradient>
      <linearGradient id="${uid}-leftEdge" x1="0" y1="0" x2="1" y2="0">
        <stop stop-color="#05b5c0" stop-opacity=".12"/><stop offset=".27" stop-color="#0bfee0" stop-opacity=".83"/><stop offset=".65" stop-color="#05a8ee" stop-opacity=".62"/><stop offset="1" stop-color="#05677d" stop-opacity=".03"/>
      </linearGradient>
      <linearGradient id="${uid}-right" x1="0" y1="1" x2="1" y2="0">
        <stop stop-color="#006186" stop-opacity=".045"/><stop offset=".5" stop-color="#00a5ad" stop-opacity=".15"/><stop offset="1" stop-color="#01b5a0" stop-opacity=".24"/>
      </linearGradient>
      <linearGradient id="${uid}-rightEdge" x1="0" y1="0" x2="1" y2="0">
        <stop stop-color="#00617d" stop-opacity=".02"/><stop offset=".53" stop-color="#07c5d7" stop-opacity=".5"/><stop offset=".75" stop-color="#1effdd" stop-opacity=".93"/><stop offset="1" stop-color="#00b5a6" stop-opacity=".12"/>
      </linearGradient>
    </defs>
    <path d="${left} L -20 234 Z" fill="url(#${uid}-left)"/>
    <path d="${left}" fill="none" stroke="url(#${uid}-leftEdge)" stroke-width="1.7"/>
    <path d="${right} L 1435 234 Z" fill="url(#${uid}-right)"/>
    <path d="${right}" fill="none" stroke="url(#${uid}-rightEdge)" stroke-width="1.7"/>`;
  }

  function draw(card) {
    const { header, index } = card;
    const source = header.closest("[data-slab-seed]") || document.body;
    const seed = integer(source.dataset.slabSeed, REFERENCE_SEED);
    // Stable cuts do not shift when catalog filtering, fonts, or viewport sizes change.
    const offset = integer(header.dataset.slabOffset, index * 900);
    const pan = integer(header.dataset.slabPan ?? source.dataset.slabPan, 36);
    if (!card.svg) {
      card.svg = document.createElementNS(SVG_NS, "svg");
      card.svg.classList.add("card-header-waves");
      card.svg.setAttribute("viewBox", "0 0 1400 230");
      card.svg.setAttribute("preserveAspectRatio", "none");
      card.svg.setAttribute("aria-hidden", "true");
      card.svg.setAttribute("focusable", "false");
      header.prepend(card.svg);
    }
    const signature = `${seed}:${offset}:${pan}`;
    if (card.signature === signature) return;
    card.svg.innerHTML = markup(`qf-card-wave-${index}`, smoothCurves(seed, offset));
    card.svg.style.setProperty("--card-wave-pan", `${Math.max(10, pan)}s`);
    card.svg.style.animation = pan > 0 ? "" : "none";
    card.signature = signature;
  }

  function refresh() {
    cards.forEach((card) => { if (card.svg) draw(card); });
  }

  // Only visible headers need SVGs or animation; the catalog can contain hundreds.
  if (typeof IntersectionObserver !== "undefined") {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) draw(cards.get(entry.target));
        entry.target.classList.toggle("card-header-wave-visible", entry.isIntersecting);
      });
    }, { rootMargin: "80px 0px", threshold: 0 });
    headers.forEach((header) => observer.observe(header));
  } else {
    cards.forEach(draw);
  }

  function updateVisibility() {
    document.documentElement.classList.toggle("card-waves-suspended", document.hidden);
  }
  document.addEventListener("visibilitychange", updateVisibility);
  updateVisibility();

  // Same seed controls as the supplied example, without adding demo UI to the docs.
  window.QubitFieldWaves = {
    refresh,
    setSeed(seed, selector = "body") {
      document.querySelectorAll(selector).forEach((source) => {
        source.dataset.slabSeed = String(integer(seed, REFERENCE_SEED));
      });
      refresh();
    },
  };
})();
