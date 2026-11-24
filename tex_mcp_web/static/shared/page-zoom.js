// The page kept at its own size where the browser would zoom all of it: as an iPhone takes a
// field for the cursor, and as a trackpad pinches over a part of the page that does not zoom
// itself. Whether the browser shows the page zoomed is pageIsZoomed.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

// Whether the browser shows the page itself zoomed in, by a pinch or by a field it zoomed into.
export function pageIsZoomed() {
  return (window.visualViewport?.scale ?? 1) > 1.01;
}

// An iPhone zooms the page into a field whose text is under 16px the moment it takes the
// cursor, and leaves it zoomed. A viewport that may not grow past 1, from a finger landing
// on a field until just after it has the cursor, keeps that from happening, and pinching
// zooms the page as before at any other moment. A page already zoomed in is left as it is.
// The page's fields so keep the size of the text around them. It changes the page's
// <meta name="viewport">, and only on an iPhone or an iPad.
export function holdZoomOnFields() {
  const agent = navigator.userAgent;
  if (!/iP(hone|ad|od)/.test(agent) && !(/Macintosh/.test(agent) && navigator.maxTouchPoints > 1)) return;
  const viewport = document.querySelector('meta[name="viewport"]');
  const free = viewport.content;
  let timer = 0;
  const release = (after) => {
    clearTimeout(timer);
    timer = setTimeout(() => { viewport.content = free; }, after);
  };
  document.addEventListener("touchstart", (event) => {
    if (event.touches.length === 1 && event.target.closest?.("textarea, input, select") && !pageIsZoomed()) {
      clearTimeout(timer);
      viewport.content = `${free}, maximum-scale=1`;
    } else {
      release(0);
    }
  }, { capture: true, passive: true });
  document.addEventListener("touchend", () => release(500), { capture: true, passive: true });
  document.addEventListener("focusin", () => release(500), true);
}

// A pinch on a trackpad reaches the page as a wheel turned with Ctrl held. Over `view`, the
// part of the page that zooms with it (a selector), it is left to that part. Anywhere else
// the browser would magnify the whole page and push the panel's right edge out of the window,
// so there the browser does not take it. Cmd or Ctrl with + and - still zoom the page.
export function holdPinchOutside(view) {
  document.addEventListener("wheel", (event) => {
    if (event.ctrlKey && !event.target.closest?.(view)) event.preventDefault();
  }, { passive: false });
}
