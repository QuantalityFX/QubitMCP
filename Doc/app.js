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
