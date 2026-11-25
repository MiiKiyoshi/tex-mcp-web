// The top bar: on a narrow screen it gives way a step at a time while it would spill over, and it
// holds the ⋯ menu, Settings and Download PDF. attachTopBar takes showSettings, which opens
// Settings, and digest, the digest of the PDF there is to download, null while there is none.

import { $, h } from "../shared/dom.js";
import { attachMenu } from "./menu.js";

// How the top bar gives way on a phone, one step at a time while it would spill over, after
// the title, the file's name and the buttons' words have gone and the build's status has
// shrunk: the faces after the first go, the tabs and the buttons draw closer together, the
// first face goes too, the git buttons drop their counts, which their tooltips still give,
// and last the tabs' names end in an ellipsis. Settings lists who is in the project whatever
// the bar shows.
const TOPBAR_STEPS = ["one-face", "tight", "faceless", "countless", "tabs-cut"];

function fitTopbar() {
  const bar = $(".topbar");
  bar.classList.remove(...TOPBAR_STEPS);
  for (const step of TOPBAR_STEPS) {
    if (bar.scrollWidth <= bar.clientWidth) break;
    bar.classList.add(step);
  }
}

export function attachTopBar({ showSettings, digest }) {
  // The bar is fitted again whenever it, or something in it, changes width: a face comes or
  // goes, a git button shows, or Settings changes the text size.
  const refit = new ResizeObserver(() => fitTopbar());
  refit.observe($(".topbar"));
  for (const part of $(".topbar").children) refit.observe(part);
  // Any choice in the top bar's ⋯ menu closes it.
  attachMenu($("#more-btn"), $("#more-menu"), {
    closes: (target) => target.closest("#more-menu button"),
    opening: () => { $("#download-pdf-btn").disabled = !digest(); },
  });
  $("#settings-btn").addEventListener("click", () => showSettings());
  $("#settings-menu-btn").addEventListener("click", () => showSettings());
  $("#download-pdf-btn").addEventListener("click", () => h("a", { href: "/pdf/download", download: "" }).click());
}
