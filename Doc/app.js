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
