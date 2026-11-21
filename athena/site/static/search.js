// SPDX-License-Identifier: GPL-3.0-or-later
// Starts Pagefind's search box on the search page. The index and the searching both stay in the browser.
(function () {
  "use strict";
  var box = document.getElementById("search");
  if (!box || !window.PagefindUI) return;
  var root = new URL(box.getAttribute("data-root") || "./", window.location.href);
  var ui = new window.PagefindUI({
    element: "#search", bundlePath: new URL("pagefind/", root).pathname, showSubResults: true, showImages: true,
    pageSize: 10, resetStyles: false, autofocus: true,
    translations: { placeholder: "Search titles and page text" },
    processResult: function (result) {
      if (result.meta && result.meta.image && !/^([a-z]+:|\/)/i.test(result.meta.image)) {
        result.meta.image = new URL(result.meta.image, root).pathname;
      }
      return result;
    }
  });
  var q = new URLSearchParams(window.location.search).get("q");
  if (q) ui.triggerSearch(q);
})();
