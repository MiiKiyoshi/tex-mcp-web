// The presses on a PDF's pages that a review page reads before the PDF viewer (EmbedPDF) does: a
// finger, and a press on a comment's highlight. attachPdfPresses takes the viewer, on whose
// shadow root it listens; areaMode(), whether the Area tool is on, which takes the presses as
// they come; and prefix, the page's name for its own classes and attributes in the viewer, as
// .<prefix>-comment-badge and data-<prefix>-selecting (viewerStyle in pdf-theme.js).
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

export function attachPdfPresses(viewer, areaMode, prefix) {
  const root = viewer.shadowRoot;
  // The viewer selects text by a pointer dragged across it or a double click on a word.
  // A finger held still on a word, which is how a tablet asks for a selection, did
  // nothing, and the browser's own long-press menu took the touch away. The hold
  // selects the word as a double click there would. A hold on a comment's highlight
  // selects the word under the highlight; the lift is not the press that opens the
  // comment.
  let highlightPress = null;
  let touchHold = null;
  const endTouchHold = () => {
    if (touchHold === null) return;
    clearTimeout(touchHold.timer);
    touchHold = null;
  };
  // On the pages a finger's press reaches the viewer only once the finger has been held
  // still: seen at once, it began a selection under a finger that was scrolling. Held, the
  // press is handed on where the finger is; a tap is handed on as it lifts, so a highlight
  // still opens its comment.
  const onPages = (event) => event.composedPath().some((node) =>
    node instanceof Element && /(auto|scroll)/.test(getComputedStyle(node).overflowY));
  const press = (type, x, y) => root.elementFromPoint(x, y)?.dispatchEvent(new PointerEvent(type, {
    bubbles: true, composed: true, cancelable: true, clientX: x, clientY: y, pointerId: 1,
    pointerType: "touch", isPrimary: true, button: 0, buttons: type === "pointerdown" ? 1 : 0,
  }));
  root.addEventListener("pointerdown", (event) => {
    if (!event.isTrusted) return;
    endTouchHold();
    if (areaMode() || event.pointerType !== "touch" || !onPages(event)) return;
    event.stopImmediatePropagation();
    // The finger's moves go to what is under it, as a mouse's do. A touch is otherwise held by
    // what it landed on, which may be a comment's badge beside the word, as a touch screen
    // lands a finger on the nearest button: the viewer would then see no move to drag on.
    try { event.target.releasePointerCapture(event.pointerId); } catch { /* not held */ }
    // A second finger is a pinch. Its press stays from the viewer, which began a selection
    // under it, and the first finger's hold has ended with it.
    if (!event.isPrimary) return;
    const { clientX, clientY } = event;
    touchHold = { x: clientX, y: clientY, held: false, timer: setTimeout(() => {
      highlightPress = null;
      touchHold.held = true;
      press("pointerdown", clientX, clientY);
      root.elementFromPoint(clientX, clientY)?.dispatchEvent(
        new MouseEvent("dblclick", { bubbles: true, clientX, clientY }));
    }, 500) };
  }, { capture: true });
  root.addEventListener("pointermove", (event) => {
    if (touchHold !== null && !touchHold.held
      && Math.hypot(event.clientX - touchHold.x, event.clientY - touchHold.y) >= 10) {
      endTouchHold();
    }
  }, { capture: true });
  // Once held, the finger extends the selection rather than scrolling the pages.
  root.addEventListener("touchmove", (event) => {
    if (touchHold?.held) event.preventDefault();
  }, { capture: true, passive: false });
  // A finger lifted after a hold is no tap. A click the browser sends for that lift, where the
  // finger left, as Chrome's touch emulation does, would be a third click after the hold's
  // double click, which the viewer takes for a triple click and selects the whole line with:
  // it is kept from the viewer.
  let heldLift = null;
  root.addEventListener("pointerup", (event) => {
    if (event.isTrusted && touchHold !== null && !touchHold.held) {
      const { x, y } = touchHold;
      endTouchHold();
      event.stopImmediatePropagation();
      press("pointerdown", x, y);
      press("pointerup", x, y);
      return;
    }
    if (event.isTrusted && touchHold?.held) heldLift = { at: Date.now(), x: event.clientX, y: event.clientY };
    if (event.isTrusted) endTouchHold();
  }, { capture: true });
  root.addEventListener("click", (event) => {
    const lift = heldLift;
    heldLift = null;
    if (!event.isTrusted || lift === null || Date.now() - lift.at >= 500
      || Math.hypot(event.clientX - lift.x, event.clientY - lift.y) >= 10) return;
    event.stopImmediatePropagation();
  }, { capture: true });
  root.addEventListener("pointercancel", endTouchHold, { capture: true });
  root.addEventListener("contextmenu", (event) => {
    if (touchHold !== null) event.preventDefault();
  });
  root.addEventListener("pointerdown", (event) => {
    if (areaMode() || event.target.closest(`.${prefix}-comment-badge`)) return;
    if (!event.isTrusted || event.button !== 0 || !event.target.closest("[data-no-interaction]")) return;
    highlightPress = { target: event.target, x: event.clientX, y: event.clientY, dragged: false };
    viewer.setAttribute(`data-${prefix}-selecting`, "");
    const underneath = root.elementFromPoint(event.clientX, event.clientY);
    event.stopImmediatePropagation();
    underneath.dispatchEvent(new PointerEvent("pointerdown", event));
  }, { capture: true });
  root.addEventListener("pointermove", (event) => {
    if (highlightPress && Math.hypot(event.clientX - highlightPress.x, event.clientY - highlightPress.y) >= 3) {
      highlightPress.dragged = true;
    }
  }, { capture: true });
  root.addEventListener("pointerup", (event) => {
    const press = highlightPress;
    highlightPress = null;
    if (press && !press.dragged) press.target.dispatchEvent(new PointerEvent("pointerdown", event));
    queueMicrotask(() => viewer.removeAttribute(`data-${prefix}-selecting`));
  }, { capture: true });
  root.addEventListener("pointercancel", () => {
    highlightPress = null;
    viewer.removeAttribute(`data-${prefix}-selecting`);
  }, { capture: true });
}
