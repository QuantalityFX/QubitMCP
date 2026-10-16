/* Masonry keeps natural card heights and supports wider feature cards. */
document.querySelectorAll(".latest-grid").forEach((container) => {
  if (!("ResizeObserver" in window)) return;
  const cards = Array.from(container.children).filter((card) => card.classList.contains("card"));
  let pending = false;
  let measuredWidth = 0;
  function scheduleLayout() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => {
      pending = false;
      const style = getComputedStyle(container);
      const columns = parseInt(style.getPropertyValue("--masonry-columns"), 10) || 1;
      const gap = parseFloat(style.columnGap) || 16;
      measuredWidth = container.clientWidth;
      const columnWidth = (measuredWidth - gap * (columns - 1)) / columns;
      const spans = cards.map((card) => card.classList.contains("card-wide") ? Math.min(2, columns) : 1);
      cards.forEach((card, index) => {
        card.style.height = "";
        card.classList.remove("masonry-fill-gap");
        card.style.width = `${columnWidth * spans[index] + gap * (spans[index] - 1)}px`;
      });
      const placed = [];
      cards.forEach((card, index) => {
        const width = card.getBoundingClientRect().width;
        const height = card.getBoundingClientRect().height;
        let best = { left: 0, top: Infinity };
        for (let column = 0; column <= columns - spans[index]; column++) {
          const left = column * (columnWidth + gap);
          const neighbors = placed.filter((other) => left < other.left + other.width + gap - 0.1
            && left + width + gap > other.left + 0.1).sort((a, b) => a.top - b.top);
          let top = 0;
          for (const other of neighbors) {
            if (top + height + gap <= other.top + 0.1) break;
            top = Math.max(top, other.top + other.height + gap);
          }
          if (top < best.top) best = { left, top };
        }
        card.style.left = `${best.left}px`;
        card.style.top = `${best.top}px`;
        placed.push({ ...best, width, height, span: spans[index], element: card });
      });
      // When a wide card clears a taller neighbor, extend just the shorter
      // card's bottom into that pocket. Its top and content stay in place.
      placed.forEach((card) => {
        const below = placed.filter((other) => other !== card && other.top >= card.top + card.height + gap - 0.1
          && Math.min(card.left + card.width, other.left + other.width) > Math.max(card.left, other.left) + 0.1)
          .sort((a, b) => a.top - b.top)[0];
        if (!below || below.span === 1) return;
        const height = below.top - card.top - gap;
        if (height <= card.height + 0.1) return;
        card.height = height;
        card.element.classList.add("masonry-fill-gap");
        card.element.style.height = `${height}px`;
      });
      container.style.height = `${Math.max(0, ...placed.map((card) => card.top + card.height))}px`;
    });
  }
  container.classList.add("is-masonry");
  const observer = new ResizeObserver((entries) => {
    if (entries.some((entry) => entry.target !== container || Math.abs(entry.contentRect.width - measuredWidth) > 0.5)) {
      scheduleLayout();
    }
  });
  observer.observe(container);
  cards.forEach((card) => observer.observe(card));
  scheduleLayout();
});

/* Mobile navigation is enhanced only when JavaScript is available. */
document.querySelectorAll(".nav-toggle").forEach((button) => {
  const sidebar = button.closest(".sidebar");
  const nav = document.getElementById(button.getAttribute("aria-controls"));
  if (!sidebar || !nav) return;
  button.hidden = false;
  sidebar.classList.add("has-nav-toggle");
  function closeNavigation() {
    button.setAttribute("aria-expanded", "false");
    sidebar.classList.remove("nav-open");
  }
  button.addEventListener("click", () => {
    const expanded = button.getAttribute("aria-expanded") !== "true";
    button.setAttribute("aria-expanded", String(expanded));
    sidebar.classList.toggle("nav-open", expanded);
  });
  nav.addEventListener("click", (event) => {
    if (!event.target.closest("a")) return;
    if (window.matchMedia("(max-width: 960px)").matches) button.focus();
    closeNavigation();
  });
  sidebar.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeNavigation();
      button.focus();
    }
  });
});

/* Keep local section links in sync; cross-page links stay ordinary links. */
const links = Array.from(document.querySelectorAll(".nav a"));
const pairs = links
  .map((link) => {
    const href = link.getAttribute("href") || "";
    const target = href.startsWith("#") ? document.getElementById(href.slice(1)) : null;
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

if (location.hash && document.getElementById(location.hash.slice(1))) {
  setActive(location.hash);
} else {
  updateActiveFromScroll();
}
