// The grips that size a review page's panel, #sidebar, within .layout: its left edge,
// #sidebar-width-grip, where the panel sits beside what is reviewed, and the bar along its
// top, #sidebar-grip, where it sits under it. A drag sets the size, and so do the keys on a
// focused grip: the arrows by a step, Home to the largest and End to the smallest. A double
// click on a grip gives back the size the page's CSS sets, and forgets the kept one. The browser
// keeps each size under the page's prefix, as <prefix>SidebarWidth and <prefix>PanelHeight,
// and the size is set on .layout as the CSS variable the page names, which the page's CSS lays
// the panel out by. The page's CSS also shows the grip its layout has. Their look is
// panel-grips.css.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import { $ } from "./dom.js";

// The tab row sits directly under the bar, and dragging moves the panel's edge out from under
// the finger: on release the tabs were where the finger now was, and the tap that ends a drag
// landed on one of them. A drag is not a tap, so clicks in the panel are ignored for this many
// milliseconds after one, at the capture phase, before any tab sees them. A window rather than
// a one-shot: the drag itself leaves a click behind, and a one-shot was spent on that one and
// let the offender through.
const DRAG_QUIET = 400;

// How far a key moves a grip, in pixels.
const STEP = 40;

// The panel's grips, wired. The page gives `prefix`, what the browser keeps the sizes under,
// and `width` and `height`, the CSS variables the panel's width beside what is reviewed and
// its height under it are set as, and may give `narrowest`, the least width in pixels, 240 by
// default, for a panel whose row needs more. It also gives what it changes of these, each
// called each time it is needed:
//   split()         a split stacked above the panel, { holdable(), centerY(), holdAt(y), keep() },
//                   whose bar stays where it is on the screen while the panel's height moves;
//                   none by default
//   resizing(size)  told of each frame a drag sets "width" or "height" in
//   resized(size)   told once a drag has ended, a key has moved a grip, or a double click has
//                   given back the page's own size
// It gives back setHeight(pixels), which sets the panel's height within its limits as a drag
// does, without keeping it, and gives back the height set.
export function attachPanelGrips({
  prefix, width, height, narrowest = 240, split = () => null, resizing = () => {}, resized = () => {},
}) {
  const layout = $(".layout");
  const sidebar = $("#sidebar");
  const widthGrip = $("#sidebar-width-grip");
  const heightGrip = $("#sidebar-grip");
  let draggedAt = -Infinity;

  const set = (name, pixels) => {
    const limited = Math.round(pixels);
    layout.style.setProperty(name, `${limited}px`);
    return limited;
  };
  // The width runs from the narrowest to 70% of the window, the height from 90px to 140px short
  // of it.
  const setWidth = (pixels) => set(width, Math.max(narrowest, Math.min(pixels, window.innerWidth * 0.7)));
  const setHeight = (pixels) => set(height, Math.max(90, Math.min(pixels, window.innerHeight - 140)));
  const keep = (name, pixels) => {
    try { localStorage.setItem(`${prefix}${name}`, String(pixels)); } catch { /* per-viewer only */ }
  };
  for (const [name, variable] of [["SidebarWidth", width], ["PanelHeight", height]]) {
    let saved = 0;
    try { saved = Number(localStorage.getItem(`${prefix}${name}`)); } catch { /* per-viewer only */ }
    if (saved > 0) layout.style.setProperty(variable, `${saved}px`);
  }

  // The size the page's CSS sets, given back by a double click on the grip.
  for (const [grip, name, variable, size] of [[widthGrip, "SidebarWidth", width, "width"],
    [heightGrip, "PanelHeight", height, "height"]]) {
    grip.addEventListener("dblclick", () => {
      try { localStorage.removeItem(`${prefix}${name}`); } catch { /* per-viewer only */ }
      layout.style.removeProperty(variable);
      resized(size);
    });
  }

  sidebar.addEventListener("click", (event) => {
    if (performance.now() - draggedAt >= DRAG_QUIET) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  // A split stacked above the panel, held where its bar is now on the screen; null for none.
  const holdSplit = () => {
    const stacked = split();
    if (!stacked?.holdable()) return null;
    const y = stacked.centerY();
    return { hold: () => stacked.holdAt(y), keep: () => stacked.keep() };
  };

  widthGrip.addEventListener("pointerdown", (down) => {
    widthGrip.classList.add("dragging");
    document.body.classList.add("sidebar-resizing");
    const right = layout.getBoundingClientRect().right;
    let pixels = null;
    follow(widthGrip, down, (pointer) => {
      pixels = setWidth(right - pointer.x);
      resizing("width");
    }, (ended, moved) => {
      if (moved) draggedAt = performance.now();
      widthGrip.classList.remove("dragging");
      document.body.classList.remove("sidebar-resizing");
      if (pixels !== null) keep("SidebarWidth", pixels);
      resized("width");
    });
  });
  widthGrip.addEventListener("keydown", (event) => {
    const wanted = stepped(event, "ArrowLeft", "ArrowRight", sidebar.getBoundingClientRect().width);
    if (wanted === null) return;
    event.preventDefault();
    keep("SidebarWidth", setWidth(wanted));
    resized("width");
  });

  heightGrip.addEventListener("pointerdown", (down) => {
    // The panel grows up from its foot, which stays where it is: the foot of the page's layout,
    // or of the window, where a phone's page scrolls under a panel standing at the window's foot.
    const bottom = sidebar.getBoundingClientRect().bottom;
    const held = holdSplit();
    let pixels = null;
    follow(heightGrip, down, (pointer) => {
      pixels = setHeight(bottom - pointer.y);
      held?.hold();
      resizing("height");
    }, (ended, moved) => {
      if (ended.cancelable) ended.preventDefault();
      if (moved) draggedAt = performance.now();
      if (pixels !== null) keep("PanelHeight", pixels);
      held?.keep();
      resized("height");
    });
  });
  heightGrip.addEventListener("keydown", (event) => {
    const wanted = stepped(event, "ArrowUp", "ArrowDown", sidebar.getBoundingClientRect().height);
    if (wanted === null) return;
    event.preventDefault();
    const held = holdSplit();
    keep("PanelHeight", setHeight(wanted));
    held?.hold();
    held?.keep();
    resized("height");
  });

  return { setHeight };
}

// A drag on a grip, from the press `down`: `frame(pointer)` sets the size from where the
// pointer is, `{ x, y }`, once a frame at most, and `end(ended, moved)` is told when the
// pointer lifts, with whether it moved. A finger sends more moves than the screen draws, and
// each one resized what is reviewed and wrote the size to storage, so the grip answered late
// and the drag read as stuck: one size per frame is all a drag can show, and the size is kept
// once, at the end.
function follow(grip, down, frame, end) {
  down.preventDefault();
  try { grip.setPointerCapture(down.pointerId); } catch { /* a synthetic or lost pointer */ }
  let pointer = null;
  let waiting = null;
  const apply = () => {
    waiting = null;
    frame(pointer);
  };
  const move = (moved) => {
    pointer = { x: moved.clientX, y: moved.clientY };
    if (waiting === null) waiting = requestAnimationFrame(apply);
  };
  const done = (ended) => {
    if (waiting !== null) cancelAnimationFrame(waiting);
    if (pointer !== null) apply();
    grip.removeEventListener("pointermove", move);
    grip.removeEventListener("pointerup", done);
    grip.removeEventListener("pointercancel", done);
    end(ended, pointer !== null);
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", done);
  grip.addEventListener("pointercancel", done);
}

// The size a key on a focused grip asks for, from the size now: a step larger or smaller by
// the arrows named, the largest by Home and the smallest by End; null for any other key.
function stepped(event, larger, smaller, current) {
  if (event.key === "Home") return Infinity;
  if (event.key === "End") return 0;
  if (event.key === larger) return current + STEP;
  if (event.key === smaller) return current - STEP;
  return null;
}
