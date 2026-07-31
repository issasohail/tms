document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("[data-year]").forEach(function (element) {
    element.textContent = String(new Date().getFullYear());
  });

  const menuButton = document.querySelector(".mobile-menu");
  const navigation = document.getElementById("primary-navigation");

  if (menuButton && navigation) {
    menuButton.addEventListener("click", function () {
      const expanded = menuButton.getAttribute("aria-expanded") === "true";
      menuButton.setAttribute("aria-expanded", String(!expanded));
      navigation.classList.toggle("is-open", !expanded);
    });

    navigation.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        menuButton.setAttribute("aria-expanded", "false");
        navigation.classList.remove("is-open");
      });
    });
  }

  const carousel = document.querySelector("[data-feature-carousel]");
  if (!carousel) return;

  const slides = Array.from(carousel.querySelectorAll("[data-feature-slide]"));
  const previousButton = carousel.querySelector("[data-feature-prev]");
  const nextButton = carousel.querySelector("[data-feature-next]");
  const pauseButton = carousel.querySelector("[data-feature-pause]");
  const currentLabel = carousel.querySelector("[data-feature-current]");
  const progress = carousel.querySelector("[data-feature-progress]");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let currentIndex = 0;
  let timer = null;
  let isPaused = reduceMotion;

  function showSlide(index) {
    currentIndex = (index + slides.length) % slides.length;
    slides.forEach(function (slide, slideIndex) {
      const active = slideIndex === currentIndex;
      slide.classList.toggle("is-active", active);
      slide.setAttribute("aria-hidden", String(!active));
    });
    currentLabel.textContent = String(currentIndex + 1).padStart(2, "0");
    progress.style.width = (((currentIndex + 1) / slides.length) * 100) + "%";
  }

  function stopRotation() {
    if (timer) window.clearInterval(timer);
    timer = null;
  }

  function startRotation() {
    stopRotation();
    if (!isPaused && !document.hidden) {
      timer = window.setInterval(function () {
        showSlide(currentIndex + 1);
      }, 5500);
    }
  }

  previousButton.addEventListener("click", function () {
    showSlide(currentIndex - 1);
    startRotation();
  });

  nextButton.addEventListener("click", function () {
    showSlide(currentIndex + 1);
    startRotation();
  });

  pauseButton.addEventListener("click", function () {
    isPaused = !isPaused;
    pauseButton.setAttribute("aria-pressed", String(isPaused));
    pauseButton.textContent = isPaused ? "Play" : "Pause";
    startRotation();
  });

  carousel.addEventListener("mouseenter", stopRotation);
  carousel.addEventListener("mouseleave", startRotation);
  carousel.addEventListener("focusin", stopRotation);
  carousel.addEventListener("focusout", function (event) {
    if (!carousel.contains(event.relatedTarget)) startRotation();
  });
  document.addEventListener("visibilitychange", startRotation);

  if (reduceMotion) {
    pauseButton.setAttribute("aria-pressed", "true");
    pauseButton.textContent = "Play";
  }

  showSlide(0);
  startRotation();
});
