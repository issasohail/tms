(function () {
  "use strict";

  if (window.TMSMasonry) {
    window.TMSMasonry.refresh(document);
    return;
  }

  const pending = new Set();
  let frame = null;

  function directItems(grid) {
    return Array.from(grid.children).filter(function (item) {
      return item.matches("[data-masonry-item]");
    });
  }

  function visible(item) {
    return item.style.display !== "none" && item.getClientRects().length > 0;
  }

  function layout(grid) {
    if (!grid || !grid.isConnected) return;
    grid.classList.add("tms-masonry-ready");
    const styles = window.getComputedStyle(grid);
    const rowHeight = parseFloat(styles.gridAutoRows) || 8;
    const rowGap = parseFloat(styles.rowGap) || 0;

    directItems(grid).forEach(function (item) {
      item.style.gridRowEnd = "auto";
      if (!visible(item)) return;
      const contentHeight = Math.max(
        item.scrollHeight,
        item.getBoundingClientRect().height
      );
      const span = Math.max(
        1,
        Math.ceil((contentHeight + rowGap) / (rowHeight + rowGap))
      );
      item.style.gridRowEnd = "span " + span;
    });
  }

  function flush() {
    frame = null;
    const grids = Array.from(pending);
    pending.clear();
    grids.forEach(layout);
  }

  function schedule(grid) {
    if (!grid) return;
    pending.add(grid);
    if (frame === null) frame = window.requestAnimationFrame(flush);
  }

  function bind(grid) {
    if (!grid || grid.dataset.masonryBound === "1") {
      schedule(grid);
      return;
    }
    grid.dataset.masonryBound = "1";

    directItems(grid).forEach(function (item) {
      item.querySelectorAll("img, video").forEach(function (media) {
        media.addEventListener("load", function () { schedule(grid); });
        media.addEventListener("error", function () { schedule(grid); });
        media.addEventListener("loadedmetadata", function () { schedule(grid); });
      });
    });

    if ("ResizeObserver" in window) {
      const observer = new ResizeObserver(function () { schedule(grid); });
      observer.observe(grid);
      directItems(grid).forEach(function (item) { observer.observe(item); });
      grid._tmsMasonryResizeObserver = observer;
    }
    schedule(grid);
  }

  function refresh(root) {
    const scope = root && root.querySelectorAll ? root : document;
    const parentGrid = scope.closest
      ? scope.closest("[data-masonry-grid]")
      : null;
    if (parentGrid) bind(parentGrid);
    if (scope.matches && scope.matches("[data-masonry-grid]")) bind(scope);
    scope.querySelectorAll("[data-masonry-grid]").forEach(bind);
  }

  window.TMSMasonry = {refresh: refresh};

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { refresh(document); });
  } else {
    refresh(document);
  }

  window.addEventListener("resize", function () { refresh(document); });
  document.addEventListener("htmx:afterSwap", function (event) {
    refresh(event.target || document);
  });

  const mutationObserver = new MutationObserver(function (mutations) {
    mutations.forEach(function (mutation) {
      const target = mutation.target.nodeType === 1
        ? mutation.target
        : mutation.target.parentElement;
      const grid = target && target.closest
        ? target.closest("[data-masonry-grid]")
        : null;
      if (grid) {
        grid.dataset.masonryBound = "";
        if (grid._tmsMasonryResizeObserver) {
          grid._tmsMasonryResizeObserver.disconnect();
          grid._tmsMasonryResizeObserver = null;
        }
        bind(grid);
      }
    });
  });
  mutationObserver.observe(document.documentElement, {
    childList: true,
    subtree: true,
  });
})();
