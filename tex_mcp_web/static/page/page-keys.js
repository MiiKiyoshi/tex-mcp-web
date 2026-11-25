// The page's keys that belong to none of its parts: a copy of text selected outside the Preview,
// kept from the PDF viewer, and Ctrl+[ outside the editor, which does what Escape does.

import { $, eventOrigin } from "../shared/dom.js";

export function attachCopyGuard() {
  // EmbedPDF handles copy on document. Keep text selected outside the Preview, in the
  // sidebar, a dialog or a notice, out of that handler. A field such as the editor's copies
  // its own, which EmbedPDF leaves alone too.
  document.addEventListener("keydown", (event) => {
    if (!(event.metaKey || event.ctrlKey) || event.altKey || event.key.toLowerCase() !== "c") return;
    if (eventOrigin(event).closest?.("input, textarea, [contenteditable]")) return;
    const selection = window.getSelection();
    const viewer = $("#pdf-viewer");
    if (selection && !selection.isCollapsed
      && !viewer.contains(selection.anchorNode) && !viewer.contains(selection.focusNode)) {
      event.stopImmediatePropagation();
    }
  }, { capture: true });
}

export function attachEscapeKey() {
  // Ctrl+[ does outside the editor what Escape does: Safari in full screen takes Escape to
  // leave full screen, so a comment being written could not be dropped without it.
  document.addEventListener("keydown", (event) => {
    if (!event.ctrlKey || event.code !== "BracketLeft" || event.target.closest?.("#source-editor")) return;
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
