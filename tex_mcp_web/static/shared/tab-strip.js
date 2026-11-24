// The tabs along the top of a review page's sidebar. Each .tab-btn shows one .tab-pane, the
// #tab-<name> its data-pane names, or else its data-tab: tabs that list comments share the
// Comments pane, so a second comment tab names that pane in data-pane.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, $$ } from "./dom.js";

// The strip, its tabs answering clicks. `shown(name, clicked)` is told of each tab shown, and
// whether a click showed it. show(name) shows a tab from the page's own code.
export function createTabStrip({ shown = () => {} } = {}) {
  function show(name, clicked = false) {
    for (const button of $$(".tab-btn")) button.classList.toggle("active", button.dataset.tab === name);
    const pane = $(`.tab-btn[data-tab="${CSS.escape(name)}"]`)?.dataset.pane ?? name;
    for (const element of $$(".tab-pane")) element.classList.toggle("active", element.id === `tab-${pane}`);
    shown(name, clicked);
  }
  for (const button of $$(".tab-btn")) button.addEventListener("click", () => show(button.dataset.tab, true));
  return { show };
}
