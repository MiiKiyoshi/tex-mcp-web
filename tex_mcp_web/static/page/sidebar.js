// The panel beside or under the paper, shown or hidden: ⇥ in the top bar, or \ pressed outside a
// field while no dialog is open, hides or shows it, and the browser keeps the choice for next
// time. setSidebarCollapsed is how the page's other parts show it.

import { $, eventOrigin } from "../shared/dom.js";

export function setSidebarCollapsed(collapsed) {
  $(".layout").classList.toggle("sidebar-collapsed", collapsed);
  $("#sidebar-toggle-btn").classList.toggle("active", collapsed);
  localStorage.setItem("sidebarCollapsed", collapsed ? "1" : "0");
}

export function attachSidebarToggle() {
  $("#sidebar-toggle-btn").addEventListener("click", () =>
    setSidebarCollapsed(!$(".layout").classList.contains("sidebar-collapsed")));
  document.addEventListener("keydown", (event) => {
    const origin = eventOrigin(event);
    if (["TEXTAREA", "INPUT", "SELECT"].includes(origin.tagName?.toUpperCase())) return;
    if (origin.isContentEditable || document.querySelector("dialog[open]")) return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.key !== "\\") return;
    event.preventDefault();
    setSidebarCollapsed(!$(".layout").classList.contains("sidebar-collapsed"));
  });
  setSidebarCollapsed(localStorage.getItem("sidebarCollapsed") === "1");
}
