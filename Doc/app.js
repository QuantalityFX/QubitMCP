/* Highlight active section in the sidebar */
const links = Array.from(document.querySelectorAll(".nav a"));
const sections = links
  .map((l) => document.querySelector(l.getAttribute("href")))
  .filter(Boolean);

const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const id = `#${entry.target.id}`;
      links.forEach((l) => l.classList.toggle("active", l.getAttribute("href") === id));
    });
  },
  { rootMargin: "-20% 0px -60% 0px", threshold: 0.1 }
);

sections.forEach((s) => observer.observe(s));
