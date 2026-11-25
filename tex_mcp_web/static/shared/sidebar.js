// The sidebar beside or under a review page's document, shown or hidden: the button
// #sidebar-toggle-btn, or \ pressed outside a field while no dialog is open, hides or shows it,
// and the browser keeps the choice for next time. attachSidebarToggle({ key, closedUnder }) wires
// them: `key` is what the browser keeps the choice under, and `closedUnder` a window width under
// which a page opened with no choice kept starts with the sidebar hidden, or none. A \ a part of
// the page has taken first (defaultPrevented), as a slide show's keys do, is left to it.
// setSidebarCollapsed(collapsed), once the toggle is attached, is how the page's other parts
// show or hide it.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, eventOrigin } from "./dom.js";

// What the browser keeps the choice under, as attachSidebarToggle was given it.
let kept = null;

function showCollapsed(collapsed) {
  $(".layout").classList.toggle("sidebar-collapsed", collapsed);
  $("#sidebar-toggle-btn").classList.toggle("active", collapsed);
}

export function setSidebarCollapsed(collapsed) {
  if (kept === null) throw new Error("setSidebarCollapsed: attachSidebarToggle has not run");
  showCollapsed(collapsed);
  try { localStorage.setItem(kept, collapsed ? "1" : "0"); } catch { /* the choice lasts this visit */ }
}

export function attachSidebarToggle({ key, closedUnder = 0 }) {
  kept = key;
  $("#sidebar-toggle-btn").addEventListener("click", () =>
    setSidebarCollapsed(!$(".layout").classList.contains("sidebar-collapsed")));
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;
    const origin = eventOrigin(event);
    if (["TEXTAREA", "INPUT", "SELECT"].includes(origin.tagName?.toUpperCase())) return;
    if (origin.isContentEditable || document.querySelector("dialog[open]")) return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.key !== "\\") return;
    event.preventDefault();
    setSidebarCollapsed(!$(".layout").classList.contains("sidebar-collapsed"));
  });
  let stored = null;
  try { stored = localStorage.getItem(key); } catch { /* none kept */ }
  showCollapsed(stored === null ? innerWidth < closedUnder : stored === "1");
}
