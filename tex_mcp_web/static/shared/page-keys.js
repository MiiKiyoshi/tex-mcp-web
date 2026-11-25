// The keys of a review page that belong to none of its parts. attachCopyGuard(viewer) keeps a
// copy of text selected outside the PDF viewer, the element `viewer` selects, from the viewer,
// which handles copy on the whole document. attachEscapeKey({ own }) makes Ctrl+[ do what Escape
// does, outside the element `own` selects, as an editor with keys of its own, or everywhere.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $, eventOrigin } from "./dom.js";

export function attachCopyGuard(viewer) {
  // EmbedPDF handles copy on document. Keep text selected outside the viewer, in the sidebar, a
  // dialog or a notice, out of that handler. A field copies its own, which EmbedPDF leaves alone
  // too.
  document.addEventListener("keydown", (event) => {
    if (!(event.metaKey || event.ctrlKey) || event.altKey || event.key.toLowerCase() !== "c") return;
    if (eventOrigin(event).closest?.("input, textarea, [contenteditable]")) return;
    const selection = window.getSelection();
    const shown = $(viewer);
    if (selection && !selection.isCollapsed
      && !shown.contains(selection.anchorNode) && !shown.contains(selection.focusNode)) {
      event.stopImmediatePropagation();
    }
  }, { capture: true });
}

export function attachEscapeKey({ own = null } = {}) {
  // Ctrl+[ does what Escape does: Safari in full screen takes Escape to leave full screen, so a
  // comment being written could not be dropped without it.
  document.addEventListener("keydown", (event) => {
    if (!event.ctrlKey || event.code !== "BracketLeft" || (own !== null && event.target.closest?.(own))) return;
    event.preventDefault();
    const dialog = document.querySelector("dialog[open]");
    if (dialog === null) {
      event.target.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }));
    } else if (dialog.querySelector("#compose-cancel")) {
      dialog.querySelector("#compose-cancel").click();
    } else {
      dialog.close();
    }
  }, true);
}
