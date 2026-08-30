(() => {
  "use strict";

  const root = document.documentElement;
  const toggle = document.querySelector("[data-menu-toggle]");
  const menu = document.querySelector("[data-menu]");

  root.classList.add("landing-js");

  if (!toggle || !menu) {
    return;
  }

  const setMenu = (open) => {
    toggle.setAttribute("aria-expanded", String(open));
    menu.classList.toggle("is-open", open);
    const label = toggle.querySelector(".sr-only");
    if (label) {
      label.textContent = open ? toggle.dataset.labelClose : toggle.dataset.labelOpen;
    }
  };

  toggle.addEventListener("click", () => {
    setMenu(toggle.getAttribute("aria-expanded") !== "true");
  });

  menu.addEventListener("click", (event) => {
    if (event.target.closest("a[href^='#']")) {
      setMenu(false);
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      setMenu(false);
      toggle.focus();
    }
  });

  const desktop = window.matchMedia("(min-width: 1021px)");
  const handleViewportChange = (event) => {
    if (event.matches) {
      setMenu(false);
    }
  };

  if (typeof desktop.addEventListener === "function") {
    desktop.addEventListener("change", handleViewportChange);
  } else {
    desktop.addListener(handleViewportChange);
  }
})();
