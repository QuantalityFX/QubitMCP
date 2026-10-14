/* Static HTML remains the catalog source. Search also works when opened from disk. */
(() => {
  const search = document.getElementById("node-search");
  const category = document.getElementById("node-category");
  const status = document.querySelector("[data-catalog-status]");
  if (!search || !category || !status) return;

  const sections = Array.from(document.querySelectorAll(".catalog-section"));
  const normalize = (value) => value.toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  const entries = Array.from(document.querySelectorAll(".card[id^='node-']"), (card) => ({
    card,
    slug: card.id.slice(5),
    category: card.closest(".catalog-section").dataset.category,
    title: card.querySelector("h3").textContent.trim(),
    text: normalize(card.textContent),
    reference: card.querySelector(".node-reference"),
  }));
  const showAll = document.querySelector("[data-show-all]");
  const noResults = document.querySelector("[data-no-results]");
  const aliases = {
    "directional-light": "light", "point-light": "light",
    "spot-light": "light", "area-light": "light",
    "fbx-animation-import": "fbx-animation", "volume-selector": "split-volume",
    "image-gs": "image-gs-splat", "gen-x-videomocap": "genx-video-mocap",
  };
  let selected = "";
  const pageTitle = document.title;
  const heading = document.querySelector("#main-content h1");

  entries.forEach(({ card, slug, title }) => {
    const link = document.createElement("a");
    link.className = "node-permalink";
    link.href = `nodes.html?node=${encodeURIComponent(slug)}#node-${slug}`;
    link.dataset.node = slug;
    link.textContent = "Open full node reference";
    link.setAttribute("aria-label", `Open full reference for ${title}`);
    card.append(link);
  });

  function applyFilters() {
    const terms = normalize(search.value).split(" ").filter(Boolean);
    const knownSelection = entries.some((entry) => entry.slug === selected);
    let visible = 0;
    entries.forEach((entry) => {
      const matches = selected && knownSelection
        ? entry.slug === selected
        : (!category.value || entry.category === category.value) && terms.every((term) => entry.text.includes(term));
      entry.card.hidden = !matches;
      if (entry.reference) entry.reference.open = Boolean(selected && knownSelection && matches);
      if (matches) visible += 1;
    });
    sections.forEach((section) => {
      section.hidden = !entries.some((entry) => !entry.card.hidden && section.contains(entry.card));
    });
    document.querySelectorAll(".node-nav [data-node]").forEach((link) => {
      const active = knownSelection && link.dataset.node === selected;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "true");
      else link.removeAttribute("aria-current");
    });
    const entry = entries.find((item) => item.slug === selected);
    document.getElementById("main-content").classList.toggle("catalog-detail-view", Boolean(entry));
    document.title = entry ? `${entry.title} | Qubit Field Node Reference` : pageTitle;
    if (heading) heading.textContent = entry ? `${entry.title} reference` : "Node Catalog";
    status.textContent = entry ? `Showing ${entry.title}. Search or choose another node to continue.`
      : `${selected ? "That node was not found. " : ""}${visible} of ${entries.length} nodes${category.value ? ` in ${category.value}` : ""}${search.value.trim() ? ` matching “${search.value.trim()}”` : ""}.`;
    showAll.hidden = !selected && !category.value && !search.value;
    noResults.hidden = visible > 0;
  }

  function readLocation() {
    const params = new URLSearchParams(location.search);
    const raw = (params.get("node") || (location.hash.startsWith("#node-") ? location.hash.slice(6) : "")).toLowerCase().replace(/_/g, "-");
    selected = aliases[raw] || raw;
    search.value = params.get("q") || "";
    category.value = params.get("category") || "";
    applyFilters();
  }

  function writeLocation(push = false) {
    const url = new URL(location.href);
    ["node", "q", "category"].forEach((key) => url.searchParams.delete(key));
    if (selected) url.searchParams.set("node", selected);
    else {
      if (search.value.trim()) url.searchParams.set("q", search.value.trim());
      if (category.value) url.searchParams.set("category", category.value);
    }
    url.hash = "";
    // File URLs support filtering even in browsers that restrict history changes.
    try { history[push ? "pushState" : "replaceState"](null, "", url); } catch (_) { /* Keep local filters usable. */ }
    applyFilters();
  }

  function clearFilters(event) {
    if (event) event.preventDefault();
    selected = "";
    search.value = "";
    category.value = "";
    writeLocation(true);
    search.focus();
  }
  search.addEventListener("input", () => { selected = ""; writeLocation(); });
  category.addEventListener("change", () => { selected = ""; writeLocation(true); });
  document.querySelector("[data-clear-filters]").addEventListener("click", clearFilters);
  showAll.addEventListener("click", clearFilters);
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[data-node]");
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    selected = link.dataset.node;
    search.value = "";
    category.value = "";
    writeLocation(true);
    document.getElementById("main-content").focus({ preventScroll: true });
    document.getElementById("main-content").scrollIntoView();
  });
  window.addEventListener("popstate", readLocation);
  window.addEventListener("hashchange", readLocation);
  document.querySelector("[data-catalog-controls]").hidden = false;
  readLocation();
})();
