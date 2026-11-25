// The workspace shows the Preview, Source and the translation as the view asks: one pane,
// or two or three side by side or stacked, each pair parted by a grip dragged to share the
// room between them. A layout is a pane's name or [direction, first, second, grip]: "row"
// puts the two side by side and "col" stacks them. Preview and Source put the translation
// under their pane; Split puts it where Settings says. At 950px wide or less every
// pair is stacked, as Split always stacked its two panes there.
//
// The view tabs choose the view, and the browser keeps it, with the room each grip shares out,
// for next time. createWorkspace takes source's init() and resize(), pdf's zoomMode() and
// refit(zoomMode), and translationShown(), whether the translation shows. It gives attach(),
// restore(), view(), show(view), apply(refit), shows(pane), sourceBesidePreview(), and split(),
// how the panel's grips hold a split stacked above the panel.

import { $, $$ } from "../shared/dom.js";

const WORKSPACE_PANES = { preview: "#pdf-pane", source: "#source-pane", translation: "#translation-pane" };
const WORKSPACE_GRIPS = { split: "#split-grip", tgrip: "#translation-grip" };
const TRANSLATION_PLACES = ["preview", "source", "column", "replace"];

function stackLayout(node) {
  return typeof node === "string" ? node : ["col", stackLayout(node[1]), stackLayout(node[2]), node[3]];
}

function layoutNode(node, grip) {
  if (typeof node === "string") return null;
  if (node[3] === grip) return node;
  return layoutNode(node[1], grip) ?? layoutNode(node[2], grip);
}

function layoutPanes(node) {
  return typeof node === "string" ? [node] : [...layoutPanes(node[1]), ...layoutPanes(node[2])];
}

// The screen box two panes and their grip share: the union of their panes' boxes.
function layoutBox(node) {
  const boxes = layoutPanes(node).map((pane) => $(WORKSPACE_PANES[pane]).getBoundingClientRect());
  const left = Math.min(...boxes.map((box) => box.left));
  const top = Math.min(...boxes.map((box) => box.top));
  return {
    left,
    top,
    width: Math.max(...boxes.map((box) => box.right)) - left,
    height: Math.max(...boxes.map((box) => box.bottom)) - top,
  };
}

export function createWorkspace({ source, pdf, translationShown }) {
  // The view shown, and how a split stacked above the panel is held (attachWorkspaceLayout).
  const state = { view: "pdf", split: null };
  const stackedWorkspace = matchMedia("(max-width: 950px)");
  const workspaceLayout = { place: "preview", split: 0.5, shares: {}, tree: "preview" };

  function layoutTree() {
    const translated = translationShown();
    if (state.view === "pdf") return translated ? ["col", "preview", "translation", "tgrip"] : "preview";
    if (state.view === "source") return translated ? ["col", "source", "translation", "tgrip"] : "source";
    if (!translated) return ["row", "preview", "source", "split"];
    switch (workspaceLayout.place) {
      case "source": return ["row", "preview", ["col", "source", "translation", "tgrip"], "split"];
      case "column": return ["row", "preview", ["row", "source", "translation", "tgrip"], "split"];
      case "replace": return ["row", "preview", "translation", "split"];
      default: return ["row", ["col", "preview", "translation", "tgrip"], "source", "split"];
    }
  }

  // The split's share is the one Split always kept, for the Preview's side. The translation
  // grip keeps a share per view, and in Split per place: the first pane's part of the two.
  function shareKey() {
    return state.view === "split" ? workspaceLayout.place : state.view;
  }

  function shareOf(grip) {
    if (grip === "split") return workspaceLayout.split;
    const saved = workspaceLayout.shares[shareKey()];
    if (Number.isFinite(saved)) return saved;
    return state.view === "split" && workspaceLayout.place === "column" ? 0.5 : 0.64;
  }

  function setShare(grip, value) {
    const share = Math.max(0.15, Math.min(0.85, value));
    if (grip === "split") workspaceLayout.split = share;
    else workspaceLayout.shares[shareKey()] = share;
    applyLayout(false);
  }

  function keepLayout() {
    try {
      localStorage.setItem("texMcpSplitRatio", String(workspaceLayout.split));
      localStorage.setItem("texMcpTranslationPlace", workspaceLayout.place);
      localStorage.setItem("texMcpTranslationShares", JSON.stringify(workspaceLayout.shares));
    } catch { /* per-viewer only */ }
  }

  // The CSS grid of a layout: track sizes, with "grip" for a grip's 10px band, and the area
  // each cell belongs to. Of two panes side by side, the one with a single row spans the
  // rows of the other; stacked panes are the same with rows and columns swapped.
  function layoutGrid(node) {
    if (typeof node === "string") return { cols: [1], rows: [1], areas: [[node]] };
    const [direction, first, second, grip] = node;
    const flip = (grid) => ({
      cols: grid.rows,
      rows: grid.cols,
      areas: grid.cols.map((_, column) => grid.areas.map((row) => row[column])),
    });
    let a = layoutGrid(first);
    let b = layoutGrid(second);
    if (direction === "col") {
      a = flip(a);
      b = flip(b);
    }
    const share = shareOf(grip);
    const rows = a.rows.length >= b.rows.length ? a.rows : b.rows;
    const rowOf = (grid, index) => grid.areas[grid.rows.length === 1 ? 0 : index];
    const scale = (tracks, by) => tracks.map((size) => (size === "grip" ? size : size * by));
    const grid = {
      cols: [...scale(a.cols, share), "grip", ...scale(b.cols, 1 - share)],
      rows,
      areas: rows.map((_, index) => [...rowOf(a, index), grip, ...rowOf(b, index)]),
    };
    return direction === "col" ? flip(grid) : grid;
  }

  function layoutShows(pane) {
    return layoutPanes(workspaceLayout.tree).includes(pane);
  }

  function refitWorkspace() {
    source.resize();
    const zoomMode = pdf.zoomMode();
    pdf.refit(zoomMode);
  }

  function applyLayout(refit = true) {
    const tree = stackedWorkspace.matches ? stackLayout(layoutTree()) : layoutTree();
    workspaceLayout.tree = tree;
    const grid = layoutGrid(tree);
    const track = (size) => (size === "grip" ? "10px" : `minmax(0, ${size}fr)`);
    const workspace = $("#workspace");
    workspace.style.gridTemplateColumns = grid.cols.map(track).join(" ");
    workspace.style.gridTemplateRows = grid.rows.map(track).join(" ");
    workspace.style.gridTemplateAreas = grid.areas.map((row) => `"${row.join(" ")}"`).join(" ");
    const shown = new Set(grid.areas.flat());
    for (const [name, selector] of Object.entries({ ...WORKSPACE_PANES, ...WORKSPACE_GRIPS })) {
      $(selector).classList.toggle("off", !shown.has(name));
    }
    for (const grip of Object.keys(WORKSPACE_GRIPS)) {
      const node = layoutNode(tree, grip);
      if (!node) continue;
      const element = $(WORKSPACE_GRIPS[grip]);
      element.setAttribute("aria-orientation", node[0] === "row" ? "vertical" : "horizontal");
      element.setAttribute("aria-valuenow", String(Math.round(shareOf(grip) * 100)));
      element.setAttribute("aria-controls", layoutPanes(node).map((pane) => WORKSPACE_PANES[pane].slice(1)).join(" "));
    }
    for (const button of $$("[data-place-choice]")) {
      button.setAttribute("aria-checked", String(button.dataset.placeChoice === workspaceLayout.place));
    }
    source.resize();
    if (refit) requestAnimationFrame(refitWorkspace);
  }

  // Source beside the Preview is Split, unless Split's translation stands in for Source:
  // then the Source view, where the translation sits under the editor.
  async function showSourceBesidePreview() {
    if (state.view !== "split") await setWorkspaceView("split");
    if (!layoutShows("source")) await setWorkspaceView("source");
  }

  async function setWorkspaceView(view) {
    const zoomMode = pdf.zoomMode();
    state.view = view;
    const layout = $(".layout");
    layout.classList.remove("view-pdf", "view-source", "view-split");
    layout.classList.add(`view-${view}`);
    for (const button of $$(".view-tab")) {
      button.classList.toggle("active", button.dataset.view === view);
    }
    localStorage.setItem("workspaceView", view);
    applyLayout(false);
    if (view !== "pdf") await source.init();
    requestAnimationFrame(() => {
      source.resize();
      pdf.refit(zoomMode);
    });
  }

  function attachWorkspaceGrip(grip) {
    const element = $(WORKSPACE_GRIPS[grip]);
    const shareAt = (x, y) => {
      const node = layoutNode(workspaceLayout.tree, grip);
      if (!node) return;
      const box = layoutBox(node);
      const across = node[0] === "row";
      const position = across ? x - box.left : y - box.top;
      setShare(grip, (position - 5) / Math.max(1, (across ? box.width : box.height) - 10));
    };
    element.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      try { element.setPointerCapture(event.pointerId); } catch { /* synthetic or lost pointer */ }
      element.classList.add("dragging");
      const move = (moved) => shareAt(moved.clientX, moved.clientY);
      const done = () => {
        element.classList.remove("dragging");
        element.removeEventListener("pointermove", move);
        element.removeEventListener("pointerup", done);
        element.removeEventListener("lostpointercapture", done);
        keepLayout();
        refitWorkspace();
      };
      element.addEventListener("pointermove", move);
      element.addEventListener("pointerup", done);
      element.addEventListener("lostpointercapture", done);
    });
    element.addEventListener("keydown", (event) => {
      const node = layoutNode(workspaceLayout.tree, grip);
      if (!node) return;
      const across = node[0] === "row";
      const decrease = across ? "ArrowLeft" : "ArrowUp";
      const increase = across ? "ArrowRight" : "ArrowDown";
      if (![decrease, increase, "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      setShare(grip, event.key === "Home" ? 0.15 : event.key === "End" ? 0.85
        : shareOf(grip) + (event.key === decrease ? -0.05 : 0.05));
      keepLayout();
      refitWorkspace();
    });
  }

  function attachWorkspaceLayout() {
    try {
      const split = Number(localStorage.getItem("texMcpSplitRatio"));
      if (split) workspaceLayout.split = split;
      const place = localStorage.getItem("texMcpTranslationPlace");
      if (TRANSLATION_PLACES.includes(place)) workspaceLayout.place = place;
      const shares = JSON.parse(localStorage.getItem("texMcpTranslationShares") || "{}");
      if (shares && typeof shares === "object") workspaceLayout.shares = shares;
    } catch { /* per-viewer only */ }
    // With the panes stacked, the comments' bar under them changes the height the split
    // divides, and a share kept through that drag carried the split's bar up the screen
    // with it. The drag holds the bar where it was by moving the share instead.
    state.split = {
      holdable: () => stackedWorkspace.matches && layoutNode(workspaceLayout.tree, "split") !== null,
      centerY: () => {
        const box = $("#split-grip").getBoundingClientRect();
        return box.top + box.height / 2;
      },
      holdAt: (y) => {
        const box = layoutBox(layoutNode(workspaceLayout.tree, "split"));
        setShare("split", (y - box.top - 5) / Math.max(1, box.height - 10));
      },
      keep: keepLayout,
    };
    for (const grip of Object.keys(WORKSPACE_GRIPS)) attachWorkspaceGrip(grip);
    for (const button of $$("[data-place-choice]")) {
      button.addEventListener("click", () => {
        workspaceLayout.place = button.dataset.placeChoice;
        keepLayout();
        applyLayout();
        if (layoutShows("source")) source.init().catch((error) => console.error(error));
      });
    }
    for (const button of $$(".view-tab")) {
      button.addEventListener("click", () => {
        setWorkspaceView(button.dataset.view).catch((error) => alert(error.message));
      });
    }
    stackedWorkspace.addEventListener("change", () => applyLayout());
    applyLayout();
  }

  async function restore() {
    const savedView = localStorage.getItem("workspaceView");
    if (["source", "split"].includes(savedView)) await setWorkspaceView(savedView);
  }

  return {
    attach: attachWorkspaceLayout,
    restore,
    view: () => state.view,
    show: setWorkspaceView,
    apply: applyLayout,
    shows: layoutShows,
    sourceBesidePreview: showSourceBesidePreview,
    split: () => state.split,
  };
}
