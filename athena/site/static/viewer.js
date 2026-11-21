// SPDX-License-Identifier: GPL-3.0-or-later
// The zoom viewer on item pages. Without JavaScript the "Enlarge" links open the largest image instead.
(function () {
  "use strict";
  var holder = document.getElementById("viewer-data");
  var dialog = document.getElementById("zoom");
  if (!holder || !dialog || typeof dialog.showModal !== "function" || !window.OpenSeadragon) return;
  var data = JSON.parse(holder.textContent);
  var root = holder.getAttribute("data-root") || "";
  var el = function (id) { return document.getElementById(id); };
  var viewer = null;
  var current = 0;

  function sources() {
    return data.pages.map(function (p) {
      return { type: "legacy-image-pyramid", levels: p.levels.map(function (l) {
        return { url: root + l.url, width: l.width, height: l.height };
      }) };
    });
  }

  function update() {
    el("zoom-label").textContent = "Page " + data.pages[current].n + " of " + data.pages.length + ": " + data.title;
    el("zoom-prev").disabled = current === 0;
    el("zoom-next").disabled = current === data.pages.length - 1;
    var one = data.pages.length === 1;
    el("zoom-prev").hidden = one;
    el("zoom-next").hidden = one;
  }

  function go(index) {
    if (index < 0 || index >= data.pages.length) return;
    current = index;
    if (viewer.currentPage() !== index) viewer.goToPage(index);
    update();
  }

  function open(index) {
    current = index;
    dialog.showModal();
    if (!viewer) {
      viewer = window.OpenSeadragon({
        element: el("zoom-canvas"), tileSources: sources(), sequenceMode: true, initialPage: index,
        showNavigationControl: false, showSequenceControl: false, visibilityRatio: 1, constrainDuringPan: true,
        maxZoomPixelRatio: 2, animationTime: 0.4, crossOriginPolicy: false
      });
    } else {
      viewer.goToPage(index);
    }
    update();
  }

  document.querySelectorAll("a[data-zoom]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;   // let people open the image in a new tab
      e.preventDefault();
      open(parseInt(a.getAttribute("data-zoom"), 10));
    });
  });
  el("zoom-close").addEventListener("click", function () { dialog.close(); });
  el("zoom-prev").addEventListener("click", function () { go(current - 1); });
  el("zoom-next").addEventListener("click", function () { go(current + 1); });
  el("zoom-in").addEventListener("click", function () { viewer.viewport.zoomBy(1.6); viewer.viewport.applyConstraints(); });
  el("zoom-out").addEventListener("click", function () { viewer.viewport.zoomBy(1 / 1.6); viewer.viewport.applyConstraints(); });
  el("zoom-home").addEventListener("click", function () { viewer.viewport.goHome(); });
  dialog.addEventListener("close", function () {
    // After the browser has put focus back on the link that opened the viewer, move to the page last looked at.
    window.setTimeout(function () {
      var back = document.querySelector('a[data-zoom="' + current + '"]');
      if (back) { back.focus(); back.scrollIntoView({ block: "nearest" }); }
    }, 0);
  });
})();
