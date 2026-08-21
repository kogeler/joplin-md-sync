function initializeHomePage() {
  const home = document.querySelector(".product-home");
  if (!home || home.dataset.revealInitialized === "true") {
    return;
  }

  home.dataset.revealInitialized = "true";
  const elements = home.querySelectorAll("[data-reveal]");

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    elements.forEach((element) => element.classList.add("is-visible"));
    return;
  }

  home.classList.add("reveal-ready");
  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { rootMargin: "0px 0px -7% 0px", threshold: 0.08 },
  );

  elements.forEach((element) => observer.observe(element));
}

if (typeof document$ !== "undefined") {
  document$.subscribe(initializeHomePage);
} else {
  document.addEventListener("DOMContentLoaded", initializeHomePage);
}
