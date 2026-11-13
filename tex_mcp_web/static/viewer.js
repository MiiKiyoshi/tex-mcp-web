import EmbedPDF, { PdfAnnotationSubtype } from "/static/embedpdf/embedpdf.js?v=selection-yellow";

const DOCUMENT_ID = "paper";
const ANNOTATION_PREFIX = "tex-web:";
const GOTO_ANNOTATION_ID = "tex-web-goto";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  viewer: null,
  sendKey: null,
  // "off" when Settings hides the key letters on the ringed comment's controls.
  keyLetters: null,
  // The threads whose replies are on their way to Overleaf, and whether its threads are being
  // synced: their buttons show the work under way until the list shows what it did.
  sending: new Set(),
  syncingComments: false,
  selection: null,
  areaMode: false,
  annotations: null,
  scroll: null,
  zoom: null,
  annotationsReady: false,
  layoutReady: false,
  pdfDigest: null,
  comments: [],
  paper: null,
  pendingAnchor: null,
  pendingSourceRevision: null,
  pendingHeading: null,
  composeSubmitting: false,
  errors: [],
  warnings: [],
  expanded: new Set(),
  picked: new Set(),
  // The box a pick last started from, so shift can run from it to the next one.
  pickedFrom: null,
  activeForm: null,
  // What the reviewer has typed into a thread's Overleaf reply, kept across renders, with
  // the draft it started from and the reason a send was refused.
  overleafReplies: new Map(),
  // Where on Overleaf the comments from there are whose words are not in the file here yet.
  overleafUnplaced: {},
  // The cookie is kept, the files are kept the same both ways (which brings new threads in
  // by themselves), how many open threads on Overleaf wait to be synced, and where a folder
  // cloned by git stands against Overleaf's branch (null until read, or no git).
  overleafSignedIn: false,
  overleafProject: null,
  // The reviewer's own name on Overleaf, once the page sits in the project.
  overleafMe: "",
  overleafFullSync: false,
  overleafWaiting: 0,
  overleafGit: null,
  overleafGitRead: 0,
  // Where a folder brought together with Overleaf by Get and Send stands, or null.
  overleafFiles: null,
  overleafGitTimer: null,
  overleafGitStateTimer: null,
  // The files commit & push showed the reviewer, whose edits its commit takes.
  committing: [],
  overleafEdits: "",
  // Comments brought in from Overleaf, and which comment tab is shown: the comments written
  // here, or the threads on Overleaf. The list, the editor and the PDF show that tab's.
  overleafImported: new Set(),
  // The edits made on Overleaf with track changes on, waiting to be accepted or rejected.
  overleafSuggested: [],
  commentTab: "comments",
  editingEntry: null,
  focusedCommentId: null,
  // The comment a range started from, while v holds one.
  rangeFrom: null,
  // The suggested edits being accepted or rejected now, and when they were last read.
  reviewingEdits: new Set(),
  suggestedRead: 0,
  // The comment tab and status the list shows, so a list drawn again knows it is the same view.
  commentsView: null,
  // How many times the list was asked for, so only the latest answer is drawn.
  commentsAsked: 0,
  annotationCommentById: new Map(),
  annotationObserver: null,
  badgeFrame: null,
  referencePreviewRequest: 0,
  lastViewerPointer: null,
  appliedCompileTimestamp: null,
  compileRefreshPromise: null,
  view: "pdf",
  editor: null,
  sourcePath: null,
  sourceRevision: null,
  sourceDirty: false,
  sourceLoading: false,
  sourceCommentMarkers: [],
  // Each comment's marked text in the editor, as { range, comment }.
  sourceCommentSpans: [],
  // The gutter's marks, as "row class": a row takes each class once.
  sourceDecorations: new Set(),
  sourceCommentsByRow: new Map(),
  panelDraggedAt: -Infinity,
};

function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") element.className = value;
    else if (key === "text") element.textContent = value;
    else if (key === "data") {
      for (const [name, dataValue] of Object.entries(value)) element.dataset[name] = dataValue;
    } else if (key === "style") Object.assign(element.style, value);
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) element.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined) continue;
    element.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return element;
}

function clear(node) {
  node.replaceChildren();
}

// Overleaf is marked by a leaf wherever the page speaks of it.
const LEAF_ICON = '<svg class="leaf-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
  + 'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
  + '<path d="M3 13C3 7 7 3 13 3c0 6-4 10-10 10z"/><path d="M3 13l6-6"/></svg>';

function leafIcon() {
  const template = document.createElement("template");
  template.innerHTML = LEAF_ICON;
  return template.content.firstChild;
}

function placeholder(text) {
  return h("p", { class: "placeholder", text });
}

function rectToBBox(rect) {
  return [
    rect.origin.x,
    rect.origin.y,
    rect.origin.x + rect.size.width,
    rect.origin.y + rect.size.height,
  ];
}

function bboxToRect([x1, y1, x2, y2]) {
  return {
    origin: { x: x1, y: y1 },
    size: { width: x2 - x1, height: y2 - y1 },
  };
}

function pdfUrl() {
  const version = state.pdfDigest ?? Date.now().toString();
  return `/pdf?v=${encodeURIComponent(version)}`;
}

function capturePdfView() {
  if (!state.layoutReady || !state.scroll || !state.zoom) return null;
  const metrics = state.scroll.getMetrics();
  if (metrics.pageVisibilityMetrics.length === 0) return null;
  const topPage = metrics.pageVisibilityMetrics.reduce((closest, page) =>
    page.viewportY < closest.viewportY ? page : closest);
  return {
    zoomLevel: state.zoom.getState().currentZoomLevel,
    pageNumber: topPage.pageNumber,
    pageCoordinates: {
      x: topPage.original.pageX,
      y: topPage.original.pageY,
    },
  };
}

// The viewer's colours for each of the page's themes. Warm, the restful one, is drawn on
// the viewer's light scheme with its own warm paper colours.
const VIEWER_COLORS = {
  light: {
    accent: {
      primary: "#647b95",
      primaryHover: "#536b86",
      primaryActive: "#455c75",
      primaryLight: "#e5e9ed",
      primaryForeground: "#fffdf8",
    },
    background: {
      app: "#faf8f2",
      surface: "#fffdf8",
      surfaceAlt: "#f4f1e8",
      elevated: "#fffdf8",
      input: "#fffdf8",
    },
    foreground: {
      primary: "#302e29",
      secondary: "#6f6a61",
      muted: "#8a8479",
      disabled: "#aaa396",
      onAccent: "#fffdf8",
    },
    interactive: {
      hover: "#f0ece2",
      active: "#e7e1d4",
      selected: "#e5e9ed",
      focus: "#647b95",
    },
    border: {
      default: "#ddd7ca",
      subtle: "#ebe6db",
      strong: "#bdb5a5",
    },
  },
  dark: {
    accent: {
      primary: "#8ea5bf",
      primaryHover: "#a2b6cc",
      primaryActive: "#b6c6d8",
      primaryLight: "#5b636d",
      primaryForeground: "#4a4944",
    },
    background: {
      app: "#403f3b",
      surface: "#54524d",
      surfaceAlt: "#4a4944",
      elevated: "#5c5a54",
      input: "#4a4944",
    },
    foreground: {
      primary: "#f5f2eb",
      secondary: "#d8d2c6",
      muted: "#c2bbaf",
      disabled: "#77726a",
      onAccent: "#4a4944",
    },
    interactive: {
      hover: "#5d5b55",
      active: "#68655e",
      selected: "#5b636d",
      focus: "#8ea5bf",
    },
    border: {
      default: "#7a766e",
      subtle: "#5d5b55",
      strong: "#8e8a80",
    },
  },
  warm: {
    accent: {
      primary: "#9b6a3d",
      primaryHover: "#8a5c33",
      primaryActive: "#774f2b",
      primaryLight: "#ded6c6",
      primaryForeground: "#fbf3e4",
    },
    background: {
      app: "#d9d2c5",
      surface: "#ece6dc",
      surfaceAlt: "#e2dccf",
      elevated: "#efebe2",
      input: "#f4f0e9",
    },
    foreground: {
      primary: "#3a2f22",
      secondary: "#6c5c46",
      muted: "#8a7860",
      disabled: "#b2a186",
      onAccent: "#fbf3e4",
    },
    interactive: {
      hover: "#e3dccf",
      active: "#d8cfc0",
      selected: "#ded6c6",
      focus: "#9b6a3d",
    },
    border: {
      default: "#ccc3b3",
      subtle: "#dcd6c9",
      strong: "#b3a896",
    },
  },
};

function viewerTheme(theme) {
  return {
    preference: theme === "dark" ? "dark" : "light",
    light: theme === "warm" ? VIEWER_COLORS.warm : VIEWER_COLORS.light,
    dark: VIEWER_COLORS.dark,
  };
}

async function initializePdfViewer(pdfView) {
  state.annotationObserver?.disconnect();
  state.annotationObserver = null;
  if (state.badgeFrame !== null) cancelAnimationFrame(state.badgeFrame);
  state.badgeFrame = null;
  clearGotoHighlight();
  hideReferencePreview();
  const host = $("#pdf-viewer");
  clear(host);
  state.selection = null;
  state.areaMode = false;
  state.annotations = null;
  state.scroll = null;
  state.zoom = null;
  state.annotationsReady = false;
  state.layoutReady = false;
  state.annotationCommentById.clear();

  state.viewer = EmbedPDF.init({
    type: "container",
    target: host,
    wasmUrl: "/static/embedpdf/pdfium.wasm",
    worker: false,
    fonts: { ui: null, signature: null },
    theme: viewerTheme(document.documentElement.dataset.theme),
    tabBar: "never",
    disabledCategories: ["annotation", "redaction", "insert", "form", "panel-comment"],
    // The pointer mode on a touch screen too: a finger scrolls the pages in it (see the
    // viewer's style), so a phone starts in the mode that also selects.
    pan: { defaultMode: "never" },
    documentManager: {
      initialDocuments: [{ url: pdfUrl(), documentId: DOCUMENT_ID }],
    },
    annotations: { autoCommit: false, selectAfterCreate: false },
    stamp: { manifests: [] },
    tiling: { tileSize: 768, overlapPx: 4, extraRings: 0 },
    selection: {
      toleranceFactor: 0.5,
      minSelectionDragDistance: 3,
      marquee: { enabled: false },
    },
  });
  if (!state.viewer) throw new Error("EmbedPDF did not create a viewer");

  const registry = await state.viewer.registry;
  const viewerStyle = document.createElement("style");
  viewerStyle.textContent = `
    [data-epdf-i="main-toolbar"] {
      gap: 4px !important;
      padding: 0 6px !important;
    }
    [data-epdf-i="main-toolbar"] button {
      height: 26px !important;
      min-width: 26px !important;
      padding: 3px !important;
    }
    [data-epdf-i="main-toolbar"] button svg {
      height: 16px !important;
      width: 16px !important;
    }
    :host([data-tex-selecting]) [data-no-interaction] *,
    :host([data-tex-area]) [data-no-interaction] * {
      pointer-events: none !important;
    }
    :host([data-tex-area]) * {
      cursor: crosshair !important;
      touch-action: none !important;
    }
    .tex-comment-badge {
      position: absolute;
      min-width: 18px;
      height: 18px;
      padding: 0 4px;
      border: 1px solid #8b7413;
      border-radius: 9px;
      color: #302900;
      background: #ffe77a;
      font: 11px/16px system-ui, sans-serif;
      text-align: center;
      transform-origin: top left;
      pointer-events: auto;
      cursor: pointer;
      z-index: 2;
    }
    .tex-comment-badge.stale {
      border-style: dashed;
      color: #6d6650;
      background: #f6f1e2;
    }
    :host([data-tex-theme="dark"]) * { color-scheme: dark; scrollbar-color: #8a867c #403f3b; }
    :host([data-tex-theme="warm"]) * { scrollbar-color: #b3a896 #d9d2c5; }
    /* On a touch screen a finger dragged on the pages scrolls them in the pointer mode too,
       as a page does, and a finger held still selects first. The viewer turns touch
       scrolling off in that mode so that every drag selects. */
    @media (pointer: coarse) {
      [style*="touch-action: none"]:not([data-epdf-handle]):not([data-epdf-vertex]) {
        touch-action: pan-x pan-y !important;
      }
    }
  `;
  state.viewer.shadowRoot.appendChild(viewerStyle);
  state.viewer.setAttribute("data-tex-theme", document.documentElement.dataset.theme);
  state.viewer.shadowRoot.addEventListener("pointerdown", (event) => {
    state.lastViewerPointer = { x: event.clientX, y: event.clientY };
  }, { capture: true });
  const selectionCapability = registry.getPlugin("selection").provides();
  const annotationCapability = registry.getPlugin("annotation").provides();
  const scrollCapability = registry.getPlugin("scroll").provides();
  const zoomCapability = registry.getPlugin("zoom").provides();
  const commands = registry.getPlugin("commands").provides();
  const ui = registry.getPlugin("ui").provides();

  state.selection = selectionCapability.forDocument(DOCUMENT_ID);
  installAreaTool(registry, selectionCapability, commands, ui);
  state.selection.onBeginSelection(() => state.viewer.setAttribute("data-tex-selecting", ""));
  state.selection.onEndSelection(() => state.viewer.removeAttribute("data-tex-selecting"));
  const root = state.viewer.shadowRoot;
  // The viewer selects text by a pointer dragged across it or a double click on a word.
  // A finger held still on a word, which is how a tablet asks for a selection, did
  // nothing, and the browser's own long-press menu took the touch away. The hold
  // selects the word as a double click there would. A hold on a comment's highlight
  // selects the word under the highlight; the lift is not the press that opens the
  // comment.
  let highlightPress = null;
  // Cmd (macOS) or Ctrl + double click: the double click selects the word as usual,
  // and Source opens it beside the PDF.
  root.addEventListener("dblclick", (event) => {
    if (!event.metaKey && !event.ctrlKey) return;
    setTimeout(() => openSelectionInSource()
      .catch((error) => alert(`Could not open the source: ${error.message}`)), 120);
  });
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
    if (state.areaMode || event.pointerType !== "touch" || !event.isPrimary || !onPages(event)) return;
    event.stopImmediatePropagation();
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
  root.addEventListener("pointerup", (event) => {
    if (event.isTrusted && touchHold !== null && !touchHold.held) {
      const { x, y } = touchHold;
      endTouchHold();
      event.stopImmediatePropagation();
      press("pointerdown", x, y);
      press("pointerup", x, y);
      return;
    }
    if (event.isTrusted) endTouchHold();
  }, { capture: true });
  root.addEventListener("pointercancel", endTouchHold, { capture: true });
  root.addEventListener("contextmenu", (event) => {
    if (touchHold !== null) event.preventDefault();
  });
  root.addEventListener("pointerdown", (event) => {
    if (state.areaMode || event.target.closest(".tex-comment-badge")) return;
    if (!event.isTrusted || event.button !== 0 || !event.target.closest("[data-no-interaction]")) return;
    highlightPress = { target: event.target, x: event.clientX, y: event.clientY, dragged: false };
    state.viewer.setAttribute("data-tex-selecting", "");
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
    queueMicrotask(() => state.viewer.removeAttribute("data-tex-selecting"));
  }, { capture: true });
  root.addEventListener("pointercancel", () => {
    highlightPress = null;
    state.viewer.removeAttribute("data-tex-selecting");
  }, { capture: true });
  state.annotationObserver = new MutationObserver((records) => {
    const onlyOwnedChanges = records.every((record) => {
      if (record.type === "attributes") {
        return record.target.classList.contains("tex-comment-badge")
          || record.target.classList.contains("tex-comment-segment");
      }
      return [...record.addedNodes, ...record.removedNodes].every((node) =>
        node.nodeType === Node.ELEMENT_NODE && node.classList.contains("tex-comment-badge"));
    });
    if (!onlyOwnedChanges) scheduleCommentBadges();
  });
  state.annotationObserver.observe(root, {
    attributes: true,
    attributeFilter: ["class", "style"],
    childList: true,
    subtree: true,
  });
  state.annotations = annotationCapability.forDocument(DOCUMENT_ID);
  state.scroll = scrollCapability.forDocument(DOCUMENT_ID);
  state.scroll.onPageChange(() => scheduleTranslation(600));
  state.zoom = zoomCapability.forDocument(DOCUMENT_ID);

  commands.registerCommand({
    id: "tex-web:comment-selection",
    label: "Comment",
    icon: "comment",
    action: openTextSelectionCompose,
  });
  state.viewer.registerIcon("tex-source", { viewBox: "0 0 24 24", path: "M8 7l-5 5 5 5 M16 7l5 5-5 5", strokeWidth: 2 });
  commands.registerCommand({
    id: "tex-web:selection-source",
    label: "Source",
    icon: "tex-source",
    action: () => openSelectionInSource().catch((error) => alert(`Could not open the source: ${error.message}`)),
  });
  const item = (id, commandId) => ({ type: "command-button", id, commandId, variant: "icon-text" });
  ui.mergeSchema({
    selectionMenus: {
      selection: {
        id: "selection",
        visibilityDependsOn: { itemIds: ["tex-web-comment-selection"] },
        items: [item("tex-web-comment-selection", "tex-web:comment-selection"),
          item("tex-web-selection-source", "tex-web:selection-source")],
      },
    },
  });

  state.annotations.onStateChange((annotationState) => {
    if (annotationState.selectedUids.length !== 1) return;
    const commentId = state.annotationCommentById.get(annotationState.selectedUids[0]);
    if (commentId) {
      setSidebarCollapsed(false);
      switchTab(state.commentTab);
      const comment = state.comments.find((item) => item.id === commentId);
      // The comment the list has just opened and shown here is left as the list has it.
      if (comment && !(comment.id === state.focusedCommentId && state.expanded.has(comment.id))) focusComment(comment);
      return;
    }
    const selected = state.annotations.getSelectedAnnotation();
    if (selected?.object?.target) {
      queueMicrotask(() => state.annotations?.deselectAnnotation());
      showReferencePreview(selected.object).catch((error) => console.error(error));
    }
  });

  scrollCapability.onLayoutReady((event) => {
    if (event.documentId !== DOCUMENT_ID) return;
    state.layoutReady = true;
    scheduleTranslation(0);   // asked for before the pages existed, and page 1 fires no change
    if (pdfView && event.totalPages > 0) {
      state.zoom.requestZoom(pdfView.zoomLevel);
      state.scroll.scrollToPage({
        pageNumber: Math.min(pdfView.pageNumber, event.totalPages),
        pageCoordinates: pdfView.pageCoordinates,
        behavior: "instant",
      });
    }
  });

  state.annotations.onAnnotationEvent((event) => {
    if (event.type !== "loaded") return;
    state.annotationsReady = true;
    syncCommentAnnotations();
  });
}

function hideReferencePreview() {
  state.referencePreviewRequest += 1;
  const preview = $("#reference-preview");
  if (preview) preview.classList.add("hidden");
}

function positionReferencePreview() {
  const preview = $("#reference-preview");
  const pointer = state.lastViewerPointer;
  if (!preview || !pointer) return;
  const gap = 12;
  const bounds = preview.getBoundingClientRect();
  const left = Math.min(
    Math.max(gap, pointer.x + gap),
    Math.max(gap, window.innerWidth - bounds.width - gap),
  );
  const below = pointer.y + gap;
  const top = below + bounds.height <= window.innerHeight - gap
    ? below
    : Math.max(gap, pointer.y - bounds.height - gap);
  preview.style.left = `${left}px`;
  preview.style.top = `${top}px`;
}

async function showReferencePreview(annotation) {
  if (!annotation.rect || annotation.pageIndex === undefined) return;
  const requestId = ++state.referencePreviewRequest;
  const bbox = rectToBBox(annotation.rect);
  const params = new URLSearchParams({
    page: String(annotation.pageIndex + 1),
    bbox: bbox.join(","),
  });
  const response = await fetch(`/reference-preview?${params}`);
  if (requestId !== state.referencePreviewRequest || !response.ok) return;
  const result = await response.json();
  if (requestId !== state.referencePreviewRequest) return;
  if (typeof result.text !== "string" || result.text.length === 0) {
    throw new Error("Reference preview response has no text");
  }
  const preview = $("#reference-preview");
  $("#reference-preview-text").textContent = result.text.replace(/\s+/g, " ").trim();
  preview.classList.remove("hidden");
  positionReferencePreview();
}

async function openTextSelectionCompose() {
  if (!state.selection || !state.pdfDigest) return;
  const formatted = state.selection.getFormattedSelection();
  if (formatted.length === 0) return;
  if (formatted.length !== 1) {
    alert("Select text on one page at a time.");
    return;
  }
  const lines = await state.selection.getSelectedText().toPromise();
  const quote = lines.join(" ").replace(/\s+/g, " ").trim();
  if (!quote) return;

  const formattedSelection = formatted[0];
  const selection = {
    page: formattedSelection.pageIndex + 1,
    bbox: rectToBBox(formattedSelection.rect),
    rects: formattedSelection.segmentRects.map(rectToBBox),
  };
  openCompose(
    {
      kind: "text_selection",
      quote,
      selection,
      pdf_digest: state.pdfDigest,
    },
    `PDF text: "${quote.slice(0, 80)}${quote.length > 80 ? "…" : ""}"`,
    quote,
  );
}

// The Area tool sits with the pan and pointer tools in the viewer's toolbar and is a mode
// of the viewer as they are. In it a drag on a page draws a box instead of selecting text,
// and the comment written for the box opens when the drag ends. The tool then gives the
// pointer back.
const AREA_MODE = "texArea";

function installAreaTool(registry, selectionCapability, commands, ui) {
  const interaction = registry.getPlugin("interaction-manager").provides();
  interaction.registerMode({ id: AREA_MODE, scope: "page", exclusive: false, cursor: "crosshair" });
  const scope = interaction.forDocument(DOCUMENT_ID);
  interaction.onModeChange(({ activeMode }) => {
    state.areaMode = activeMode === AREA_MODE;
    state.viewer.toggleAttribute("data-tex-area", state.areaMode);
    if (!state.areaMode) return;
    // The document's modes exist once it has loaded, which it has by the time a tool is picked.
    selectionCapability.enableForMode(AREA_MODE, {
      enableSelection: false, showSelectionRects: false, enableMarquee: true, showMarqueeRects: true,
    }, DOCUMENT_ID);
    state.selection.clear();
  });
  state.selection.onMarqueeEnd(({ pageIndex, rect }) => {
    if (!state.areaMode || rect === null) return;
    // A click, not a box.
    if (rect.size.width < 4 || rect.size.height < 4) return;
    scope.activateDefaultMode();
    openCompose(
      { kind: "area", page: pageIndex + 1, bbox: rectToBBox(rect), pdf_digest: state.pdfDigest },
      `PDF area on p${pageIndex + 1}`,
    );
  });
  state.viewer.registerIcon("tex-area", {
    viewBox: "0 0 24 24",
    path: "M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6 M8 12h8 M12 8v8",
    strokeWidth: 2,
  });
  commands.registerCommand({
    id: "tex-web:area",
    label: "Draw a box to comment on",
    icon: "tex-area",
    categories: ["tools"],
    action: () => (scope.getActiveMode() === AREA_MODE ? scope.activateDefaultMode() : scope.activate(AREA_MODE)),
    active: ({ state: viewerState, documentId }) =>
      viewerState.plugins["interaction-manager"]?.documents?.[documentId]?.activeMode === AREA_MODE,
  });
  const toolbar = ui.getSchema().toolbars["main-toolbar"];
  const items = toolbar.items.map((item) => item.id !== "center-group" ? item : {
    ...item,
    items: [...item.items, { type: "command-button", id: "area-button", commandId: "tex-web:area", variant: "icon" }],
  });
  // One rule at every width. The viewer's own rules took the pan, pointer and zoom buttons
  // into a menu below 500px, as on a phone, where this bar has room for all of them.
  const everyWidth = {
    minWidth: 0,
    show: ["pan-button", "pointer-button", "divider-3", "page-settings-button", "zoom-menu-button", "divider-2"],
    hide: ["mode-select-button", "overflow-left-action-menu-button", "overflow-tabs-button", "view-mode",
      "annotate-mode", "shapes-mode", "insert-mode", "form-mode", "redact-mode", "zoom-toolbar"],
  };
  ui.mergeSchema({ toolbars: { "main-toolbar": { items, responsive: { breakpoints: { everyWidth } } } } });
}

// Source opens the editor beside the Preview, in Split, where SyncTeX says the selected
// lines come from, and selects the words there.
async function openSelectionInSource() {
  if (!state.selection || !state.pdfDigest) return;
  const formatted = state.selection.getFormattedSelection();
  if (formatted.length !== 1) {
    alert("Select text on one page at a time.");
    return;
  }
  const quote = (await state.selection.getSelectedText().toPromise()).join(" ");
  const response = await fetch("/synctex/pdf-to-source", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      page: formatted[0].pageIndex + 1,
      rects: formatted[0].segmentRects.map(rectToBBox),
      pdf_digest: state.pdfDigest,
    }),
  });
  if (!response.ok) throw new Error(await responseError(response));
  const place = await response.json();
  await showSourceBesidePreview();
  if (!(await openSource(place.file, place.line_start))) return;
  selectQuoteInLines(quote, place.line_start, place.line_end);
}

// The words a PDF shows and the TeX that prints them differ in spacing, in ties, in words
// split at a line's end and in ligatures, so they are compared with all of that set aside.
// Words a macro prints are not in the source as written; the lines are selected then.
function selectQuoteInLines(quote, first, last) {
  const Range = window.ace.require("ace/range").Range;
  const session = state.editor.session;
  const lines = session.getLines(first - 1, last - 1);
  const kept = [];
  lines.forEach((line, row) => {
    for (let column = 0; column < line.length; column += 1) {
      if (!/[\s~]/.test(line[column])) kept.push({ character: line[column], row: first - 1 + row, column });
    }
  });
  const text = kept.map((item) => item.character).join("");
  const unligated = quote.replace(/ﬀ/g, "ff").replace(/ﬁ/g, "fi").replace(/ﬂ/g, "fl")
    .replace(/ﬃ/g, "ffi").replace(/ﬄ/g, "ffl");
  let range = null;
  for (const words of [unligated.replace(/(\p{L})-\s+(\p{L})/gu, "$1$2"), unligated]) {
    const wanted = words.replace(/\s+/g, "");
    const at = wanted === "" ? -1 : text.indexOf(wanted);
    if (at < 0) continue;
    const start = kept[at];
    const end = kept[at + wanted.length - 1];
    range = new Range(start.row, start.column, end.row, end.column + 1);
    break;
  }
  if (range === null) range = new Range(first - 1, 0, last - 1, lines[lines.length - 1].length);
  state.editor.selection.setRange(range);
  state.editor.scrollToLine(range.start.row, true, true, () => {});
  state.editor.focus();
}

// A section comment names the heading it was pressed on too, since headings can share a title.
function openCompose(anchor, label, selectionText = "", heading = null) {
  state.pendingAnchor = anchor;
  state.pendingSourceRevision = anchor.kind === "source_range" ? state.sourceRevision : null;
  state.pendingHeading = heading;
  $("#compose-anchor").textContent = label;
  $("#compose-text").value = "";
  $("#compose-suggestion-old").value = selectionText;
  $("#compose-suggestion-new").value = "";
  $("#compose-suggestion-details").open = Boolean(selectionText);
  $("#compose-dialog").showModal();
  setTimeout(() => $("#compose-text").focus(), 50);
}

function clearPendingSelection() {
  if (state.selection) state.selection.clear();
  state.pendingAnchor = null;
}

async function responseError(response) {
  const text = await response.text();
  if (!text) return String(response.status);
  try {
    return JSON.parse(text).error;
  } catch (error) {
    return `${response.status}: ${text}`;
  }
}

function sourceMode(path) {
  if (path.endsWith(".tex")) return "ace/mode/latex";
  if (path.endsWith(".bib")) return "ace/mode/bibtex";
  return "ace/mode/text";
}

function setSourceStatus(text, kind = "") {
  const status = $("#source-status");
  status.textContent = text;
  status.classList.toggle("dirty", kind === "dirty");
  status.classList.toggle("conflict", kind === "conflict");
}

function setSourceDirty(dirty) {
  state.sourceDirty = dirty;
  $("#source-save-btn").disabled = !dirty;
  if (dirty) {
    setSourceStatus("Unsaved", "dirty");
    clearSourceCommentMarkers();
  }
  updateSourceCommentButton();
}

function updateSourceCommentButton() {
  const button = $("#source-comment-btn");
  if (!button) return;
  const selected = Boolean(state.editor?.getSelectedText().trim());
  button.disabled = !selected || state.sourceDirty;
  button.title = state.sourceDirty
    ? "Save source before commenting"
    : selected ? "Comment on the selected source text (Ctrl+Alt+M, gc with Vim keys)" : "Select source text to comment";
  $("#source-pdf-btn").disabled = !selected;
}

// The words selected in the editor, shown where the Preview prints them, beside the editor
// in Split, at the zoom the Preview is read at: SyncTeX names the boxes of their lines, and
// the server finds the printed words in them.
async function showSourceSelectionInPdf() {
  const text = state.editor.getSelectedText();
  if (!text.trim() || !state.sourcePath || !state.scroll || !state.annotations) return;
  const range = state.editor.getSelectionRange();
  const lastRow = range.end.column === 0 && range.end.row > range.start.row ? range.end.row - 1 : range.end.row;
  const response = await fetch("/synctex/source-to-pdf-words", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file: state.sourcePath, line_start: range.start.row + 1, line_end: lastRow + 1, text }),
  });
  if (!response.ok) throw new Error(await responseError(response));
  const place = await response.json();
  if (state.view !== "split") {
    await setWorkspaceView("split");
    // The Preview takes its new width before it is scrolled.
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }
  const [x1, y1, x2, y2] = place.bbox;
  // At once: a smooth scroll in Safari came late, or stopped short, when one place followed
  // another.
  state.scroll.scrollToPage({
    pageNumber: place.page,
    pageCoordinates: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 },
    behavior: "instant",
    alignX: 50,
    alignY: 50,
  });
  clearGotoHighlight();
  state.annotations.createAnnotation(place.page - 1, {
    id: GOTO_ANNOTATION_ID,
    pageIndex: place.page - 1,
    type: PdfAnnotationSubtype.HIGHLIGHT,
    rect: bboxToRect(place.bbox),
    segmentRects: place.rects.map(bboxToRect),
    // The editor's selection blue, apart from the comments' yellow.
    opacity: 0.6,
    strokeColor: "#b5d5ff",
    contents: "",
  });
}

function clearSourceCommentMarkers() {
  if (!state.editor) return;
  for (const marker of state.sourceCommentMarkers) state.editor.session.removeMarker(marker);
  for (const mark of state.sourceDecorations) {
    const [row, name] = mark.split(" ");
    state.editor.session.removeGutterDecoration(Number(row), name);
  }
  state.sourceCommentMarkers = [];
  state.sourceCommentSpans = [];
  state.sourceDecorations.clear();
  state.sourceCommentsByRow.clear();
}

// Ace adds a class to a row again on a second call and takes one away on a removal: a class
// given twice would outlast its comments. Each goes on a row once.
function decorateRow(row, name) {
  const mark = `${row} ${name}`;
  if (state.sourceDecorations.has(mark)) return;
  state.sourceDecorations.add(mark);
  state.editor.session.addGutterDecoration(row, name);
}

function syncSourceCommentMarkers() {
  if (!state.editor) return;
  clearSourceCommentMarkers();
  if (!state.sourcePath) return;
  const Range = window.ace.require("ace/range").Range;
  for (const comment of state.comments) {
    if (comment.status !== "open" || comment.anchor.kind !== "source_range"
        || comment.anchor.file !== state.sourcePath) continue;
    if (comment.stale || state.sourceDirty) continue;
    const source = comment.resolved_source ?? comment.anchor;
    const firstRow = Math.max(0, source.line_start - 1);
    const lastRow = Math.max(firstRow, source.line_end - 1);
    const startColumn = source.column_start ?? 0;
    const endColumn = source.column_end ?? state.editor.session.getLine(lastRow).length;
    // A reattached comment whose text was deleted has an empty range: the line where
    // the text stood gets a faint mark instead, so the comment does not vanish.
    const deleted = firstRow === lastRow && startColumn === endColumn;
    state.sourceCommentMarkers.push(state.editor.session.addMarker(
      new Range(firstRow, startColumn, lastRow, deleted ? startColumn + 1 : endColumn),
      deleted ? "source-comment-deleted" : "source-comment-highlight",
      deleted ? "fullLine" : "text",
      false,
    ));
    state.sourceCommentSpans.push({ range: new Range(firstRow, startColumn, lastRow, deleted ? startColumn + 1 : endColumn), comment });
    decorateRow(firstRow, "source-comment-line");
    const rowComments = state.sourceCommentsByRow.get(firstRow) ?? [];
    rowComments.push(comment);
    state.sourceCommentsByRow.set(firstRow, rowComments);
  }
  // The edits suggested on Overleaf in this file, whatever the list shows, as Overleaf's editor
  // marks them: the inserted words underlined in their author's colour, and a red mark where
  // words were deleted. The server placed each in the file as saved.
  for (const edit of state.sourceDirty ? [] : state.overleafSuggested) {
    if (edit.file !== state.sourcePath || !edit.here) continue;
    const { line_start: lineStart, column_start: columnStart, line_end: lineEnd, column_end: columnEnd } = edit.here;
    const item = suggestedItem(edit);
    if (edit.inserted) {
      const range = new Range(lineStart - 1, columnStart, lineEnd - 1, columnEnd);
      state.sourceCommentMarkers.push(state.editor.session.addMarker(range, suggestedInsertClass(edit.user_id), "text", false));
      state.sourceCommentSpans.push({ range, comment: item });
    }
    if (edit.deleted) {
      const range = new Range(lineEnd - 1, columnEnd, lineEnd - 1, columnEnd + 1);
      state.sourceCommentMarkers.push(state.editor.session.addMarker(range, "source-suggested-delete", "text", false));
      if (!edit.inserted) state.sourceCommentSpans.push({ range, comment: item });
    }
    decorateRow(lineStart - 1, "source-suggested-line");
  }
  // The text an open proposal would replace, whatever its comment points at: a comment on
  // the PDF proposes in the source too, and the editor marks the same text the card's −
  // line shows. The server found each piece once in the file, so an edit made since then
  // that moves or duplicates it leaves that piece unmarked rather than marked wrongly.
  if (state.sourceDirty) return;
  const text = state.editor.session.getValue();
  for (const comment of state.comments) {
    const suggestion = comment.suggestion;
    if (comment.status !== "open" || !suggestion || suggestion.file !== state.sourcePath) continue;
    for (const change of suggestion.changes) {
      const at = text.indexOf(change.old);
      if (at < 0 || text.indexOf(change.old, at + 1) >= 0) continue;
      const start = state.editor.session.doc.indexToPosition(at);
      const end = state.editor.session.doc.indexToPosition(at + change.old.length);
      state.sourceCommentMarkers.push(state.editor.session.addMarker(
        new Range(start.row, start.column, end.row, end.column),
        "source-suggestion-highlight",
        "text",
        false,
      ));
      state.sourceCommentSpans.push({ range: new Range(start.row, start.column, end.row, end.column), comment });
      decorateRow(start.row, "source-comment-line");
      decorateRow(start.row, "source-suggestion-line");
      const rowComments = state.sourceCommentsByRow.get(start.row) ?? [];
      if (!rowComments.includes(comment)) rowComments.push(comment);
      state.sourceCommentsByRow.set(start.row, rowComments);
    }
  }
}

function openSourceCommentCompose() {
  if (!state.editor || !state.sourcePath || state.sourceDirty) return;
  const text = state.editor.getSelectedText();
  const range = state.editor.getSelectionRange();
  if (!text || range.isEmpty()) return;
  const lineStart = range.start.row + 1;
  const lineEnd = range.end.row + 1;
  // Back in the editor once the comment is sent or dropped, to go on from where it was.
  $("#compose-dialog").addEventListener("close", () => state.editor.focus(), { once: true });
  openCompose(
    {
      kind: "source_range",
      file: state.sourcePath,
      line_start: lineStart,
      line_end: lineEnd,
      column_start: range.start.column,
      column_end: range.end.column,
    },
    `Source: ${state.sourcePath}:${lineStart}-${lineEnd}`,
    text,
  );
}

// A line given puts the cursor there and the keys in the editor, unless *focus* is false.
async function openSource(path, line = null, force = false, focus = true) {
  if (!state.editor || !path) return false;
  if (!force && path === state.sourcePath) {
    if (line !== null) {
      state.editor.gotoLine(Math.max(1, Number(line)), 0, true);
      state.editor.focus();
    }
    return true;
  }
  if (!force && state.sourceDirty && path !== state.sourcePath
      && !confirm(`Discard unsaved changes to ${state.sourcePath}?`)) {
    showSourceFile(state.sourcePath);
    return false;
  }

  const response = await fetch(`/source?${new URLSearchParams({ path })}`);
  if (!response.ok) throw new Error(await responseError(response));
  const source = await response.json();
  state.sourceLoading = true;
  state.editor.session.setMode(sourceMode(source.path));
  state.editor.setValue(source.text, -1);
  state.sourceLoading = false;
  state.sourcePath = source.path;
  state.sourceRevision = source.revision;
  state.sourceDirty = false;
  showSourceFile(source.path);
  if (state.view !== "pdf") scheduleTranslation(300);
  $("#source-save-btn").disabled = true;
  $("#source-reload-btn").disabled = false;
  setSourceStatus("Saved");
  syncSourceCommentMarkers();
  updateSourceCommentButton();
  if (line !== null) {
    state.editor.gotoLine(Math.max(1, Number(line)), 0, true);
    if (focus) state.editor.focus();
  }
  return true;
}

async function refreshSourceFiles() {
  const response = await fetch("/sources");
  if (!response.ok) throw new Error(await responseError(response));
  const result = await response.json();
  const select = $("#source-file");
  clear(select);
  for (const path of result.files) {
    select.appendChild(h("option", { value: path, text: path }));
  }
  if (result.files.length === 0) {
    state.editor.setReadOnly(true);
    setSourceStatus("No watched source files");
    return;
  }
  state.editor.setReadOnly(false);
  const preferred = result.files.includes(state.sourcePath)
    ? state.sourcePath
    : result.files.includes(result.main_file)
      ? result.main_file
      : result.files[0];
  showSourceFile(preferred);
  if (preferred !== state.sourcePath) await openSource(preferred);
}

// The file list's choice, and on a touch screen the name drawn over the list in the bar's
// own type: a list there takes 16px type so the phone does not zoom in on it, which drew
// the name larger than everything else in the bar. The list lies over the name, clear,
// and still opens on a tap.
function showSourceFile(path) {
  $("#source-file").value = path;
  $("#source-file-name").textContent = path.split("/").pop();
}

// Light, dark or warm, as the reader last chose, else, under System, light or dark as the
// system is, following it as it changes. The page's own parts and the viewer's change; the
// PDF's pages are drawn as compiled, white in every theme.
const THEMES = ["light", "dark", "warm"];
let themeChosen = null;
try {
  const kept = localStorage.getItem("texMcpTheme");
  if (THEMES.includes(kept)) themeChosen = kept;
} catch { /* none kept */ }

function darkTheme() {
  return document.documentElement.dataset.theme === "dark";
}

function editorTheme() {
  return darkTheme() ? "ace/theme/one_dark" : "ace/theme/textmate";
}

function applyTheme() {
  const theme = themeChosen ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = theme;
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.setAttribute("aria-checked", String(button.dataset.themeChoice === (themeChosen ?? "system")));
  }
  state.editor?.setTheme(editorTheme());
  state.viewer?.setTheme?.(viewerTheme(theme));
  // The viewer's scroll bars take the page's scheme, which its own theme does not set.
  state.viewer?.setAttribute("data-tex-theme", theme);
}

// Settings: the paper's Overleaf project, the cookie that signs in to it, how the files
// follow it, where they stand, and the page's look. The cookie is handed over once and is
// never shown again.
function showSettings(status = "") {
  $("#overleaf-status").textContent = status;
  if (!$("#settings-dialog").open) {
    $("#settings-dialog").showModal();
    // Opening reads Overleaf's branch afresh, so what the dialog says is what is there now.
    loadOverleafStatus()
      .then((linked) => (linked?.git || state.overleafFiles ? overleafGitAction("fetch") : null))
      .catch((error) => console.error(error));
  }
}

function renderOverleafStatus({ project, cookie, sync = false, live = false, settable = true, git = null, waiting = 0 }) {
  $("#overleaf-link-row").hidden = Boolean(project) || !settable;
  $("#overleaf-connected").hidden = !project;
  $("#overleaf-disconnect-btn").hidden = !settable;
  $("#overleaf-settable").hidden = settable || Boolean(project);
  // How the files follow Overleaf is a choice in every connected folder, by hand unless
  // live is chosen. What each changes depends on whether the folder is a git clone.
  $("#overleaf-mode").hidden = !project;
  for (const box of $$('#overleaf-mode input[name="overleaf-mode"]')) {
    box.checked = box.value === (sync ? "live" : "hand");
    box.disabled = !settable;
  }
  // The sync button and the tab it is in show drawings, so the note shows them too.
  const syncDrawing = [...$("#overleaf-sync-comments-btn").querySelectorAll("svg")].map((svg) => svg.cloneNode(true));
  // Get and Send show their drawings beside their words, which a phone leaves out.
  const drawn = (selector, word) => [...[...$(selector).querySelectorAll("svg")].map((svg) => {
    const copy = svg.cloneNode(true);
    copy.removeAttribute("hidden");
    return copy;
  }), word];
  $("#overleaf-mode-hand-note").replaceChildren(...(git
    ? ["Files change only when you press git pull or commit & push, which show in the top bar when there are commits or edits to move. Overleaf gets nothing until you commit & push. A new comment thread on Overleaf comes in when you press "]
    : ["Files change only when you press ", ...drawn("#git-pull-btn", " Get"), " or ", ...drawn("#git-push-btn", " Send"),
      ", which show in the top bar when there are edits to move. Overleaf gets nothing until you Send. A new comment thread on Overleaf comes in when you press "]),
  ...syncDrawing, " in the ", leafIcon(), " Comments tab.");
  $("#overleaf-mode-live-note").textContent = git
    ? "While this review page is open, text files such as .tex change both ways within a second, and a new comment thread on Overleaf comes in by itself. Figures and other uploaded files go by git: one changed here goes with commit & push, and one added on Overleaf comes in by itself, or with git pull when the top bar shows it."
    : "While this review page is open, text files such as .tex change both ways within a second, and a new comment thread on Overleaf comes in by itself. Figures and other uploaded files are copied whole when one side replaces them, and a new figure or source file here goes up by itself.";
  $("#overleaf-direct").hidden = !project;
  $("#overleaf-tracking-git").hidden = !git;
  $("#overleaf-git").hidden = !git && !state.overleafFiles;
  if (!git) {
    state.overleafGit = null;
    renderGitButtons();
  }
  state.overleafSignedIn = Boolean(cookie);
  state.overleafProject = project ?? null;
  state.overleafFullSync = live;
  state.overleafWaiting = waiting;
  if (project) {
    $("#overleaf-project").href = project;
    $("#overleaf-project").textContent = project.replace(/^https?:\/\//, "");
  }
  $("#overleaf-cookie-row").hidden = Boolean(cookie);
  $("#overleaf-cookie-help").hidden = Boolean(cookie);
  $("#overleaf-signed").hidden = !cookie;
  $("#overleaf-signed-text").textContent = cookie === "environment"
    ? "Signed in with the cookie in the environment variable TEX_MCP_WEB_OVERLEAF_COOKIE."
    : "Signed in with your saved cookie.";
  $("#overleaf-cookie-forget").hidden = cookie !== "file";
  renderOverleafThreadSync();
  renderPickedActions();
}

// Comments sync at the commit Overleaf is at: a thread is on words, and only the same text
// puts it exactly where Overleaf has it.
// Sync comments, atop the comment list.
function renderOverleafThreadSync() {
  const side = $("#overleaf-sync-comments-btn");
  const waiting = state.overleafWaiting;
  // Files kept the same both ways bring new threads in by themselves: nothing to press.
  side.hidden = !state.overleafProject || state.overleafFullSync || state.commentTab !== "overleaf-comments";
  side.querySelector(".count").textContent = waiting ? String(waiting) : "";
  const words = waiting ? `Sync comments from Overleaf (${waiting} new)` : "Sync comments from Overleaf";
  side.title = words;
  side.setAttribute("aria-label", words);
  const files = state.overleafFiles;
  side.disabled = !state.overleafSignedIn || state.syncingComments
    || (state.overleafGit !== null && state.overleafGit.state !== "same")
    || (files !== null && (!files.read || files.get.length + files.send.length + files.choose.length > 0));
  side.classList.toggle("busy", state.syncingComments);
}

async function loadOverleafStatus() {
  const response = await fetch("/overleaf", { cache: "no-store" });
  if (!response.ok) return null;
  const linked = await response.json();
  renderOverleafStatus(linked);
  return linked;
}

function commits(count) {
  return `${count} commit${count === 1 ? "" : "s"}`;
}

function replies(count) {
  return `${count} repl${count === 1 ? "y" : "ies"}`;
}

// Where the folder stands against Overleaf's branch: said in the dialog, and Pull and Push
// in the top bar, each shown only when it has commits to move.
function renderOverleafGit(now) {
  state.overleafGit = now;
  // With the files kept the same, comments come in by themselves, not after a commit.
  const then = state.overleafFullSync ? "" : ", and comments sync after that";
  $("#overleaf-git-state").textContent = {
    same: `This folder is at Overleaf's commit (${now.there}).`,
    ahead: `This folder has ${commits(now.ahead)} Overleaf lacks. commit & push in the top bar sends them${then}.`,
    behind: `Overleaf has ${commits(now.behind)} this folder lacks. git pull in the top bar brings them in${then}.`,
    diverged: `Overleaf and this folder each have commits the other lacks (${now.ahead} here, ${now.behind} on Overleaf). `
      + `git pull in the top bar merges them, commit & push sends the result${then}.`,
  }[now.state];
  $("#overleaf-git-uncommitted").hidden = !now.uncommitted.length;
  $("#overleaf-git-uncommitted").textContent = `Not committed yet, so commit & push in the top bar commits them first: ${now.uncommitted.join(", ")}`;
  renderGitButtons();
  renderOverleafThreadSync();
}

function fileCount(count) {
  return `${count} file${count === 1 ? "" : "s"}`;
}

// A folder brought together with Overleaf by hand: Get and Send, each with the files it moves.
function renderOverleafFiles(now) {
  state.overleafFiles = now;
  const counts = [now.get.length, now.send.length];
  $("#overleaf-git").hidden = false;
  $("#overleaf-git-state").textContent = !now.read ? "Reading Overleaf's files…"
    : counts[0] && counts[1] ? `Overleaf has edits to ${fileCount(counts[0])} this folder lacks, and this folder has edits to ${fileCount(counts[1])} Overleaf lacks. Get brings them in and Send makes them there, and comments sync after that.`
    : counts[0] ? `Overleaf has edits to ${fileCount(counts[0])} this folder lacks. Get in the top bar brings them in, and comments sync after that.`
    : counts[1] ? `This folder has edits to ${fileCount(counts[1])} Overleaf lacks. Send in the top bar makes them there, and comments sync after that.`
    : "This folder and Overleaf hold the same files.";
  $("#overleaf-git-uncommitted").hidden = true;
  renderGitButtons();
  renderOverleafThreadSync();
}

function renderGitButtons() {
  const pull = $("#git-pull-btn");
  const push = $("#git-push-btn");
  const byHand = state.overleafFiles;
  for (const button of [pull, push]) {
    button.querySelector(".by-hand-mark").hidden = !byHand;
    button.querySelector(".wide-label").textContent = byHand ? (button === pull ? "Get" : "Send")
      : (button === pull ? "git pull" : "commit & push");
  }
  if (byHand) {
    pull.hidden = !byHand.get.length;
    push.hidden = !byHand.send.length;
    pull.disabled = push.disabled = false;
    pull.querySelector(".git-count").textContent = byHand.get.length ? String(byHand.get.length) : "";
    push.querySelector(".git-count").textContent = byHand.send.length ? String(byHand.send.length) : "";
    pull.title = `Get Overleaf's edits to ${fileCount(byHand.get.length)}: ${byHand.get.join(", ")}`;
    push.title = `Send the edits to ${fileCount(byHand.send.length)} to Overleaf: ${byHand.send.join(", ")}`;
    return;
  }
  const now = state.overleafGit;
  // The commits git push sends: those Overleaf lacks, and one more of the edits not committed.
  const sending = (now?.ahead ?? 0) + (now?.uncommitted.length ? 1 : 0);
  pull.hidden = !now?.behind;
  // With the files kept the same, Push takes Overleaf's commits in first by itself.
  push.hidden = !sending || (Boolean(now?.behind) && !state.overleafFullSync);
  pull.disabled = push.disabled = false;
  if (!now) return;
  pull.querySelector(".git-count").textContent = now.behind ? String(now.behind) : "";
  push.querySelector(".git-count").textContent = sending ? String(sending) : "";
  pull.title = `git pull ${commits(now.behind)} from Overleaf`;
  push.title = now.uncommitted.length
    ? `Commit the edits to ${fileCount(now.uncommitted.length)} and git push ${commits(sending)} to Overleaf`
    : `git push ${commits(now.ahead)} to Overleaf`;
}

// Where the folder stands, read here without asking Overleaf: what is not committed yet.
async function readOverleafGitState() {
  const response = await fetch("/overleaf/git/state", { method: "POST" });
  if (response.ok) renderOverleafGit(await response.json());
}

// A file changed here, so commit & push shows once there is an edit to commit.
function readOverleafGitStateSoon() {
  if (!state.overleafGit) return;
  clearTimeout(state.overleafGitStateTimer);
  state.overleafGitStateTimer = setTimeout(
    () => readOverleafGitState().catch((error) => console.error(error)), 1000);
}

// commit & push asks first: it lists the files whose edits go into one commit, under a
// message the reviewer may change, and says what git push then sends, for everyone in the
// project.
async function openCommitDialog() {
  await readOverleafGitState();
  const now = state.overleafGit;
  if (!now.ahead && !now.uncommitted.length) return;
  state.committing = now.uncommitted;
  const at = new Date();
  const two = (value) => String(value).padStart(2, "0");
  $("#commit-head").textContent = now.uncommitted.length
    ? `Commit the edits to ${fileCount(now.uncommitted.length)}:` : "No edits to commit.";
  $("#commit-files").replaceChildren(...now.uncommitted.map((file) => h("li", { text: file })));
  $("#commit-message").hidden = !now.uncommitted.length;
  $("#commit-message").required = now.uncommitted.length > 0;
  $("#commit-message").value = `${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())} `
    + `${two(at.getHours())}:${two(at.getMinutes())}`;
  $("#commit-sends").textContent = `Then git push sends ${commits(now.ahead + (now.uncommitted.length ? 1 : 0))} `
    + "to Overleaf, for everyone in the project.";
  $("#commit-dialog").showModal();
}

// Reading Overleaf's branch, or pulling from it or pushing to it, as the reviewer asked.
// A pull or push that does not go through says why in Settings.
async function overleafGitAction(action, commit) {
  if (state.overleafFiles) return overleafFilesAction({ fetch: "read", pull: "get", push: "send" }[action]);
  for (const id of ["#git-pull-btn", "#git-push-btn", "#overleaf-sync-comments-btn"]) $(id).disabled = true;
  if (action !== "fetch") {
    $("#overleaf-git-state").textContent = action === "pull" ? "Running git pull from Overleaf…"
      : commit ? "Committing, then running git push to Overleaf…" : "Running git push to Overleaf…";
  }
  state.overleafGitRead = Date.now();
  const response = await fetch(`/overleaf/git/${action}`, commit
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(commit) }
    : { method: "POST" });
  if (response.ok) {
    renderOverleafGit(await response.json());
    if (action !== "fetch" && $("#settings-dialog").open) {
      $("#overleaf-status").textContent = action === "pull" ? "git pull done." : commit ? "commit & push done." : "git push done.";
    }
    return;
  }
  const why = await responseError(response);
  if (action === "fetch") {
    $("#overleaf-git-state").textContent = `Could not read Overleaf's branch: ${why}`;
    renderGitButtons();
    return;
  }
  showSettings(`Could not ${action}: ${why}`);
  await overleafGitAction("fetch");
}

// Overleaf's branch is read again when the page opens, a little after someone edits on
// Overleaf, and when the reviewer comes back to the page, at most once a minute.
// Reading where the files brought together by hand stand, or a Get or a Send the reviewer asked
// for. One that does not go through says why in Settings.
async function overleafFilesAction(action) {
  const sending = state.overleafFiles?.send ?? [];
  if (action === "send" && !confirm(`Send the edits to ${fileCount(sending.length)} (${sending.join(", ")}) to Overleaf, `
    + "for everyone in the project?")) return;
  for (const id of ["#git-pull-btn", "#git-push-btn", "#overleaf-sync-comments-btn"]) $(id).disabled = true;
  if (action !== "read") $("#overleaf-git-state").textContent = action === "get" ? "Getting Overleaf's edits…" : "Sending to Overleaf…";
  state.overleafGitRead = Date.now();
  const response = await fetch(`/overleaf/files/${action}`, { method: "POST" });
  if (response.ok) {
    renderOverleafFiles(await response.json());
    if (action !== "read" && $("#settings-dialog").open) {
      $("#overleaf-status").textContent = action === "get" ? "Get done." : "Send done.";
    }
    return;
  }
  const why = await responseError(response);
  if (action === "read") {
    $("#overleaf-git-state").textContent = `Could not read Overleaf's files: ${why}`;
    renderGitButtons();
    return;
  }
  showSettings(`Could not ${action === "get" ? "Get" : "Send"}: ${why}`);
  await overleafFilesAction("read");
}

function readOverleafGitSoon(delay) {
  if (!state.overleafGit && !state.overleafFiles) return;
  clearTimeout(state.overleafGitTimer);
  state.overleafGitTimer = setTimeout(() => overleafGitAction("fetch").catch((error) => console.error(error)), delay);
}

// Connecting names the project in the paper's config, as a line written there by hand
// would. Keeping the files the same asks once before it starts, since it changes what
// everyone in the project sees.
const OVERLEAF_SYNC_QUESTION = "The paper's files and the Overleaf project will be kept the same both ways while a "
  + "page is open: edits here are sent to Overleaf, and edits there are written into the files. "
  + "A file that differs at the start waits for your choice. New comment threads on Overleaf come in "
  + "by themselves whenever every file is the same. Figures and other uploaded files are copied whole when one "
  + "side replaces them, and a new figure or source file here goes up by itself, except in a folder cloned by git, "
  + "where they go by git pull and git push as before, and those never put older text over Overleaf's. Turn this on?";

async function connectOverleaf(change, done) {
  const response = await fetch("/overleaf/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(change),
  });
  if (!response.ok) {
    showSettings(`Could not change it: ${await responseError(response)}`);
    await loadOverleafStatus();
    return false;
  }
  renderOverleafStatus(await response.json());
  showSettings(done);
  return true;
}

async function switchOverleafMode(live) {
  if (live && !confirm(OVERLEAF_SYNC_QUESTION)) {
    $('#overleaf-mode input[value="hand"]').checked = true;
    return;
  }
  await connectOverleaf({ sync: live }, live ? "The files now follow Overleaf live." : "The files now follow Overleaf by hand.");
}

async function saveOverleafCookie() {
  const input = $("#overleaf-cookie");
  const response = await fetch("/overleaf/cookie", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cookie: input.value }),
  });
  if (!response.ok) {
    showSettings(`Could not keep it: ${await responseError(response)}`);
    return;
  }
  input.value = "";
  renderOverleafStatus(await response.json());
  showSettings("Cookie saved.");
}

async function forgetOverleafCookie() {
  const response = await fetch("/overleaf/cookie", { method: "DELETE" });
  if (response.ok) renderOverleafStatus(await response.json());
  showSettings(response.ok ? "Cookie forgotten." : `Could not forget it: ${await responseError(response)}`);
}

function overleafBroughtIn(done) {
  const parts = [`${done.added} new comments`, `${done.replies} new replies`];
  if (done.resolved) parts.push(`${done.resolved} resolved threads left out`);
  if (done.unplaced) parts.push(`${done.unplaced} not found in the source, put on the paper`);
  return `Synced: ${parts.join(", ")}.`;
}

// From the comment list it works quietly, and only what did not go through opens the dialog.
async function syncOverleafComments() {
  state.syncingComments = true;
  renderOverleafThreadSync();
  try {
    const response = await fetch("/overleaf/comments", { method: "POST" });
    if (!response.ok) {
      // A cookie Overleaf turned away is asked for again.
      if (response.status === 401) $("#overleaf-cookie-row").hidden = false;
      showSettings(`Could not sync them: ${await responseError(response)}`);
      return;
    }
    const done = overleafBroughtIn(await response.json());
    if ($("#settings-dialog").open) showSettings(done);
    await refreshComments();
  } finally {
    state.syncingComments = false;
    await loadOverleafStatus();
  }
}

// ----- Overleaf: who is in the project -----
// While a page is open the server sits in the paper's Overleaf project, read-only, and
// tells the page who is there, where their cursors are, and which files they changed. A
// face per person in the top bar goes to where they are; a collaborator's cursor in the
// open file is drawn in the editor; a file changed on Overleaf says so above the editor;
// Settings lists all of it.
const overleafLive = { snapshot: null, layer: null, notice: "", settled: false };

function overleafHue(id) {
  let hash = 0;
  for (const char of String(id)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 360;
}

function overleafColor(id) {
  return `hsl(${overleafHue(id)} 62% 42%)`;
}

// The editor marks text by class, not by colour: each author's colour of a suggested insertion
// is a class, written once.
function suggestedInsertClass(id) {
  const hue = overleafHue(id);
  let sheet = document.getElementById("suggested-hues");
  if (!sheet) {
    sheet = h("style", { id: "suggested-hues" });
    document.head.append(sheet);
  }
  if (!sheet.textContent.includes(`.suggested-hue-${hue} `)) {
    sheet.append(`#source-editor .ace_marker-layer .source-suggested-insert.suggested-hue-${hue} `
      + `{ background: hsl(${hue} 62% 42% / 0.14); box-shadow: inset 0 -2px 0 hsl(${hue} 62% 42%); }\n`);
  }
  return `source-suggested-insert suggested-hue-${hue}`;
}

function overleafInitials(name) {
  const words = String(name || "?").trim().split(/\s+/);
  return (words[0][0] + (words.length > 1 ? words.at(-1)[0] : "")).toUpperCase();
}

function overleafWhen(seconds) {
  const ago = Date.now() / 1000 - seconds;
  if (ago < 60) return "just now";
  if (ago < 3600) return `${Math.round(ago / 60)} min ago`;
  return `at ${new Date(seconds * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

function overleafItem(dot, text, where, onClick) {
  const item = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  if (dot) {
    const mark = document.createElement("span");
    mark.className = "overleaf-dot";
    mark.style.background = dot;
    button.append(mark);
  }
  const label = document.createElement("span");
  label.textContent = text;
  const place = document.createElement("span");
  place.className = "where";
  place.textContent = where;
  button.append(label, place);
  button.addEventListener("click", onClick);
  item.append(button);
  return item;
}

// Files kept in step with Overleaf: how many are, and a choice for each that stopped.
const OVERLEAF_CHOICES = {
  differs: [["overleaf", "Use Overleaf's"], ["here", "Send this file's"]],
  stopped: [["overleaf", "Use Overleaf's"], ["here", "Send this file's"]],
  held: [["anyway", "Send it anyway"], ["overleaf", "Put Overleaf's back"]],
  gone: [["delete-there", "Delete on Overleaf"], ["restore", "Get it back"]],
  dropped: [["delete-here", "Delete here"], ["restore", "Send it back"]],
};

// What a file waiting for a choice says, by why it waits.
const OVERLEAF_WAITS = {
  held: "Not sent to Overleaf",
  differs: "Not in step with Overleaf",
  stopped: "Stopped following Overleaf",
  gone: "Deleted here",
  dropped: "Deleted on Overleaf",
};

function overleafChoiceItem(file, withOpen) {
  const item = document.createElement("li");
  item.className = "overleaf-sync-file";
  item.dataset.path = file.path;
  const name = document.createElement("strong");
  name.textContent = withOpen ? `${OVERLEAF_WAITS[file.status] ?? "Waiting"}: ${file.path}` : file.path;
  const why = document.createElement("span");
  why.textContent = file.message;
  item.append(name, why);
  const choices = [...(OVERLEAF_CHOICES[file.status] ?? [])];
  if (withOpen) choices.unshift(["open", "Open the file"]);
  for (const [choice, label] of choices) {
    const choose = document.createElement("button");
    choose.type = "button";
    choose.className = `overleaf-sync-${choice}`;
    choose.textContent = label;
    choose.addEventListener("click", () => (choice === "open"
      ? goToOverleafPlace(file.path, null)
      : chooseOverleafText(file.path, choice, label)).catch(
      (error) => alert(`Could not settle ${file.path}: ${error.message}`)));
    item.append(choose);
  }
  return item;
}

function renderOverleafChoices(files, choosing) {
  const head = $("#overleaf-sync-head");
  const followed = files.filter((file) => file.status !== "unsupported").length;
  head.hidden = !files.length || (Boolean(state.overleafFiles) && !choosing.length);
  head.textContent = state.overleafFiles ? `${choosing.length} file${choosing.length === 1 ? "" : "s"} wait for your choice.`
    : choosing.length
    ? `${choosing.length} file${choosing.length === 1 ? "" : "s"} wait for your choice; the others stay in step with Overleaf.`
    : `${followed} file${followed === 1 ? "" : "s"} kept in step with Overleaf.`;
  $("#overleaf-sync-files").replaceChildren(...choosing.map((file) => overleafChoiceItem(file, false)));
  // A file that waits is said across the top of the page until it is settled, since edits
  // to it go nowhere meanwhile.
  const alertBox = $("#overleaf-sync-alert");
  const wasHidden = alertBox.hidden;
  alertBox.hidden = !choosing.length;
  $("#overleaf-sync-alert-head").textContent = choosing.length === 1
    ? "A file waits for your choice. Edits to it stay here until you choose."
    : `${choosing.length} files wait for your choice. Edits to them stay here until you choose.`;
  $("#overleaf-sync-alert-files").replaceChildren(...choosing.map((file) => overleafChoiceItem(file, true)));
  if (wasHidden !== alertBox.hidden) requestAnimationFrame(() => state.editor?.resize());
}

async function chooseOverleafText(path, choice, label) {
  const asked = {
    overleaf: `${label} for ${path}? This file's text is kept in .tex-mcp-web/overleaf-sync/kept/.`,
    here: `${label} for ${path}? Overleaf's text is kept in .tex-mcp-web/overleaf-sync/kept/.`,
    "delete-there": `Delete ${path} on Overleaf, for everyone in the project? Overleaf keeps it in its history.`,
    "delete-here": `Delete ${path} here? A copy is kept in .tex-mcp-web/overleaf-sync/kept/.`,
  }[choice];
  if (asked && !confirm(asked)) return;
  const response = await fetch("/overleaf/sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, choice }),
  });
  if (!response.ok) throw new Error(await responseError(response));
}

function renderOverleafLive(snapshot) {
  overleafLive.snapshot = snapshot;
  if ((snapshot.me ?? "") !== state.overleafMe) {
    state.overleafMe = snapshot.me ?? "";
    if (["unanswered", "answered"].includes($("#comment-filter").value)) refreshComments().catch((error) => console.error(error));
  }
  if (snapshot.by_hand) renderOverleafFiles(snapshot.by_hand);
  else if (state.overleafFiles) {
    state.overleafFiles = null;
    renderGitButtons();
  }
  const { status, users = [], changes = [] } = snapshot;
  const button = $("#overleaf-live-btn");
  const off = status === "off";
  const commentsTrouble = status === "live" ? snapshot.comments_trouble : "";
  const files = snapshot.files ?? [];
  const choosing = files.filter((file) => ["differs", "stopped", "held", "gone", "dropped"].includes(file.status));
  // The leaf button connects an unconnected paper, and otherwise shows only when something
  // needs a look in the dialog; a session that is up shows faces alone.
  const trouble = !off && (status === "signed_out" || status === "reconnecting" || !!commentsTrouble
    || choosing.length > 0);
  button.classList.toggle("off", off);
  button.classList.toggle("trouble", trouble);
  button.hidden = !off && !trouble;
  button.title = off ? "Connect this paper to its Overleaf project" : "Overleaf: something needs a look";
  renderOverleafChoices(files, choosing);
  renderOverleafThreadSync();
  $("#overleaf-live-label").textContent = off ? "Connect Overleaf"
    : choosing.length ? `${choosing.length} waiting`
    : commentsTrouble ? "Comments not coming in"
    : { signed_out: "Signed out", reconnecting: "Reconnecting" }[status] ?? "";
  $("#overleaf-live").hidden = off;
  $("#overleaf-tracking").hidden = snapshot.tracking !== true;
  // Someone edited on Overleaf: its branch is read again once the typing settles.
  const edited = changes.map((change) => `${change.file}@${change.at}`).join("|");
  if (edited !== state.overleafEdits) {
    state.overleafEdits = edited;
    if (edited) readOverleafGitSoon(15000);
  }
  // The session begins again when files are added, renamed or deleted on Overleaf, and the
  // files kept the same come into step after a choice: the branch is read again then too.
  const settled = status === "live" && files.every((file) => ["synced", "unsupported"].includes(file.status));
  if (settled && !overleafLive.settled) readOverleafGitSoon(2000);
  overleafLive.settled = settled;
  $("#overleaf-live-faces").replaceChildren(...users.slice(0, 4).map((user) => {
    const where = user.file ? `${user.file}:${(user.row ?? 0) + 1}` : "";
    const said = `${user.name || "Someone"} on Overleaf${where ? `, at ${where}` : ""}`;
    return h("button", {
      class: "overleaf-face", type: "button", title: said, "aria-label": said,
      style: { background: overleafColor(user.user_id || user.id) },
      onclick: () => goToOverleafPlace(user.file, user.row).catch((error) => alert(error.message)),
    }, overleafInitials(user.name));
  }));
  $("#overleaf-live-status").textContent = commentsTrouble ? `Overleaf comments are not coming in: ${commentsTrouble}` : {
    live: users.length ? "In the Overleaf project now:" : "No one else is in the Overleaf project.",
    connecting: "Joining the Overleaf project…",
  }[status] ?? (snapshot.message || "Not connected to Overleaf.");
  $("#overleaf-live-users").replaceChildren(...users.map((user) => overleafItem(
    overleafColor(user.user_id || user.id), user.name || "Someone",
    user.file ? `${user.file}:${(user.row ?? 0) + 1}` : "",
    () => goToOverleafPlace(user.file, user.row))));
  $("#overleaf-live-changes-head").hidden = !changes.length;
  $("#overleaf-live-changes").replaceChildren(...changes.map((change) => overleafItem(
    null, change.file, `${change.by}, ${overleafWhen(change.at)}`, () => goToOverleafPlace(change.file, null))));
  renderOverleafCursors();
}

async function goToOverleafPlace(file, row) {
  if (!file) return;
  if ($("#settings-dialog").open) $("#settings-dialog").close();
  if (state.view === "pdf") await setWorkspaceView("split");
  await initializeSourceEditor();
  showSourceFile(file);
  await openSource(file, row === null || row === undefined ? null : row + 1).catch((error) => alert(error.message));
}

// Collaborators' cursors in the open file, drawn over the editor after each render.
function renderOverleafCursors() {
  const editor = state.editor;
  if (!editor) return;
  const container = editor.container;
  if (!overleafLive.layer || overleafLive.layer.parentNode !== container) {
    overleafLive.layer = document.createElement("div");
    overleafLive.layer.className = "overleaf-cursors";
    container.append(overleafLive.layer);
  }
  const snapshot = overleafLive.snapshot ?? { users: [], changes: [] };
  const here = snapshot.users.filter((user) => user.file && user.file === state.sourcePath && user.row !== null);
  const box = container.getBoundingClientRect();
  const height = editor.renderer.lineHeight;
  overleafLive.layer.replaceChildren(...here.flatMap((user) => {
    const row = Math.min(user.row, editor.session.getLength() - 1);
    const column = Math.min(user.column ?? 0, editor.session.getLine(row).length);
    const at = editor.renderer.textToScreenCoordinates(row, column);
    const top = at.pageY - (box.top + window.scrollY);
    const left = at.pageX - (box.left + window.scrollX);
    if (top < -height || top > box.height) return [];
    const color = overleafColor(user.user_id || user.id);
    const caret = document.createElement("div");
    caret.className = "overleaf-cursor";
    caret.dataset.user = user.name;
    Object.assign(caret.style, { top: `${top}px`, left: `${left}px`, height: `${height}px`, background: color });
    const name = document.createElement("span");
    name.className = "overleaf-cursor-name";
    name.style.background = color;
    name.textContent = user.name;
    caret.append(name);
    return [caret];
  }));
  const change = snapshot.changes.find((item) => item.file === state.sourcePath);
  const waiting = (snapshot.files ?? []).find((file) => file.path === state.sourcePath
    && OVERLEAF_WAITS[file.status]);
  const notice = waiting
    ? `${OVERLEAF_WAITS[waiting.status]}: ${waiting.message}`
    : change
    ? `${change.by} edited this file on Overleaf ${overleafWhen(change.at)} (${change.count} ${change.count === 1 ? "edit" : "edits"}). Bring those in before saving here.`
    : "";
  $("#overleaf-file-notice").classList.toggle("waiting", !!waiting);
  if (notice !== overleafLive.notice) {
    overleafLive.notice = notice;
    $("#overleaf-file-notice").textContent = notice;
    $("#overleaf-file-notice").hidden = !notice;
    requestAnimationFrame(() => editor.resize());
  }
}

// The editor's type size, kept for next time. Stepped by a pixel between 10 and 28.
function sourceFontSize() {
  const stored = Number(localStorage.getItem("texMcpSourceFontSize"));
  return stored >= 10 && stored <= 28 ? stored : 13;
}

function stepSourceFontSize(step) {
  const size = Math.min(28, Math.max(10, sourceFontSize() + step));
  localStorage.setItem("texMcpSourceFontSize", String(size));
  state.editor?.setFontSize(`${size}px`);
}

// Long lines wrap unless the viewer turned it off, and Vim keys are off unless the viewer
// turned them on, each kept for next time. With Vim keys, :w saves.
function sourceWraps() {
  return localStorage.getItem("texMcpSourceWrap") !== "0";
}

function applySourceWrap() {
  const on = sourceWraps();
  $("#source-wrap-btn").classList.toggle("active", on);
  $("#source-wrap-btn").setAttribute("aria-pressed", String(on));
  state.editor?.session.setUseWrapMode(on);
}

function vimKeys() {
  return localStorage.getItem("texMcpVimKeys") === "1";
}

function applyVimKeys() {
  const on = vimKeys();
  $("#source-vim-btn").classList.toggle("active", on);
  $("#source-vim-btn").setAttribute("aria-pressed", String(on));
  state.editor?.setKeyboardHandler(on ? "ace/keyboard/vim" : null, () => {
    // Leaving Vim keys keeps Vim's block cursor, under which a click takes the character
    // before the one clicked.
    if (!on) {
      state.editor.renderer.$blockCursor = false;
      state.editor.textInput.getElement().readOnly = false;
      return;
    }
    defineVimCommands(window.ace.require("ace/keyboard/vim").Vim);
    holdKeysOutsideInsert(state.editor);
    syncYanksToClipboard(state.editor);
    keepInputAtCursor(state.editor);
  });
}

// Outside Insert mode Vim moves the editor's hidden input off to the editor's left, out of
// view, and Safari, bringing it into view at each key, made the text shake and flicker as
// the cursor moved. The input stays at the cursor, inside the editor, in every mode. Ace
// keeps it away from the cursor on iOS itself, which a touch screen leaves alone.
function keepInputAtCursor(editor) {
  if (window.matchMedia("(pointer: coarse)").matches) return;
  const keep = () => {
    editor.renderer.$keepTextAreaAtCursor = true;
    editor.renderer.$moveTextAreaToCursor();
  };
  editor.state.cm.on("vim-mode-change", keep);
  keep();
}

// What Vim yanks, deletes or changes goes to the system clipboard too, so it pastes in
// another program. A browser lets a page write the clipboard only on localhost or https.
function syncYanksToClipboard(editor) {
  const Vim = window.ace.require("ace/keyboard/vim").Vim;
  const unnamed = () => Vim.getRegisterController().getRegister('"').toString();
  let last = unnamed();
  // Vim says a command is done as it starts on it, before the text reaches the register.
  editor.state.cm.on("vim-command-done", () => setTimeout(() => {
    const text = unnamed();
    if (text === last) return;
    last = text;
    navigator.clipboard?.writeText(text).catch(() => {});
  }));
}

// macOS answers a letter held down in a text field with its accent menu rather than a
// repeat, so j held to run down the file opened the menu. Outside Insert mode the editor's
// input field is read-only, which the menu leaves alone, so a held key repeats. A touch
// screen keeps the field writable: its on-screen keyboard opens only for one.
function holdKeysOutsideInsert(editor) {
  if (window.matchMedia("(pointer: coarse)").matches) return;
  const cm = editor.state.cm;
  const field = editor.textInput.getElement();
  const sync = () => { field.readOnly = !cm.state.vim.insertMode; };
  cm.on("vim-mode-change", sync);
  sync();
}

// :w saves, and gc comments on what a motion or the visual selection covers, as Vim's
// operators do: gcc the line, gcip the paragraph, gc on a selection the selection.
let vimCommandsDefined = false;

function defineVimCommands(Vim) {
  if (vimCommandsDefined) return;
  vimCommandsDefined = true;
  // jj typed quickly leaves Insert mode, as Escape and Ctrl+[ do.
  Vim.map("jj", "<Esc>", "insert");
  Vim.defineEx("make", "mak", () => {
    compileFromKeys().catch((error) => alert(`Could not compile: ${error.message}`));
  });
  Vim.defineEx("write", "w", () =>
    saveSource().catch((error) => alert(`Could not save: ${error.message}`)));
  // What an operator covers, selected in the editor; its start is where the cursor ends.
  const select = (ranges) => {
    const ends = [ranges[0].anchor, ranges[0].head].sort((a, b) => a.line - b.line || a.ch - b.ch);
    const [start] = ends;
    let [, end] = ends;
    // A line's operator ends at the start of the next line, which is not part of it.
    if (end.ch === 0 && end.line > start.line) {
      end = { line: end.line - 1, ch: state.editor.session.getLine(end.line - 1).length };
    }
    const Range = window.ace.require("ace/range").Range;
    state.editor.selection.setRange(new Range(start.line, start.ch, end.line, end.ch));
    return start;
  };
  Vim.defineOperator("sourceComment", (cm, args, ranges) => {
    const start = select(ranges);
    commentSourceSelection();
    return start;
  });
  Vim.mapCommand("gc", "operator", "sourceComment");
  Vim.defineAction("showCommentAtCursor", () => showCommentAtCursor());
  Vim.mapCommand("K", "action", "showCommentAtCursor", {}, { context: "normal" });
  // gs shows what a motion or the selection covers in the Preview, as → Preview does: gss
  // the line, gsip the paragraph.
  Vim.defineOperator("showInPreview", (cm, args, ranges) => {
    const start = select(ranges);
    showSourceSelectionInPdf().catch((error) => alert(`Could not show it in the Preview: ${error.message}`));
    return start;
  });
  Vim.mapCommand("gs", "operator", "showInPreview");
  Vim.defineAction("goToDefinition", () => {
    goToDefinition().catch((error) => setSourceStatus(error.message, "conflict"));
  });
  Vim.mapCommand("gd", "action", "goToDefinition", {}, { context: "normal" });
  Vim.mapCommand("<C-]>", "action", "goToDefinition", {}, { context: "normal" });
  Vim.defineAction("backFromDefinition", () => {
    backFromDefinition().catch((error) => setSourceStatus(error.message, "conflict"));
  });
  Vim.mapCommand("<C-t>", "action", "backFromDefinition", {}, { context: "normal" });
}

// With Vim keys and an input method on, as with Korean, a key outside Insert mode reaches
// the page as the method's own event and then as the letter it composed, and Vim knew
// neither: the command was lost and the letter went into the text. The key is read by
// where it sits on the keyboard instead, and the letter that follows is dropped.
const COMPOSED = /[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/;

// A letter key as the letter where it sits on the keyboard, whatever an input method made of it.
function keyByPosition(event) {
  const letter = /^Key([A-Z])$/.exec(event.code)?.[1];
  return letter === undefined ? undefined : event.shiftKey ? letter : letter.toLowerCase();
}

function vimCommandMode() {
  const vim = state.editor?.state?.cm?.state.vim;
  return Boolean(state.editor?.$vimModeHandler && vim && !vim.insertMode);
}

function attachVimInputMethod(editor) {
  editor.container.addEventListener("keydown", (event) => {
    const field = editor.textInput.getElement();
    if (!vimCommandMode() || event.target !== field || event.ctrlKey || event.metaKey || event.altKey) return;
    let key = null;
    if (event.isComposing || event.key === "Process" || COMPOSED.test(event.key)) {
      key = keyByPosition(event) ?? null;
    } else if (field.readOnly && event.key.length === 1 && event.key !== " ") {
      // A read-only field (below) takes no typing, so a character key reaches Vim here,
      // where Vim otherwise reads it from the typing. Space is a named key Vim reads itself.
      key = event.key;
    }
    if (key === null) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    window.ace.require("ace/keyboard/vim").Vim.handleKey(editor.state.cm, key, "user");
  }, true);
  // Safari starts a composition at every key while a Korean method is on, and Ace answers
  // one outside Insert mode by cancelling it, which takes the focus off the editor and back:
  // the cursor and selection flickered at each key. The keys are read above, so outside
  // Insert mode the composition never reaches Ace.
  for (const kind of ["compositionstart", "compositionupdate", "compositionend"]) {
    editor.container.addEventListener(kind, (event) => {
      if (vimCommandMode() && event.target === editor.textInput.getElement()) event.stopImmediatePropagation();
    }, true);
  }
  const typed = editor.onTextInput.bind(editor);
  editor.onTextInput = (text, ...rest) => {
    if (vimCommandMode() && COMPOSED.test(text)) return;
    typed(text, ...rest);
  };
}

// ----- Completion -----
// The editor offers what fits where the cursor is, as an editor for LaTeX does: the paper's
// labels after \ref and its kin, its bib entries after \cite and its kin (a title's words
// find them too), environments after \begin and \end, its .tex files after \input, its
// pictures after \includegraphics, and commands after a backslash, the paper's own among
// them. The lists come from the server and are read again after a save or a compile;
// labels in the open file come from the editor, so one just typed is offered at once.
const completion = { data: null, timer: null };

const COMPLETION_WORD = /[^\\{},\s]/;
const ARGUMENT_COMMANDS = {
  ref: /^(ref|cref|Cref|eqref|autoref|pageref|nameref|vref|labelcref|crefrange|Crefrange)$/,
  cite: /^(cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite|autocite|footcite|nocite)$/,
  environment: /^(begin|end)$/,
  input: /^(input|include|subfile|includeonly)$/,
  graphics: /^includegraphics$/,
  bibliography: /^(bibliography|addbibresource)$/,
};

const ENVIRONMENTS = [
  "figure", "figure*", "table", "table*", "tabular", "tabularx", "itemize", "enumerate",
  "description", "equation", "equation*", "align", "align*", "gather", "gather*", "multline",
  "split", "cases", "array", "matrix", "bmatrix", "pmatrix", "center", "minipage", "subfigure",
  "algorithm", "algorithmic", "abstract", "quote", "verbatim", "lstlisting", "theorem", "lemma",
  "proof", "definition", "IEEEkeywords", "thebibliography",
];

// Name and what follows it, in Ace's snippet syntax: $1 is where the cursor goes.
const COMMANDS = [
  ["textbf", "{$1}"], ["textit", "{$1}"], ["emph", "{$1}"], ["texttt", "{$1}"], ["textsc", "{$1}"],
  ["underline", "{$1}"], ["textsuperscript", "{$1}"], ["textsubscript", "{$1}"],
  ["section", "{$1}"], ["section*", "{$1}"], ["subsection", "{$1}"], ["subsubsection", "{$1}"],
  ["paragraph", "{$1}"], ["label", "{$1}"], ["ref", "{$1}"], ["cref", "{$1}"], ["Cref", "{$1}"],
  ["eqref", "{$1}"], ["autoref", "{$1}"], ["cite", "{$1}"], ["citep", "{$1}"], ["citet", "{$1}"],
  ["caption", "{$1}"], ["includegraphics", "[width=\\\\linewidth]{$1}"], ["input", "{$1}"],
  ["include", "{$1}"], ["begin", "{$1}"], ["end", "{$1}"], ["item", ""], ["centering", ""],
  ["footnote", "{$1}"], ["url", "{$1}"], ["href", "{$1}{$2}"], ["hline", ""], ["toprule", ""],
  ["midrule", ""], ["bottomrule", ""], ["cline", "{$1}"], ["multicolumn", "{$1}{$2}{$3}"],
  ["multirow", "{$1}{$2}{$3}"], ["vspace", "{$1}"], ["hspace", "{$1}"], ["noindent", ""],
  ["newline", ""], ["newpage", ""], ["clearpage", ""], ["small", ""], ["footnotesize", ""],
  ["scriptsize", ""], ["normalsize", ""], ["large", ""], ["linewidth", ""], ["columnwidth", ""],
  ["textwidth", ""], ["resizebox", "{$1}{$2}{$3}"], ["frac", "{$1}{$2}"], ["sqrt", "{$1}"],
  ["mathrm", "{$1}"], ["mathbf", "{$1}"], ["mathcal", "{$1}"], ["text", "{$1}"],
  ["operatorname", "{$1}"], ["left", ""], ["right", ""], ["cdot", ""], ["times", ""],
  ["ldots", ""], ["approx", ""], ["leq", ""], ["geq", ""], ["neq", ""], ["sum", ""], ["infty", ""],
  ["alpha", ""], ["beta", ""], ["gamma", ""], ["delta", ""], ["lambda", ""], ["mu", ""],
  ["sigma", ""], ["theta", ""], ["usepackage", "{$1}"], ["newcommand", "{\\\\$1}{$2}"],
  ["bibliography", "{$1}"], ["bibliographystyle", "{$1}"],
];
// Commands whose argument has its own list: the list opens once the command is in.
const REOPENS = new Set(["ref", "cref", "Cref", "eqref", "autoref", "cite", "citep", "citet",
  "includegraphics", "input", "include", "begin", "end", "bibliography"]);

function scheduleCompletions(delay = 800) {
  clearTimeout(completion.timer);
  completion.timer = setTimeout(() => loadCompletions().catch((error) => console.error(error)), delay);
}

async function loadCompletions() {
  const response = await fetch("/completions");
  if (response.ok) completion.data = await response.json();
}

// What the cursor is in: a command's argument (the kind and the command), or a command name.
function completionContext(before) {
  const argument = before.match(/\\([A-Za-z]+)\*?(?:\[[^\]]*\])*\{([^{}]*)$/);
  if (argument) {
    for (const [kind, pattern] of Object.entries(ARGUMENT_COMMANDS)) {
      if (pattern.test(argument[1])) return { kind, command: argument[1] };
    }
    return null;
  }
  return /\\[A-Za-z@]*\*?$/.test(before) ? { kind: "command" } : null;
}

// Environments begun above the cursor and not yet ended, innermost last.
function openEnvironments(editor, row) {
  const open = [];
  const text = editor.session.getLines(0, row).join("\n").replace(/(^|[^\\])%.*$/gm, "$1");
  for (const match of text.matchAll(/\\(begin|end)\{([^{}]+)\}/g)) {
    if (match[1] === "begin") open.push(match[2]);
    else if (open.at(-1) === match[2]) open.pop();
  }
  return open;
}

function bufferLabels(editor) {
  const found = [];
  editor.session.getDocument().getAllLines().forEach((line, row) => {
    for (const match of line.replace(/(^|[^\\])%.*$/, "$1").matchAll(/\\label\s*\{([^{}]*)\}/g)) {
      if (match[1].trim()) found.push({ name: match[1].trim(), file: state.sourcePath, line: row + 1 });
    }
  });
  return found;
}

function completionItems(editor, context, row) {
  const data = completion.data ?? {};
  const item = (fields) => ({ completer: texCompleter, kind: context.kind, ...fields });
  switch (context.kind) {
    case "ref": {
      const labels = (data.labels ?? []).filter((label) => label.file !== state.sourcePath)
        .concat(bufferLabels(editor));
      return labels.map((label) => item({
        caption: label.name, value: label.name, meta: `${label.file.split("/").pop()}:${label.line}`,
        score: label.file === state.sourcePath ? 2 : 1,
      }));
    }
    case "cite":
      return (data.citations ?? []).map((entry) => item({
        caption: entry.title ? `${entry.key}  ${entry.title.slice(0, 48)}` : entry.key,
        value: entry.key, meta: [entry.author, entry.year].filter(Boolean).join(" "),
        docText: [entry.title, [entry.author, entry.year].filter(Boolean).join(", ")].filter(Boolean).join("\n"),
        score: 1,
      }));
    case "environment": {
      const names = [...new Set([...(data.environments ?? []), ...ENVIRONMENTS])];
      const open = context.command === "end" ? openEnvironments(editor, row) : [];
      return names.map((name) => item({
        caption: name, value: name, meta: "environment", begin: context.command === "begin",
        score: open.length && name === open.at(-1) ? 3 : 1,
      }));
    }
    case "input":
      return (data.tex_files ?? []).map((path) => item({
        caption: path.replace(/\.tex$/, ""), value: path.replace(/\.tex$/, ""), meta: "file", score: 1,
      }));
    case "graphics":
      return (data.graphics ?? []).map((path) => item({ caption: path, value: path, meta: "picture", score: 1 }));
    case "bibliography":
      return (data.bib_files ?? []).map((path) => item({
        caption: path.replace(/\.bib$/, ""), value: path.replace(/\.bib$/, ""), meta: "bib", score: 1,
      }));
    case "command": {
      const own = (data.commands ?? []).map((command) => [command.name,
        Array.from({ length: command.args }, (_, index) => `{$${index + 1}}`).join(""), "paper"]);
      const seen = new Set(own.map(([name]) => name));
      return own.concat(COMMANDS.filter(([name]) => !seen.has(name)).map(([name, rest]) => [name, rest, "command"]))
        .map(([name, rest, meta]) => item({
          caption: `\\${name}`, value: name, rest, meta, score: meta === "paper" ? 2 : 1,
        }));
    }
    default:
      return [];
  }
}

const texCompleter = {
  id: "tex",
  identifierRegexps: [COMPLETION_WORD],
  triggerCharacters: ["\\", "{", ","],
  getCompletions(editor, session, pos, prefix, callback) {
    const context = completionContext(session.getLine(pos.row).slice(0, pos.column));
    callback(null, context === null ? [] : completionItems(editor, context, pos.row));
  },
  // The typed part of the word goes, and so does the rest of the word after the cursor.
  // An argument gets its closing brace unless one follows, and \begin{name} gets its
  // \end{name} with the cursor on the line between.
  insertMatch(editor, data) {
    const { row, column } = editor.getCursorPosition();
    const line = editor.session.getLine(row);
    let start = column;
    while (start > 0 && COMPLETION_WORD.test(line[start - 1])) start -= 1;
    let end = column;
    while (end < line.length && COMPLETION_WORD.test(line[end])) end += 1;
    const Range = window.ace.require("ace/range").Range;
    const { snippetManager } = window.ace.require("ace/snippets");
    const escape = (text) => text.replace(/[\\$]/g, "\\$&");   // a bare } is plain text here
    const closed = line[end] === "}";
    if (data.kind === "environment" && data.begin && closed) end += 1;
    editor.session.remove(new Range(row, start, row, end));
    editor.moveCursorTo(row, start);
    let snippet;
    if (data.kind === "command") snippet = escape(data.value) + data.rest;
    else if (data.kind === "environment" && data.begin) {
      snippet = `${escape(data.value)}}\n\t$0\n\\\\end{${escape(data.value)}}`;
    } else snippet = escape(data.value) + (closed || line[end] === "," ? "" : "}");
    snippetManager.insertSnippet(editor, snippet);
    if (data.kind === "command" && REOPENS.has(data.value)) {
      setTimeout(() => editor.execCommand("startAutocomplete"), 0);
    }
  },
};

function attachCompletion(editor) {
  window.ace.require("ace/ext/language_tools");
  editor.completers = [texCompleter];
  editor.setOptions({ enableBasicAutocompletion: [texCompleter], enableLiveAutocompletion: [texCompleter] });
  // Enter takes a choice only when it changes the text: after \item typed whole, Enter
  // starts the next line.
  const { Autocomplete } = window.ace.require("ace/autocomplete");
  const completer = Autocomplete.for(editor);
  completer.keyboardHandler.bindKey("Return", (target) => {
    const chosen = completer.popup?.getData(completer.popup.getRow());
    if (!chosen || chosen.value === (completer.completions?.filterText ?? "")) {
      completer.detach();
      return false;
    }
    return completer.insertMatch();
  });
  // A brace, comma or backslash starts a new list where one is open: Ace would only
  // narrow the open one, \label's for instance, by "label{" and close it.
  editor.commands.on("afterExec", (event) => {
    if (event.command.name !== "insertstring" || !texCompleter.triggerCharacters.includes(event.args)) return;
    if (!completer.activated) return;
    completer.detach();
    completer.autoShown = true;
    completer.showPopup(editor);
  });
  scheduleCompletions(0);
}

// gd with Vim keys, or F12, goes from what the cursor is on to where it is defined: from
// \input (or \include, \subfile) to that file, from \ref and its kin to the \label it
// names, from \cite and its kin to the entry in a .bib file. In a list such as
// \cite{a,b} it is the name under the cursor. The open file is searched first.
const DEFINED = /\\(input|include|subfile|ref|cref|Cref|eqref|autoref|pageref|nameref|cite|citep|citet|parencite|textcite)\*?(?:\[[^\]]*\])*\{([^}]*)\}/g;

// Each jump to a definition leaves its starting place here, and Ctrl+T or Ctrl+O with Vim
// keys, or Shift+F12, goes back to the last one, as Vim's tag stack does.
const definitionTrail = [];

// Windows browsers keep Ctrl+T for a new tab, so with Vim keys Ctrl+O, Vim's way back to
// the last place, goes back from a definition too. With no such jump left it is Vim's own.
function attachDefinitionBack(editor) {
  editor.container.addEventListener("keydown", (event) => {
    if (!vimCommandMode() || definitionTrail.length === 0 || event.code !== "KeyO"
      || !event.ctrlKey || event.shiftKey || event.altKey || event.metaKey) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    backFromDefinition().catch((error) => setSourceStatus(error.message, "conflict"));
  }, true);
}

async function backFromDefinition() {
  const back = definitionTrail.pop();
  if (back === undefined) {
    setSourceStatus("No jump to go back from");
    return;
  }
  if (await openSource(back.path, back.row + 1)) state.editor.moveCursorTo(back.row, back.column);
}

async function jumpTo(path, line, from) {
  if (await openSource(path, line)) definitionTrail.push(from);
}

async function goToDefinition() {
  const editor = state.editor;
  if (!editor) return;
  const { row, column } = editor.getCursorPosition();
  const line = editor.session.getLine(row);
  const match = [...line.matchAll(DEFINED)].find((found) =>
    column >= found.index && column <= found.index + found[0].length);
  if (match === undefined) {
    setSourceStatus("No \\input, \\ref or \\cite under the cursor");
    return;
  }
  const [whole, command, argument] = match;
  let at = match.index + whole.length - argument.length - 1;
  let name = argument.trim();
  for (const part of argument.split(",")) {
    name = part.trim();
    if (column <= at + part.length) break;
    at += part.length + 1;
  }
  const files = Array.from($("#source-file").options, (option) => option.value);
  if (["input", "include", "subfile"].includes(command)) {
    const file = [name, `${name}.tex`].find((path) => files.includes(path));
    if (file === undefined) setSourceStatus(`${name} is not among the source files`);
    else await jumpTo(file, 1, { path: state.sourcePath, row, column });
    return;
  }
  const cited = command.includes("cite");
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = cited ? new RegExp(`@\\w+\\s*\\{\\s*${escaped}\\s*,`) : new RegExp(`\\\\label\\{\\s*${escaped}\\s*\\}`);
  const candidates = files.filter((path) => path.endsWith(cited ? ".bib" : ".tex"))
    .sort((a, b) => (b === state.sourcePath) - (a === state.sourcePath));
  for (const path of candidates) {
    const text = path === state.sourcePath
      ? editor.getValue()
      : (await (await fetch(`/source?${new URLSearchParams({ path })}`)).json()).text;
    const index = text?.search(pattern) ?? -1;
    if (index >= 0) {
      await jumpTo(path, text.slice(0, index).split("\n").length, { path: state.sourcePath, row, column });
      return;
    }
  }
  setSourceStatus(`No ${cited ? "bib entry" : "\\label"} for ${name}`);
}

// K with Vim keys, or Ctrl+Alt+K (Cmd+Option+K), opens in the comments the comment the
// cursor is on, as a click on its mark beside the line does: the narrowest when several
// cover the cursor, else one on the cursor's line. The comment keys then act on it, and K there
// brings the cursor back to its words. Off any comment, the keys go to the list as it was.
function showCommentAtCursor() {
  const { row, column } = state.editor.getCursorPosition();
  const size = ({ range }) => (range.end.row - range.start.row) * 1e6 + range.end.column - range.start.column;
  const covering = state.sourceCommentSpans.filter(({ range }) => range.contains(row, column)).sort((a, b) => size(a) - size(b));
  const chosen = covering[0]?.comment ?? state.sourceCommentsByRow.get(row)?.[0];
  setSidebarCollapsed(false);
  state.editor.blur();
  if (chosen?.suggested) {
    openSuggestedEdit(chosen.id).catch((error) => console.error(error));
    return;
  }
  switchTab(state.commentTab);
  if (chosen !== undefined) focusComment(chosen);
  $("#comments-list").focus({ preventScroll: true });
}

// A suggested edit opened from the source: the leaf tab's suggested edits view, ringed there.
async function openSuggestedEdit(id) {
  switchTab("overleaf-comments");
  setCommentFilter("changes");
  $("#comments-list").focus({ preventScroll: true });
  await refreshComments();
  const item = state.comments.find((comment) => comment.id === id);
  if (item) focusComment(item);
}

// Ctrl+Alt+M (Cmd+Option+M), or gc with Vim keys. Comments wait for the file to be saved,
// as the button does, and the bar says so.
function commentSourceSelection() {
  if (state.sourceDirty) {
    setSourceStatus("Save before commenting", "dirty");
    return;
  }
  openSourceCommentCompose();
}

async function initializeSourceEditor() {
  if (state.editor) return;
  if (!window.ace) throw new Error("Ace Editor did not load");
  window.ace.config.set("basePath", "/static/ace");
  state.editor = window.ace.edit("source-editor");
  state.editor.setTheme(editorTheme());
  // Ace restarts its cursor's blinking animation at every move, and Safari redrew the whole
  // editor each time, so the text flickered as the cursor moved. In Safari the cursor holds
  // steady instead. Every browser on an iPhone or iPad is Safari underneath.
  if (/iPhone|iPad|iPod/.test(navigator.userAgent)
    || /^((?!chrome|chromium|android|edg).)*safari/i.test(navigator.userAgent)) {
    state.editor.container.classList.add("steady-cursor");
  }
  state.editor.session.setMode("ace/mode/latex");
  state.editor.session.setUseWorker(false);
  state.editor.session.setUseWrapMode(sourceWraps());
  state.editor.setOptions({
    fontSize: `${sourceFontSize()}px`,
    showPrintMargin: false,
    scrollPastEnd: 0.25,
  });
  state.editor.on("change", () => {
    if (!state.sourceLoading) setSourceDirty(true);
  });
  state.editor.on("changeSelection", updateSourceCommentButton);
  state.editor.selection.on("changeCursor", followSourceCursor);
  state.editor.on("guttermousedown", (event) => {
    const row = event.getDocumentPosition().row;
    // A suggested edit's mark beside its line opens it in the leaf tab.
    const edit = state.overleafSuggested.find((item) => item.file === state.sourcePath && item.here?.line_start === row + 1);
    if (edit && event.domEvent.target.classList.contains("source-suggested-line")
        && !event.domEvent.target.classList.contains("source-comment-line")) {
      event.stop();
      setSidebarCollapsed(false);
      openSuggestedEdit(edit.id).catch((error) => console.error(error));
      return;
    }
    const comments = state.sourceCommentsByRow.get(row);
    if (!comments?.length || !event.domEvent.target.classList.contains("source-comment-line")) return;
    event.stop();
    setSidebarCollapsed(false);
    switchTab(state.commentTab);
    focusComment(comments[0]);
  });
  state.editor.commands.addCommand({
    name: "saveSource",
    bindKey: { win: "Ctrl-S", mac: "Command-S" },
    exec: () => saveSource().catch((error) => alert(`Could not save: ${error.message}`)),
  });
  state.editor.commands.addCommand({
    name: "backFromDefinition",
    bindKey: { win: "Shift-F12", mac: "Shift-F12" },
    exec: () => backFromDefinition().catch((error) => setSourceStatus(error.message, "conflict")),
  });
  state.editor.commands.addCommand({
    name: "goToDefinition",
    bindKey: { win: "F12", mac: "F12" },
    exec: () => goToDefinition().catch((error) => setSourceStatus(error.message, "conflict")),
  });
  state.editor.commands.addCommand({
    name: "showInPreview",
    bindKey: { win: "Ctrl-Alt-P", mac: "Command-Alt-P" },
    exec: () => showSourceSelectionInPdf().catch((error) => alert(`Could not show it in the Preview: ${error.message}`)),
  });
  state.editor.commands.addCommand({
    name: "showCommentAtCursor",
    bindKey: { win: "Ctrl-Alt-K", mac: "Command-Alt-K" },
    exec: () => showCommentAtCursor(),
  });
  state.editor.commands.addCommand({
    name: "commentSource",
    bindKey: { win: "Ctrl-Alt-M", mac: "Command-Alt-M" },
    exec: commentSourceSelection,
  });
  attachVimInputMethod(state.editor);
  attachDefinitionBack(state.editor);
  attachCompletion(state.editor);
  state.editor.renderer.on("afterRender", renderOverleafCursors);
  applyVimKeys();
  await refreshSourceFiles();
}

async function saveSource() {
  if (!state.editor || !state.sourcePath || !state.sourceDirty) return;
  const button = $("#source-save-btn");
  button.disabled = true;
  setSourceStatus("Saving…");
  const response = await fetch(`/source?${new URLSearchParams({ path: state.sourcePath })}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: state.editor.getValue(),
      revision: state.sourceRevision,
    }),
  });
  if (response.status === 409) {
    setSourceStatus("Changed on disk · reload", "conflict");
    button.disabled = false;
    return;
  }
  if (!response.ok) {
    button.disabled = false;
    setSourceStatus("Save failed", "conflict");
    throw new Error(await responseError(response));
  }
  const result = await response.json();
  if (typeof result.text === "string" && result.text !== state.editor.getValue()) {
    // Brought together with edits made on Overleaf meanwhile: the editor takes the result
    // where it stands.
    const cursor = state.editor.getCursorPosition();
    const top = state.editor.session.getScrollTop();
    state.sourceLoading = true;
    state.editor.session.doc.setValue(result.text);
    state.sourceLoading = false;
    state.editor.moveCursorToPosition(cursor);
    state.editor.clearSelection();
    state.editor.session.setScrollTop(top);
  }
  state.sourceRevision = result.revision;
  state.sourceDirty = false;
  button.disabled = true;
  setSourceStatus(typeof result.text === "string" ? "Saved with Overleaf's edits" : "Saved");
  scheduleCompletions(300);
  await refreshComments();
  updateSourceCommentButton();
}

async function reloadSource() {
  if (!state.sourcePath) return;
  if (state.sourceDirty && !confirm(`Discard unsaved changes to ${state.sourcePath}?`)) return;
  const line = state.editor.getCursorPosition().row + 1;
  await openSource(state.sourcePath, line, true);
}

async function setWorkspaceView(view) {
  const zoomMode = state.zoom?.getState().zoomLevel;
  state.view = view;
  const layout = $(".layout");
  layout.classList.remove("view-pdf", "view-source", "view-split");
  layout.classList.add(`view-${view}`);
  for (const button of $$(".view-tab")) {
    button.classList.toggle("active", button.dataset.view === view);
  }
  localStorage.setItem("workspaceView", view);
  applyLayout(false);
  if (view !== "pdf") await initializeSourceEditor();
  requestAnimationFrame(() => {
    state.editor?.resize();
    if (typeof zoomMode === "string") state.zoom?.requestZoom(zoomMode);
  });
}

async function handleSourceChanged(message) {
  if (!state.editor || message.path !== state.sourcePath
      || message.revision === state.sourceRevision) return;
  if (state.sourceDirty) {
    const followed = (overleafLive.snapshot?.files ?? []).some((file) => file.path === message.path);
    setSourceStatus(followed ? "Edited on Overleaf · saving takes both" : "Changed on disk · reload",
      followed ? "" : "conflict");
    return;
  }
  const line = state.editor.getCursorPosition().row + 1;
  // A file changed under the reader leaves the keys where they are, in a reply being written
  // or with the comment list.
  const focused = state.editor.isFocused();
  await refreshComments();
  await openSource(state.sourcePath, line, true, focused);
}

// What sends a comment, as Settings chooses: shift with enter, Enter writing a new line as
// in any box of text, or Enter, shift with it writing the new line. The command or control
// key with enter sends either way. The choice is kept for next time.
function enterSends() {
  return state.sendKey === "enter";
}

function applySendKey() {
  const key = enterSends() ? "enter" : "shift";
  for (const button of document.querySelectorAll("[data-send-choice]")) {
    button.setAttribute("aria-checked", String(button.dataset.sendChoice === key));
  }
}

function sendOn(input, send) {
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.altKey) return;
    if (!(event.metaKey || event.ctrlKey || (enterSends() ? !event.shiftKey : event.shiftKey))) return;
    // An Enter that ends a word an input method is still composing, as Korean is typed,
    // comes before the box holds that word, which is sent once composing ends.
    if (event.isComposing) {
      input.addEventListener("compositionend", () => setTimeout(send), { once: true });
      return;
    }
    event.preventDefault();
    send();
  });
}

function applyComposeSubmitting(submitting) {
  state.composeSubmitting = submitting;
  const dialog = $("#compose-dialog");
  dialog.setAttribute("aria-busy", String(submitting));
  for (const control of $$("#compose-form textarea, #compose-form button")) {
    control.disabled = submitting;
  }
  $("#compose-submit").textContent = submitting ? "Posting…" : "Post";
}

async function submitCompose(event) {
  event.preventDefault();
  if (state.composeSubmitting) return;
  const text = $("#compose-text").value.trim();
  if (!text || !state.pendingAnchor) return;
  const body = { anchor: state.pendingAnchor, text };
  if (state.pendingSourceRevision !== null) body.source_revision = state.pendingSourceRevision;
  if (state.pendingHeading !== null) body.heading = state.pendingHeading;
  const suggestionOld = $("#compose-suggestion-old").value.trim();
  const suggestionNew = $("#compose-suggestion-new").value.trim();
  if (suggestionOld && suggestionNew) body.suggestion = { old: suggestionOld, new: suggestionNew };

  applyComposeSubmitting(true);
  try {
    const response = await fetch("/comments", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      alert(`Could not save comment: ${await responseError(response)}`);
      return;
    }
    $("#compose-dialog").close();
    clearPendingSelection();
    if (state.commentTab !== "comments") switchTab("comments");
    await refreshComments();
  } finally {
    applyComposeSubmitting(false);
  }
}

async function refreshComments() {
  // A list asked for again while one is on its way, as when the view changes, is the one that
  // stands: an answer that comes back after a later one is dropped.
  const asked = ++state.commentsAsked;
  const status = $("#comment-filter").value;
  const url = status === "all" ? "/comments"
    : `/comments?status=${["unanswered", "answered", "new", "changes"].includes(status) ? "open" : status}`;
  const [response, overleaf] = await Promise.all([fetch(url), fetch("/overleaf")]);
  if (!response.ok) throw new Error(await responseError(response));
  const data = await response.json();
  // Which threads came from Overleaf, and so can be answered there.
  const linked = overleaf.ok ? await overleaf.json() : {};
  if (asked !== state.commentsAsked) return;
  state.overleafImported = new Set(linked.imported ?? []);
  state.overleafUnplaced = linked.unplaced ?? {};
  state.overleafSignedIn = Boolean(linked.cookie);
  state.overleafProject = linked.project ?? null;
  state.overleafFullSync = Boolean(linked.live);
  state.overleafWaiting = linked.waiting ?? 0;
  state.overleafSuggested = linked.suggested ?? [];
  // The other tab's comments are left out here, once, so the list, the editor's marks and
  // the PDF's highlights agree without each asking. Each tab counts its own open ones, and the
  // leaf tab the suggested edits waiting too.
  const opened = (comments) => comments.filter((comment) => comment.status === "open").length;
  const onOverleaf = data.comments.filter(forOverleaf);
  const here = data.comments.filter((comment) => !forOverleaf(comment));
  renderCommentTabs(opened(here), opened(onOverleaf) + state.overleafSuggested.length);
  data.comments = state.commentTab !== "overleaf-comments" ? here
    : status === "unanswered" ? onOverleaf.filter(answeredByOthers)
    : status === "answered" ? onOverleaf.filter(answeredByMe)
    : status === "new" ? [...onOverleaf.filter(draftedThread), ...onOverleaf.filter(openedHere)]
    : status === "changes" ? state.overleafSuggested.map(suggestedItem)
    : onOverleaf;
  renderOverleafThreadSync();
  // The open view, and the unanswered and answered ones, keep the server's order: the
  // comments written here in the order they were written, and Overleaf's threads in the
  // order the paper reads them, as Overleaf's review panel has them. The new-thread view puts the drafts still to send above the
  // threads already opened from them, each in the paper's order. The resolved view leads
  // with the comment closed last, the one the reader has just closed and looks for. The
  // mixed view leads with the one written last, by when it was written and not by when it
  // was last touched: closing a comment touches it, so by that measure every closed comment
  // stood above every open one.
  // The archive view leads with the thread touched last: what was kept to be read
  // again is read again when something is added to it.
  const moment = status === "resolved"
    ? (comment) => comment.resolved ?? comment.created
    : status === "archived" ? (comment) => comment.updated
    : (comment) => comment.created;
  const before = state.comments;
  state.comments = ["open", "unanswered", "answered", "new", "changes"].includes(status) ? data.comments
    : [...data.comments].sort((first, second) => (moment(first) < moment(second) ? 1
      : moment(first) > moment(second) ? -1 : 0));
  // A focused comment that leaves the view, as one resolved in the open view does, hands the
  // focus to the comment now in its place, so the keys go on from there.
  const view = `${state.commentTab} ${status}`;
  const at = before.findIndex((comment) => comment.id === state.focusedCommentId);
  if (view === state.commentsView && at !== -1 && state.comments.length > 0
      && !state.comments.some((comment) => comment.id === state.focusedCommentId)) {
    state.focusedCommentId = state.comments[Math.min(at, state.comments.length - 1)].id;
  }
  state.commentsView = view;
  renderComments();
  syncCommentAnnotations();
  syncSourceCommentMarkers();
}

// A suggested edit as the list holds it, beside the comments: open until accepted or rejected,
// with nothing said in it, and its place in the source, or its line on Overleaf when the text
// around it is not in the file here.
function suggestedItem(edit) {
  const place = edit.here ?? { line_start: edit.line, line_end: edit.line, column_start: 0, column_end: 0 };
  return { ...edit, suggested: true, status: "open", thread: [], anchor: { kind: "suggested" },
           resolved_source: { file: edit.file, ...place } };
}

// A thread from Overleaf, or one drafted for it: a comment with no entries, since nothing
// has been said in it until the reviewer sends its draft.
function draftedThread(comment) {
  return comment.thread.length === 0 && Boolean(comment.overleaf_draft);
}

// A thread opened on Overleaf from here: its first message is the draft the reviewer sent.
function openedHere(comment) {
  return comment.thread[0]?.overleaf !== undefined;
}

function forOverleaf(comment) {
  return state.overleafImported.has(comment.id) || draftedThread(comment);
}

// A thread from Overleaf whose last message there is someone else's: one sent from here,
// or written there under the reviewer's own name, is the reviewer's answer. Entries that
// stay here are not on Overleaf, and a thread drafted for it has nothing said in it yet.
function answeredByOthers(comment) {
  const last = lastOnOverleaf(comment);
  return last !== undefined && last.overleaf === undefined && last.name !== state.overleafMe;
}

// A thread from Overleaf whose last message there is the reviewer's own.
function answeredByMe(comment) {
  return lastOnOverleaf(comment) !== undefined && !answeredByOthers(comment);
}

function lastOnOverleaf(comment) {
  return comment.thread.filter((entry) => entry.name !== undefined || entry.overleaf !== undefined).at(-1);
}

// The leaf tab shows once the paper names an Overleaf project or has threads from one.
function renderCommentTabs(here, onOverleaf) {
  const leafTab = $('.tab-btn[data-tab="overleaf-comments"]');
  leafTab.hidden = !state.overleafProject && state.overleafImported.size === 0;
  if (leafTab.hidden && state.commentTab === "overleaf-comments") {
    state.commentTab = "comments";
    if (leafTab.classList.contains("active")) switchTab("comments");
  }
  for (const [badge, count] of [[$("#comments-tab-count"), here], [$("#overleaf-tab-count"), onOverleaf]]) {
    badge.textContent = String(count);
    badge.classList.toggle("hidden", count === 0);
  }
  renderCommentFilter();
}

// The views each comment tab's list offers, as [value, name]. Overleaf has no archive, and only
// its threads wait for an answer, have one, or are opened from here.
const COMMENT_VIEWS = {
  comments: [["open", "open"], ["resolved", "resolved"], ["archived", "archived"], ["all", "all"]],
  "overleaf-comments": [["unanswered", "unanswered"], ["answered", "answered"], ["new", "new thread"],
    ["changes", "suggested edits"], ["open", "open"], ["resolved", "resolved"], ["all", "all"]],
};

// The row above the list holds what the shown tab can do, from the moment the tab is chosen.
// The list holds the tab's views and no others, since Safari still lists a hidden option.
function renderCommentFilter() {
  const filter = $("#comment-filter");
  const views = COMMENT_VIEWS[state.commentTab];
  if ([...filter.options].map((option) => option.value).join(" ") !== views.map(([value]) => value).join(" ")) {
    const chosen = filter.value;
    filter.replaceChildren(...views.map(([value, name]) => h("option", { value, text: name })));
    setCommentFilter(views.some(([value]) => value === chosen) ? chosen : "open");
  }
  // The suggested edits' view says how many wait.
  const changes = [...filter.options].find((option) => option.value === "changes");
  if (changes) {
    const count = state.overleafSuggested.length;
    changes.textContent = count ? `suggested edits (${count})` : "suggested edits";
    if (filter.value === "changes") $("#comment-filter-name").textContent = changes.textContent;
  }
  $("#archive-picked-btn").hidden = state.commentTab === "overleaf-comments";
}

function renderComments() {
  const list = $("#comments-list");
  const scrollTop = list.scrollTop;
  const activeInput = document.activeElement?.classList.contains("cmt-form-input")
    ? document.activeElement
    : null;
  const inputState = activeInput ? {
    commentId: activeInput.closest(".cmt")?.dataset.commentId,
    box: boxOf(activeInput),
    selectionStart: activeInput.selectionStart,
    selectionEnd: activeInput.selectionEnd,
    selectionDirection: activeInput.selectionDirection,
    scrollTop: activeInput.scrollTop,
  } : null;
  clear(list);
  if (state.comments.length === 0) {
    list.appendChild(placeholder($("#comment-filter").value === "changes" && state.commentTab === "overleaf-comments"
      ? "No suggested edits on Overleaf." : "No comments at this filter."));
  } else {
    for (const comment of state.comments) {
      list.appendChild(comment.suggested ? renderSuggestedEdit(comment) : renderCommentItem(comment));
    }
  }
  list.scrollTop = scrollTop;
  if (inputState?.commentId) {
    const commentNode = Array.from(list.children).find(
      (node) => node.dataset.commentId === inputState.commentId,
    );
    const input = commentNode?.querySelector(inputState.box);
    if (input) {
      input.focus({ preventScroll: true });
      input.setSelectionRange(
        inputState.selectionStart,
        inputState.selectionEnd,
        inputState.selectionDirection,
      );
      input.scrollTop = inputState.scrollTop;
    }
  }
  renderPickedActions();
  renderFoldAction();
  markKeys();
}

// What the picked comments can be sent to, which is decided by the state they are in:
// open ones close, unarchived ones archive, and closed ones reopen. The counts say
// exactly what each press will touch.
function renderPickedActions() {
  // A thread from Overleaf resolves and reopens there too. Overleaf has no archive. A thread
  // drafted for Overleaf is only sent or deleted.
  const picked = state.comments.filter((comment) => state.picked.has(comment.id) && !draftedThread(comment)
    && !comment.suggested);
  const open = picked.filter((comment) => comment.status === "open").map((comment) => comment.id);
  const archivable = picked.filter((comment) => comment.status !== "archived"
    && !state.overleafImported.has(comment.id)).map((comment) => comment.id);
  const closed = picked.filter((comment) => comment.status !== "open").map((comment) => comment.id);
  const shown = state.comments.length;
  const all = $("#pick-all-btn");
  all.disabled = shown === 0;
  const clearing = shown > 0 && state.comments.every((comment) => state.picked.has(comment.id));
  all.querySelector(".icon-select").classList.toggle("hidden", clearing);
  all.querySelector(".icon-clear").classList.toggle("hidden", !clearing);
  all.setAttribute("aria-label", clearing ? "Clear" : "Select all");
  all.title = clearing ? "Let go of every picked comment" : "Pick every comment in this view";
  // A button with nothing to do stays in place, greyed out: buttons that came and went
  // moved everything beside them. The count shows only when there is one.
  const resolve = $("#resolve-picked-btn");
  resolve.disabled = open.length === 0;
  resolve.querySelector(".count").textContent = open.length > 0 ? String(open.length) : "";
  resolve.setAttribute("aria-label", open.length > 0 ? `Resolve ${open.length}` : "Resolve");
  resolve.dataset.ids = open.join(" ");
  const archive = $("#archive-picked-btn");
  archive.disabled = archivable.length === 0;
  archive.querySelector(".count").textContent = archivable.length > 0 ? String(archivable.length) : "";
  archive.setAttribute("aria-label", archivable.length > 0 ? `Archive ${archivable.length}` : "Archive");
  archive.dataset.ids = archivable.join(" ");
  const reopen = $("#reopen-picked-btn");
  reopen.disabled = closed.length === 0;
  reopen.querySelector(".count").textContent = closed.length > 0 ? String(closed.length) : "";
  reopen.setAttribute("aria-label", closed.length > 0 ? `Reopen ${closed.length}` : "Reopen");
  reopen.dataset.ids = closed.join(" ");
  // A picked thread from Overleaf sends what its reply box holds, each to its own thread there.
  const sendable = state.comments.filter((comment) => state.picked.has(comment.id)
    && forOverleaf(comment) && comment.status === "open"
    && overleafReplyText(comment).trim()).map((comment) => comment.id);
  const send = $("#send-picked-btn");
  send.hidden = !state.overleafProject || state.commentTab !== "overleaf-comments";
  send.disabled = !state.overleafSignedIn || sendable.length === 0 || state.sending.size > 0;
  send.classList.toggle("busy", state.sending.size > 0);
  send.querySelector(".count").textContent = sendable.length > 0 ? String(sendable.length) : "";
  send.setAttribute("aria-label", sendable.length > 0 ? `Send ${replies(sendable.length)} to Overleaf` : "Send to Overleaf");
  send.title = state.overleafSignedIn ? "Send the picked replies to Overleaf, for everyone in the project"
    : "Save your Overleaf cookie first";
  send.dataset.ids = sendable.join(" ");
  // The suggested edits' view accepts or rejects the picked ones in place of resolving and sending.
  const suggesting = state.commentTab === "overleaf-comments" && $("#comment-filter").value === "changes";
  const edits = state.comments.filter((item) => item.suggested && state.picked.has(item.id));
  resolve.hidden = suggesting;
  send.hidden = send.hidden || suggesting;
  for (const [id, word] of [["#accept-picked-btn", "Accept"], ["#reject-picked-btn", "Reject"]]) {
    const button = $(id);
    button.hidden = !suggesting;
    button.disabled = edits.length === 0 || state.reviewingEdits.size > 0;
    button.classList.toggle("busy", edits.some((item) => state.reviewingEdits.has(item.id)));
    button.querySelector(".count").textContent = edits.length > 0 ? String(edits.length) : "";
    button.setAttribute("aria-label", edits.length > 0 ? `${word} ${edits.length}` : word);
  }
  fitCommentRow();
}

// The less pressed buttons stay in the row while it has room for every button, and go
// behind the dots once the row would spill over.
function fitCommentRow() {
  const row = $(".filter-row");
  row.classList.remove("narrow", "wrapped");
  const over = () => row.scrollWidth > row.clientWidth;
  const narrow = over();
  row.classList.toggle("narrow", narrow);
  row.classList.toggle("wrapped", narrow && over());
  if (!narrow) closeCommentRare();
}

function closeCommentRare() {
  $("#comment-rare").classList.remove("open");
  $("#comment-rare-btn").setAttribute("aria-expanded", "false");
}

// Every card in the view opened, or every one closed: which of the two the button does
// is read off the cards, so it always offers the one that changes something.
function renderFoldAction() {
  const shown = state.comments.map((comment) => comment.id);
  const button = $("#fold-all-btn");
  button.disabled = shown.length === 0;
  const folding = shown.length > 0 && shown.every((id) => state.expanded.has(id));
  button.querySelector(".icon-expand").classList.toggle("hidden", folding);
  button.querySelector(".icon-collapse").classList.toggle("hidden", !folding);
  button.setAttribute("aria-label", folding ? "Collapse all" : "Expand all");
  button.title = folding ? "Close every comment in this view" : "Open every comment in this view";
}

function foldAll() {
  foldEvery(!state.comments.every((comment) => state.expanded.has(comment.id)));
}

function foldEvery(open) {
  for (const comment of state.comments) {
    if (open) state.expanded.add(comment.id);
    else state.expanded.delete(comment.id);
  }
  renderComments();
}

function pickAll() {
  const shown = state.comments.map((comment) => comment.id);
  const already = shown.every((id) => state.picked.has(id));
  for (const id of shown) {
    if (already) state.picked.delete(id);
    else state.picked.add(id);
  }
  renderComments();
}

// The picked comments change status in one call, and the list is drawn again once. The
// thread already holds what was said.
async function setPickedStatus(ids, status) {
  const response = await fetch("/comments/status", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids, status }),
  });
  if (response.ok) for (const id of ids) state.picked.delete(id);
  await refreshComments();
  if (!response.ok) throw new Error(await responseError(response));
}

// A reply sent to its thread on Overleaf: null once sent, or why not, with the files its
// draft names that are not the same here as on Overleaf when that is the only reason.
async function sendOverleafReply(id, text, anyway = false) {
  const response = await fetch(`/comments/${id}/reply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, overleaf: true, anyway }),
  });
  if (response.ok) return null;
  const raw = await response.text();
  try {
    const { error, apart = [] } = JSON.parse(raw);
    return { error, apart };
  } catch (error) {
    return { error: `${response.status}: ${raw}`, apart: [] };
  }
}

// Each picked reply goes to its own thread on Overleaf, one after another. Those whose
// files are not the same here as on Overleaf wait for one question at the end and go
// anyway when it is answered yes. One that is refused keeps its words and says why on
// its card, opened, and the rest still go.
async function sendPickedToOverleaf(ids) {
  const waiting = [];
  const refused = (id, text, from, error) => {
    state.overleafReplies.set(id, { text, from, error });
    state.expanded.add(id);
  };
  for (const id of ids) {
    const comment = state.comments.find((item) => item.id === id);
    const text = comment ? overleafReplyText(comment) : "";
    if (!text.trim()) continue;
    const answer = await sendOverleafReply(id, text.trim());
    if (answer === null) {
      state.overleafReplies.delete(id);
      state.picked.delete(id);
    } else if (answer.apart.length) {
      waiting.push({ id, text, from: comment.overleaf_draft?.text, ...answer });
    } else {
      refused(id, text, comment.overleaf_draft?.text, answer.error);
    }
  }
  const files = [...new Set(waiting.flatMap((reply) => reply.apart))];
  if (waiting.length && confirm(`${files.join(", ")} ${files.length === 1 ? "is" : "are"} not the same here as on `
    + `Overleaf, so ${waiting.length === 1 ? "a reply" : replies(waiting.length)} may describe an edit Overleaf `
    + `does not have yet. Send ${waiting.length === 1 ? "it" : "them"} anyway?`)) {
    for (const reply of waiting) {
      const answer = await sendOverleafReply(reply.id, reply.text.trim(), true);
      if (answer === null) {
        state.overleafReplies.delete(reply.id);
        state.picked.delete(reply.id);
      } else {
        refused(reply.id, reply.text, reply.from, answer.error);
      }
    }
  } else {
    for (const reply of waiting) refused(reply.id, reply.text, reply.from, reply.error);
  }
  await refreshComments();
}

// The box that picks a card, in its head, which opens the card: picking is not opening.
function pickBox(comment) {
  const box = h("input", { class: "comment-pick", type: "checkbox", title: "Pick this comment" });
  box.checked = state.picked.has(comment.id);
  // A checkbox has already taken its new value by the time the click arrives, so the
  // whole decision is made here and nothing listens for the change.
  box.addEventListener("click", (event) => {
    event.stopPropagation();
    const shown = state.comments.map((item) => item.id);
    const from = shown.indexOf(state.pickedFrom);
    const to = shown.indexOf(comment.id);
    // Shift runs from the box the last pick started at to this one, both ends included,
    // and gives the whole run the value this box just took.
    const run = event.shiftKey && from !== -1 && to !== -1
      ? shown.slice(Math.min(from, to), Math.max(from, to) + 1)
      : [comment.id];
    for (const id of run) {
      if (box.checked) state.picked.add(id);
      else state.picked.delete(id);
    }
    state.pickedFrom = comment.id;
    renderComments();
    renderPickedActions();
  });
  return box;
}

function renderCommentItem(comment) {
  const expanded = state.expanded.has(comment.id);
  const replies = comment.thread.length - 1;
  const box = pickBox(comment);
  const toggle = (event) => {
    event.stopPropagation();
    if (expanded) state.expanded.delete(comment.id);
    else state.expanded.add(comment.id);
    renderComments();
    jumpToComment(comment.id);
  };
  const head = h("div", {
    class: "cmt-head",
    title: expanded ? "collapse" : "expand thread",
    onclick: toggle,
  },
  box,
  h("span", {
    class: "cmt-toggle",
    text: expanded ? "▾" : replies > 0 ? `▸ ${replies} repl${replies > 1 ? "ies" : "y"}` : "▸",
  }),
  h("span", { class: "cmt-id", text: comment.id }),
  h("span", { class: "cmt-status", text: `[${comment.status}]` }),
  comment.stale ? h("span", { class: "stale", text: "STALE" }) : null,
  !comment.stale && comment.reanchored
    ? h("span", { class: "reanchored", text: "EDITED", title: "The commented text was edited; the highlight follows the edited text" })
    : null,
  h("span", { class: "cmt-anchor", text: anchorLabel(comment.anchor) }));

  const children = [head];
  const unplaced = state.overleafUnplaced[comment.id];
  if (unplaced && comment.status === "open") {
    children.push(h("div", { class: "cmt-unplaced" }, leafIcon(), h("span", {
      text: `On Overleaf at ${unplaced}. Those words are not in the file here, so this sits on the paper until they are.`,
    })));
  }
  if (!expanded && comment.thread.length > 0) {
    // A thread from Overleaf names who opened it, since it may be anyone in the project.
    const first = comment.thread[0];
    children.push(h("div", { class: "cmt-preview",
                             text: first.name !== undefined ? `${first.name}  ${first.text}` : first.text }));
    // The request, then whatever was last said about it: a thread someone has answered
    // must not read like one nobody has touched, and who answered has to be visible.
    // A long answer is cut to three lines here and opens in full on a click, so nothing
    // it says is only half shown with no way to reach the rest.
    const latest = comment.thread[comment.thread.length - 1];
    if (comment.thread.length > 1) {
      children.push(h("div", { class: `cmt-latest author-${latest.author}`,
                               title: "expand thread",
                               onclick: toggle,
                               text: `${writer(latest)}  ${latest.text}` }));
    }
  }
  const onOverleaf = state.overleafImported.has(comment.id);
  if (expanded) {
    // The thread reads downward, oldest first, the way it was written.
    children.push(
      h("div", { class: "cmt-thread" },
        ...comment.thread.map((entry, index) => renderThreadEntry(entry, comment.id, index, onOverleaf))));
  }
  // The proposal sits under the conversation, where the card ends. A thread carries one,
  // so this is never a list.
  if (comment.suggestion) children.push(renderSuggestion(comment));
  // So does the reply drafted for Overleaf, or a new thread's first message, shown closed or
  // open while it waits to be sent.
  if (forOverleaf(comment) && comment.status === "open" && (expanded || comment.overleaf_draft)) {
    children.push(renderOverleafReply(comment));
  }
  if (expanded) {
    children.push(h("div", { class: "cmt-actions" }, ...actionButtons(comment)));
    const form = renderActiveForm(comment);
    if (form) children.push(form);
  }
  const focused = state.focusedCommentId === comment.id;
  const ranged = rangeIds().includes(comment.id);
  return h("div", {
    class: `cmt status-${comment.status}${focused ? " is-focused" : ""}${ranged ? " in-range" : ""}`,
    data: { commentId: comment.id },
  }, ...children);
}

// A suggested edit's card: who suggested it and when, its words as a proposal's − and + lines
// show them, and Accept and Reject, which act on Overleaf for everyone in the project. A click
// shows it in the source, and opens long words in full.
function renderSuggestedEdit(item) {
  const expanded = state.expanded.has(item.id);
  const show = (event) => {
    event.stopPropagation();
    if (expanded) state.expanded.delete(item.id);
    else state.expanded.add(item.id);
    state.focusedCommentId = item.id;
    renderComments();
    jumpToComment(item.id).catch((error) => console.error(error));
  };
  const where = `${item.file}:${item.here ? item.here.line_start : item.line}`;
  const head = h("div", { class: "cmt-head", title: "Show it in the source", onclick: show },
    pickBox(item),
    h("span", { class: "suggested-by", style: { borderColor: overleafColor(item.user_id) }, text: item.name || "Someone" }),
    item.when ? h("span", { class: "cmt-when", text: item.when }) : null,
    h("span", { class: "cmt-anchor", text: where }));
  const line = (kind, marker, text) => h("div", { class: kind },
    h("span", { class: "sugg-marker", text: marker }), h("span", { class: "sugg-text", text }));
  const words = h("div", { class: `cmt-suggestion suggested-words${expanded ? "" : " clamped"}`, onclick: show },
    item.deleted ? line("sugg-old", "−", item.deleted) : null,
    item.inserted ? line("sugg-new", "+", item.inserted) : null);
  const busy = state.reviewingEdits.has(item.id);
  const press = (action) => () => reviewSuggestedEdits(action, [item]).catch((error) => alert(error.message));
  const accept = actionButton("change-accept", "Accept", press("accept"));
  const reject = actionButton("change-reject", "Reject", press("reject"));
  for (const button of [accept, reject]) {
    button.disabled = busy;
    button.classList.toggle("busy", busy);
  }
  return h("div", {
    class: `cmt suggested-edit${state.focusedCommentId === item.id ? " is-focused" : ""}`
      + `${rangeIds().includes(item.id) ? " in-range" : ""}`,
    data: { commentId: item.id },
  }, head, words,
  item.here ? null : h("div", { class: "cmt-unplaced" }, leafIcon(), h("span", {
    text: `On Overleaf at ${item.file}:${item.line}. The text around it is not in the file here yet.`,
  })),
  h("div", { class: "cmt-actions" }, accept, reject));
}

// Overleaf's suggested edits read again when the reviewer comes back to the page or opens their
// view, at most every ten seconds: an edit suggested in a file the page does not follow live
// is not told to it. The list changes once the reading finds something new.
function readSuggestedEdits() {
  if (!state.overleafProject || Date.now() - state.suggestedRead < 10000) return;
  state.suggestedRead = Date.now();
  fetch("/overleaf/changes/read", { method: "POST" }).catch((error) => console.error(error));
}

// Accept or reject suggested edits on Overleaf, the edits of one file in one request.
async function reviewSuggestedEdits(action, items) {
  const docs = new Map();
  for (const item of items) docs.set(item.doc, [...(docs.get(item.doc) ?? []), item]);
  for (const item of items) state.reviewingEdits.add(item.id);
  renderComments();
  try {
    for (const [doc, edits] of docs) {
      const response = await fetch(`/overleaf/changes/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc, ids: edits.flatMap((item) => item.ids) }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      for (const item of edits) state.picked.delete(item.id);
    }
  } finally {
    for (const item of items) state.reviewingEdits.delete(item.id);
    await refreshComments();
  }
}

function renderSuggestion(comment) {
  const changes = comment.suggestion?.changes ?? [];
  // The proposal names its own file, so it applies whatever the comment points at: a
  // source range, PDF text, or a section.
  let applyButton = null;
  if (changes.length > 0 && comment.status === "open") {
    applyButton = actionButton("sugg-apply", "Apply suggestion", async () => {
      applyButton.disabled = true;
      applyButton.textContent = "Applying…";
      if (await doMutation(comment.id, "apply-suggestion", { updated: comment.updated })) {
        try {
          await refreshComments();
        } catch (error) {
          alert(`Applied, but comments could not be refreshed: ${error.message}`);
        }
        return;
      }
      applyButton.disabled = false;
      applyButton.textContent = "Apply suggestion";
    });
  }
  // One pair per piece the proposal changes; the text it leaves alone is not shown,
  // because it is not part of the proposal.
  return h("div", { class: "cmt-suggestion" },
    ...changes.flatMap((change) => [
      h("div", { class: "sugg-old", title: "current text" },
        h("span", { class: "sugg-marker", text: "−" }),
        h("span", { class: "sugg-text", text: change.old })),
      h("div", { class: "sugg-new", title: "proposed replacement" },
        h("span", { class: "sugg-marker", text: "+" }),
        h("span", { class: "sugg-text", text: change.new })),
    ]),
    applyButton ? h("div", { class: "sugg-actions" }, applyButton) : null,
  );
}

// The thread's Overleaf reply: the agent's draft, which the reviewer edits and sends. The
// server sends it only once the words its draft describes are on Overleaf, and says why not.
// What a thread's reply box for Overleaf holds: what the reviewer typed over the agent's draft,
// else the draft. A new draft from the agent replaces what was typed over the one before.
function overleafReplyText(comment) {
  const kept = state.overleafReplies.get(comment.id);
  if (kept && kept.from !== comment.overleaf_draft?.text) state.overleafReplies.delete(comment.id);
  return state.overleafReplies.get(comment.id)?.text ?? comment.overleaf_draft?.text ?? "";
}

function renderOverleafReply(comment) {
  const draft = comment.overleaf_draft;
  const textarea = h("textarea", {
    class: "cmt-form-input overleaf-reply-input", rows: 3,
    placeholder: draftedThread(comment) ? "The thread's first message on Overleaf, for everyone in the project…"
      : "Reply on Overleaf, for everyone in the project…",
  });
  textarea.value = overleafReplyText(comment);
  const kept = state.overleafReplies.get(comment.id);
  textarea.addEventListener("input", () => {
    state.overleafReplies.set(comment.id, { text: textarea.value, from: draft?.text });
    renderPickedActions();
  });
  // Escape leaves the box with its words in it, for the comment keys to go on.
  textarea.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    $("#comments-list").focus({ preventScroll: true });
  });
  const sending = state.sending.has(comment.id);
  textarea.disabled = sending;
  const note = h("div", { class: "overleaf-reply-note", role: "status", text: sending ? "Sending…" : kept?.error ?? "" });
  const send = h("button", {
    class: `overleaf-send${sending ? " busy" : ""}`, type: "button",
    title: state.overleafSignedIn ? `Send to Overleaf, for everyone in the project${keyHint("overleaf-send")}`
      : "Save your Overleaf cookie first",
  }, "Send to ", leafIcon());
  send.disabled = !state.overleafSignedIn || sending;
  // The card shows the reply on its way until the list shows it sent, or why not.
  send.addEventListener("click", async () => {
    const text = textarea.value.trim();
    if (!text) return;
    state.sending.add(comment.id);
    renderComments();
    try {
      let answer = await sendOverleafReply(comment.id, text);
      if (answer?.apart.length && confirm(`${answer.error} Send it anyway?`)) {
        answer = await sendOverleafReply(comment.id, text, true);
      }
      if (answer === null) {
        state.overleafReplies.delete(comment.id);
        await refreshComments();
      } else {
        state.overleafReplies.set(comment.id, { text: textarea.value, from: draft?.text, error: answer.error });
      }
    } finally {
      state.sending.delete(comment.id);
      renderComments();
    }
  });
  return h("div", { class: "cmt-overleaf" },
    h("div", { class: "cmt-overleaf-head" }, leafIcon(),
      h("span", { text: draftedThread(comment) ? "New thread on Overleaf"
        : draft ? "Draft for Overleaf" : "Reply on Overleaf" })),
    textarea,
    draft?.ranges?.length
      ? h("div", { class: "thread-edits" }, ...draft.ranges.map(draftRange))
      : null,
    note,
    h("div", { class: "cmt-form-actions" }, send));
}

// The lines a draft describes, where they are now as a comment's are. A range not found has
// no line to name, and one whose lines were edited after the draft was written says so.
function draftRange(range) {
  return h("span", { class: "edit", text: range.range },
    range.stale ? h("span", { class: "stale", text: "STALE", title: "These lines are not found in the file any more" }) : null,
    range.edited ? h("span", { class: "reanchored", text: "EDITED", title: "These lines were edited after the draft was written" }) : null);
}

// An entry Overleaf holds goes by the name Overleaf gives its writer, as on Overleaf.
function writer(entry) {
  return entry.name ?? entry.author;
}

// On a thread from Overleaf, each entry says whether Overleaf has it: one written there, one
// sent there, or one that stays on this page.
function overleafMark(entry) {
  if (entry.overleaf !== undefined) {
    return h("span", {
      class: "entry-overleaf sent",
      title: entry.overleaf ? `Sent to Overleaf at commit ${entry.overleaf}` : "Sent to Overleaf",
    }, leafIcon(), "sent");
  }
  if (entry.name !== undefined) return h("span", { class: "entry-overleaf", title: "Written on Overleaf" }, leafIcon());
  return h("span", { class: "entry-overleaf here", text: "here only", title: "Not sent to Overleaf" });
}

function renderThreadEntry(entry, commentId, index, onOverleaf) {
  const editing = state.editingEntry?.commentId === commentId
    && state.editingEntry.index === index;
  const children = [
    h("div", { class: "thread-meta" },
      h("span", { text: `${writer(entry)} · ${entry.when}` }),
      onOverleaf ? overleafMark(entry) : null,
      // Overleaf keeps what it holds as written there, so that is answered, not edited.
      entry.author === "human" && !editing && entry.name === undefined && entry.overleaf === undefined
        ? actionButton("cmt-edit", "Edit", () => startEntryEdit(commentId, index, entry.text))
        : null),
  ];
  if (editing) children.push(renderEntryEditor(commentId, index));
  else if (entry.text) children.push(h("div", { class: "thread-text", text: entry.text }));
  if (entry.edits?.length) {
    children.push(h("div", { class: "thread-edits" },
      ...entry.edits.map((edit) => h("span", { class: "edit", text: edit }))));
  }
  return h("div", { class: `thread-entry author-${entry.author}` }, ...children);
}

function startEntryEdit(commentId, index, text) {
  state.editingEntry = { commentId, index, draft: text };
  renderComments();
  const node = Array.from($("#comments-list").children).find(
    (child) => child.dataset.commentId === commentId,
  );
  node?.querySelector(".entry-edit-input")?.focus({ preventScroll: true });
}

// Editing rewrites the entry in place: the author and time stay, so a typo fix
// does not read as a new message in the thread.
function renderEntryEditor(commentId, index) {
  const editing = state.editingEntry;
  const textarea = h("textarea", { class: "cmt-form-input entry-edit-input", rows: 3 });
  textarea.value = editing.draft;
  textarea.addEventListener("input", () => {
    if (state.editingEntry === editing) editing.draft = textarea.value;
  });
  const save = async () => {
    const text = textarea.value.trim();
    if (!text) return;
    textarea.disabled = true;
    if (!await doMutation(commentId, "edit", { index, text })) {
      textarea.disabled = false;
      textarea.focus();
      return;
    }
    state.editingEntry = null;
    try {
      await refreshComments();
    } catch (error) {
      alert(`Saved, but comments could not be refreshed: ${error.message}`);
    }
  };
  const cancel = () => {
    state.editingEntry = null;
    renderComments();
  };
  sendOn(textarea, save);
  textarea.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    cancel();
    $("#comments-list").focus({ preventScroll: true });
  });
  return h("div", { class: "cmt-form mode-edit" }, textarea,
    h("div", { class: "cmt-form-actions" },
      actionButton("cmt-form-cancel", "Cancel", cancel),
      actionButton("cmt-form-submit", "Save", save)));
}

function actionButton(className, label, onclick) {
  const hint = keyHint(className);
  return h("button", { class: className, type: "button", text: label, onclick, title: hint ? `${label}${hint}` : null });
}

function actionButtons(comment) {
  const deleteButton = actionButton("cmt-delete", "Delete", () => {
    if (confirm(`Permanently delete ${comment.id}?`)) {
      mutateAndRefresh(comment.id, "delete", null);
    }
  });
  // Every status flip is one click: the thread already holds what was said, so an
  // empty summary flips the status without adding an entry. An archived thread still
  // takes replies, and goes back to open or resolved from the same row.
  const buttons = [];
  // A thread drafted for Overleaf has nothing said in it yet: it is sent, or deleted here.
  if (draftedThread(comment)) return [deleteButton];
  // A thread from Overleaf resolves and reopens there, at once: Overleaf takes no summary,
  // has no archive, and deletes a thread only there.
  if (state.overleafImported.has(comment.id)) {
    return [
      actionButton("cmt-reply", "Reply", () => setActiveForm(comment.id, "reply")),
      comment.status === "open"
        ? actionButton("cmt-resolve", "Resolve", () => mutateAndRefresh(comment.id, "resolve", { summary: "" }))
        : actionButton("cmt-reopen", "Reopen", () => mutateAndRefresh(comment.id, "reopen", {})),
    ];
  }
  if (comment.status !== "resolved") {
    buttons.push(actionButton("cmt-reply", "Reply", () => setActiveForm(comment.id, "reply")));
  }
  if (comment.status !== "open") {
    buttons.push(actionButton("cmt-reopen", "Reopen", () => mutateAndRefresh(comment.id, "reopen", {})));
  }
  if (comment.status !== "resolved") {
    buttons.push(actionButton("cmt-resolve", "Resolve", () => closeComment(comment.id, "resolve", "summary")));
  }
  if (comment.status !== "archived") {
    buttons.push(actionButton("cmt-archive", "Archive", () => mutateAndRefresh(comment.id, "archive", {})));
  }
  buttons.push(deleteButton);
  return buttons;
}

// A card can hold several boxes, the reply for Overleaf above the one being written here among
// them, so a box is found again by which it is, never as the card's first.
function boxOf(input) {
  if (input.classList.contains("overleaf-reply-input")) return ".overleaf-reply-input";
  const mode = [...(input.closest(".cmt-form")?.classList ?? [])].find((name) => name.startsWith("mode-"));
  return `.cmt-form.${mode} .cmt-form-input`;
}

function setActiveForm(commentId, mode) {
  state.activeForm = { commentId, mode, draft: "" };
  state.expanded.add(commentId);
  renderComments();
  const commentNode = Array.from($("#comments-list").children).find(
    (node) => node.dataset.commentId === commentId,
  );
  const input = commentNode?.querySelector(`.cmt-form.mode-${mode} .cmt-form-input`);
  if (!input) return;
  input.closest(".cmt-form").scrollIntoView({ block: "nearest", behavior: "smooth" });
  input.focus({ preventScroll: true });
}

async function closeComment(commentId, action, key) {
  if (state.activeForm?.commentId === commentId) state.activeForm = null;
  await mutateAndRefresh(commentId, action, { [key]: "" });
}

const FORM_CONFIG = {
  reply: { placeholder: "Reply…", key: "text", label: "Post reply" },
};

function renderActiveForm(comment) {
  if (!state.activeForm || state.activeForm.commentId !== comment.id) return null;
  const mode = state.activeForm.mode;
  const config = FORM_CONFIG[mode];
  const textarea = h("textarea", {
    class: "cmt-form-input",
    rows: 3,
    placeholder: config.placeholder,
  });
  textarea.value = state.activeForm.draft;
  textarea.addEventListener("input", () => {
    if (
      state.activeForm?.commentId === comment.id
      && state.activeForm.mode === mode
    ) {
      state.activeForm.draft = textarea.value;
    }
  });
  let submitting = false;
  let submitButton = null;
  const submit = async () => {
    const text = textarea.value.trim();
    if (!text || submitting) return;
    submitting = true;
    textarea.disabled = true;
    submitButton.disabled = true;
    submitButton.textContent = "Saving…";
    const saved = await doMutation(comment.id, mode, { [config.key]: text });
    if (saved) {
      state.activeForm = null;
      try {
        await refreshComments();
      } catch (error) {
        alert(`Saved, but comments could not be refreshed: ${error.message}`);
      }
      return;
    }
    submitting = false;
    textarea.disabled = false;
    submitButton.disabled = false;
    submitButton.textContent = config.label;
    textarea.focus();
  };
  sendOn(textarea, submit);
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      state.activeForm = null;
      renderComments();
      $("#comments-list").focus({ preventScroll: true });
    }
  });
  submitButton = actionButton("cmt-form-submit", config.label, submit);
  const form = h("div", { class: `cmt-form mode-${mode}` },
    h("div", { class: "cmt-form-title", text: config.placeholder }),
    textarea,
    h("div", { class: "cmt-form-actions" },
      actionButton("cmt-form-cancel", "Cancel", () => {
        state.activeForm = null;
        renderComments();
      }),
      submitButton));
  return form;
}

function anchorLabel(anchor) {
  switch (anchor.kind) {
    case "paper": return "paper";
    case "section": return `§ ${anchor.title}`;
    case "source_range": return `${anchor.file}:${anchor.line_start}-${anchor.line_end}`;
    case "text_selection": return `p${anchor.selection.page} text`;
    case "area": return `p${anchor.page} area`;
    default: throw new Error(`unknown anchor kind: ${anchor.kind}`);
  }
}

async function doMutation(commentId, action, body) {
  const isDelete = action === "delete";
  let response;
  try {
    response = await fetch(
      isDelete ? `/comments/${commentId}` : `/comments/${commentId}/${action}`,
      isDelete ? { method: "DELETE" } : {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  } catch (error) {
    alert(`Action failed: ${error.message}`);
    return false;
  }
  if (!response.ok) {
    alert(`Action failed: ${await responseError(response)}`);
    return false;
  }
  return true;
}

async function mutateAndRefresh(commentId, action, body) {
  if (!await doMutation(commentId, action, body)) return;
  try {
    await refreshComments();
  } catch (error) {
    alert(`Saved, but comments could not be refreshed: ${error.message}`);
  }
}

function annotationId(commentId, page) {
  return `${ANNOTATION_PREFIX}${commentId}:p${page}`;
}

function commentBadgePlans() {
  let number = 0;
  const plans = new Map();
  for (const comment of state.comments) {
    if (comment.status !== "open") continue;
    const selection = comment.anchor.kind === "text_selection"
      ? comment.anchor.selection
      : comment.anchor.kind === "area"
        ? { page: comment.anchor.page, rects: [comment.anchor.bbox] }
        : null;
    if (selection === null) continue;
    number += 1;
    const pagePlans = plans.get(selection.page) ?? [];
    pagePlans.push({
      comment,
      number,
      segments: selection.rects.length,
    });
    plans.set(selection.page, pagePlans);
  }
  return plans;
}

function annotationPage(layer) {
  let row = layer;
  while (row.parentElement !== null) {
    const parent = row.parentElement;
    if (getComputedStyle(row).display === "flex"
      && getComputedStyle(parent).flexDirection === "column") {
      return Array.from(parent.children).indexOf(row) + 1;
    }
    row = parent;
  }
  return null;
}

function commentSegments(layer) {
  return Array.from(layer.querySelectorAll("div")).filter((node) =>
    node.classList.contains("tex-comment-segment")
      || (node.style.position === "absolute"
        && node.style.zIndex === "1"
        && node.style.cursor === "pointer"
        && (node.style.opacity === "0.2" || node.style.opacity === "0.35")));
}

function syncCommentBadges() {
  state.badgeFrame = null;
  if (!state.viewer) return;
  const plans = commentBadgePlans();
  const pageOffsets = new Map();
  const shown = new Set();
  for (const layer of state.viewer.shadowRoot.querySelectorAll("[data-no-interaction]")) {
    const segments = commentSegments(layer);
    if (segments.length === 0) continue;
    const page = annotationPage(layer);
    const pagePlans = plans.get(page);
    const offset = pageOffsets.get(page) ?? 0;
    const plan = pagePlans?.[offset];
    pageOffsets.set(page, offset + 1);
    if (plan === undefined || segments.length < plan.segments) continue;
    const frame = segments[0]?.parentElement?.parentElement;
    for (const segment of segments.slice(0, plan.segments)) {
      segment.classList.add("tex-comment-segment");
      segment.style.pointerEvents = "none";
      segment.style.cursor = "text";
    }
    if (!frame) continue;
    shown.add(plan.comment.id);
    let badge = layer.querySelector(
      `.tex-comment-badge[data-comment-id="${CSS.escape(plan.comment.id)}"]`,
    );
    if (badge === null) {
      badge = document.createElement("button");
      badge.type = "button";
      badge.className = "tex-comment-badge";
      badge.dataset.commentId = plan.comment.id;
      badge.addEventListener("pointerdown", (event) => event.stopPropagation());
      badge.addEventListener("click", (event) => {
        event.stopPropagation();
        if (!$(".layout").classList.contains("sidebar-collapsed")
          && state.focusedCommentId === plan.comment.id) {
          setSidebarCollapsed(true);
          return;
        }
        setSidebarCollapsed(false);
        switchTab(state.commentTab);
        const comment = state.comments.find((item) => item.id === plan.comment.id);
        if (comment) focusComment(comment);
      });
      layer.appendChild(badge);
    }
    badge.classList.toggle("stale", plan.comment.stale === true);
    if (badge.textContent !== String(plan.number)) badge.textContent = String(plan.number);
    badge.title = plan.comment.thread[0]?.text ?? "";
    badge.setAttribute("aria-label", `Open comment ${plan.number}`);
    const localWidth = parseFloat(frame.style.width);
    const scale = localWidth > 0 ? frame.getBoundingClientRect().width / localWidth : 1;
    badge.style.left = `${Math.max(0, parseFloat(frame.style.left) - 20 / scale)}px`;
    badge.style.top = `${Math.max(0, parseFloat(frame.style.top) - 1 / scale)}px`;
    badge.style.transform = `scale(${1 / scale})`;
  }
  for (const badge of state.viewer.shadowRoot.querySelectorAll(".tex-comment-badge")) {
    if (!shown.has(badge.dataset.commentId)) badge.remove();
  }
}

function scheduleCommentBadges() {
  if (state.badgeFrame !== null) return;
  state.badgeFrame = requestAnimationFrame(() => {
    state.badgeFrame = requestAnimationFrame(syncCommentBadges);
  });
}

function syncCommentAnnotations() {
  if (!state.annotationsReady || !state.annotations) return;
  for (const tracked of state.annotations.getAnnotations()) {
    if (tracked.object.id.startsWith(ANNOTATION_PREFIX)) {
      state.annotations.purgeAnnotation(tracked.object.pageIndex, tracked.object.id);
    }
  }
  state.annotationCommentById.clear();
  for (const comment of state.comments) {
    if (comment.status !== "open") continue;
    // A stale comment's text has moved or gone; what the page still has is the place it
    // was written at, and a faint mark in the STALE badge's red stands there: it says the
    // comment was about this much of the page and no closer. Clicking it, or the card,
    // still lands here.
    const stale = comment.stale === true;
    const selections = comment.anchor.kind === "text_selection"
      ? [comment.anchor.selection]
      : comment.anchor.kind === "area"
        ? [{ page: comment.anchor.page, bbox: comment.anchor.bbox, rects: [comment.anchor.bbox] }]
        : [];
    for (const selection of selections) {
      const id = annotationId(comment.id, selection.page);
      state.annotationCommentById.set(id, comment.id);
      state.annotations.createAnnotation(selection.page - 1, {
        id,
        pageIndex: selection.page - 1,
        type: PdfAnnotationSubtype.HIGHLIGHT,
        rect: bboxToRect(selection.bbox),
        segmentRects: selection.rects.map(bboxToRect),
        opacity: stale ? 0.2 : 0.35,
        strokeColor: stale ? "#b44a43" : "#fbdc00",
        contents: comment.thread[0]?.text ?? "",
        custom: { texWebCommentId: comment.id },
      });
    }
  }
  scheduleCommentBadges();
}

function renderErrorBanner() {
  const banner = $("#error-banner");
  clear(banner);
  if (state.errors.length === 0 && state.warnings.length === 0) {
    banner.appendChild(placeholder("No errors or warnings."));
    return;
  }
  const items = [
    ...state.errors.map((error) => ({ ...error, level: "error" })),
    ...state.warnings.map((warning) => ({ ...warning, level: "warning" })),
  ];
  for (const item of items) {
    const children = [
      h("div", { class: "err-loc" },
        h("span", { class: `err-level err-level-${item.level}`, text: item.level.toUpperCase() }),
        h("span", { text: `${item.file ?? ""}${item.line ? `:${item.line}` : ""}` })),
      h("div", { class: "err-msg", text: item.message ?? "" }),
    ];
    if (item.context?.length) children.push(h("pre", { class: "err-context", text: item.context.join("\n") }));
    banner.appendChild(h("div", { class: `err-item err-${item.level}` }, ...children));
  }
}

async function refreshPaper() {
  const response = await fetch("/paper");
  if (!response.ok) throw new Error(await responseError(response));
  state.paper = await response.json();
  state.pdfDigest = state.paper.pdf_digest;
  $("#main-file").textContent = state.paper.main_file;
  applyTranslationLanguage(state.paper.translation_language ?? null);
  renderSections();
}

function renderSections() {
  const host = $("#sections-list");
  clear(host);
  if (!state.paper) {
    host.appendChild(placeholder("Loading…"));
    return;
  }
  const list = h("ul", { class: "paper-sections" });
  for (const section of state.paper.sections) {
    list.appendChild(h("li", { class: `paper-section level-${section.level}` },
      h("span", {
        class: "paper-section-title",
        title: `${section.file}:${section.line}`,
        onclick: () => jumpToSource(section.file, section.line),
      },
      section.number ? h("span", { class: "section-number", text: section.number }) : null,
      h("span", { text: section.title })),
      actionButton("comment-section-btn", "+ comment", () =>
        openCompose({ kind: "section", title: section.title, label: section.label }, `Section: ${section.title}`,
          "", { file: section.file, line: section.line }))));
  }
  host.appendChild(list);
}

// The two comment tabs share one pane, which shows the chosen tab's comments.
function setCommentFilter(value) {
  const filter = $("#comment-filter");
  filter.value = value;
  $("#comment-filter-name").textContent = filter.selectedOptions[0].textContent;
}

function switchTab(name) {
  for (const button of $$(".tab-btn")) button.classList.toggle("active", button.dataset.tab === name);
  const pane = name === "overleaf-comments" ? "comments" : name;
  for (const element of $$(".tab-pane")) element.classList.toggle("active", element.id === `tab-${pane}`);
  if (pane !== "comments" || state.commentTab === name) return;
  state.commentTab = name;
  state.picked.clear();
  renderCommentFilter();
  // The leaf tab opens on the threads that wait for the reviewer's answer.
  if (name === "overleaf-comments") setCommentFilter("unanswered");
  refreshComments().catch((error) => console.error(error));
}

function applyCompileResult(result) {
  state.errors = result.errors;
  state.warnings = result.warnings;
  const duration = typeof result.duration_seconds === "number"
    ? ` · ${result.duration_seconds.toFixed(1)}s`
    : "";
  $("#compile-status").textContent = (result.success ? "✓ ok" : "✗ failed") + duration;
  const badge = $("#compile-tab-count");
  const total = state.errors.length + state.warnings.length;
  badge.textContent = String(total);
  badge.classList.toggle("hidden", total === 0);
  badge.classList.toggle("has-errors", state.errors.length > 0);
  renderErrorBanner();
}

function applyCompiling(compiling) {
  const button = $("#compile-btn");
  button.disabled = compiling;
  button.querySelector(".wide-label").textContent = compiling ? "Compiling…" : "Compile";
  if (compiling) $("#compile-status").textContent = "compiling…";
}

// ----- Translation -----
// One source file's paragraphs in the language `translation: language:` names in the
// configuration, with the ones typeset on the current PDF page marked. The server
// translates a paragraph only the first time its text is seen, so paging, switching files
// and recompiling cost nothing except for changed paragraphs. The file is the one chosen
// in the panel, else the editor's file (Source/Split), else the file SyncTeX places most on
// the current page. Without a configured language the button and the panel stay hidden.
const translation = { on: false, language: null, page: 0, file: null, timer: null };

function setTranslation(on) {
  translation.on = on;
  try { localStorage.setItem("texMcpTranslation", on ? "1" : "0"); } catch { /* per-viewer only */ }
  showTranslation();
}

function applyTranslationLanguage(language) {
  if (language === translation.language) return;
  translation.language = language;
  translation.file = null;
  $("#translation-btn").classList.toggle("hidden", !language);
  $("#translation-btn").textContent = language || "Translation";
  $("#translation-language").textContent = language || "Translation";
  showTranslation();
}

function translationShown() {
  return translation.on && Boolean(translation.language);
}

function showTranslation() {
  const shown = translationShown();
  $("#translation-btn").classList.toggle("active", shown);
  applyLayout();
  if (shown) {
    syncTranslationFiles().catch((error) => console.error(error));
    scheduleTranslation(0);
  }
}

async function syncTranslationFiles() {
  const response = await fetch("/sources");
  if (!response.ok) return;
  const files = (await response.json()).files.filter((file) => file.endsWith(".tex"));
  const select = $("#translation-file");
  const chosen = select.value;
  select.replaceChildren(h("option", { value: "", text: "Auto" }),
    ...files.map((file) => h("option", { value: file, text: file })));
  select.value = files.includes(chosen) ? chosen : "";
}

function scheduleTranslation(delay = 800) {
  if (!translationShown()) return;
  clearTimeout(translation.timer);
  translation.timer = setTimeout(() => loadTranslation().catch((error) => console.error(error)), delay);
}

async function loadTranslation() {
  if (!translationShown() || !state.scroll) return;
  const page = state.scroll.getCurrentPage();
  const chosen = $("#translation-file").value || (state.view !== "pdf" ? state.sourcePath : null);
  const params = new URLSearchParams({ page: String(page) });
  if (chosen) params.set("file", chosen);
  const request = params.toString();
  translation.request = request;
  const response = await fetch(`/translations?${request}`);
  if (translation.request !== request) return;
  if (!response.ok) {
    $("#translation-status").textContent = await responseError(response);
    return;
  }
  const data = await response.json();
  const body = $("#translation-body");
  const sameFile = data.file === translation.file;
  const pageChanged = page !== translation.page;
  const keepScroll = body.scrollTop;
  translation.file = data.file;
  translation.page = page;
  $("#translation-page").textContent = `p. ${page}`;
  $("#translation-status").textContent = data.status === "pending" ? "Translating…"
    : data.status === "error" ? `Translation failed: ${data.error}` : "";
  const items = data.paragraphs.map((paragraph) => h("p", {
    class: ["translation-para", paragraph.text ? "" : "pending", paragraph.on_page ? "on-page" : ""].join(" ").trim(),
    title: `${data.file}:${paragraph.line_start}-${paragraph.line_end}`,
    data: { start: paragraph.line_start, end: paragraph.line_end },
    onclick: async () => {
      if (state.view === "pdf") await showSourceBesidePreview();
      else await initializeSourceEditor();
      if (!layoutShows("source")) await setWorkspaceView("source");
      await openSource(data.file, paragraph.line_start);
    },
  }, h("span", {
    class: "translation-line",
    text: paragraph.line_start === paragraph.line_end
      ? String(paragraph.line_start) : `${paragraph.line_start}–${paragraph.line_end}`,
  }), h("span", { class: "translation-text", text: paragraph.text ?? "Translating…" })));
  body.replaceChildren(...items);
  markTranslationCursor();
  const first = items.find((item) => item.classList.contains("on-page"));
  if ((!sameFile || pageChanged) && first) body.scrollTop = Math.max(0, first.offsetTop - 8);
  else if (sameFile) body.scrollTop = keepScroll;
  else body.scrollTop = 0;
  if (data.status === "pending") translation.timer = setTimeout(() => scheduleTranslation(0), 3000);
}

// The paragraph holding the editor's cursor, while the two show the same file.
function markTranslationCursor() {
  const line = state.editor && translation.file === state.sourcePath
    ? state.editor.getCursorPosition().row + 1 : null;
  let marked = null;
  for (const item of $("#translation-body").children) {
    const here = line !== null && Number(item.dataset.start) <= line && line <= Number(item.dataset.end);
    item.classList.toggle("at-cursor", here);
    if (here) marked = item;
  }
  return marked;
}

function followSourceCursor() {
  if (!translationShown()) return;
  const item = markTranslationCursor();
  if (item === null) return;
  const body = $("#translation-body");
  const above = item.offsetTop < body.scrollTop;
  const below = item.offsetTop + item.offsetHeight > body.scrollTop + body.clientHeight;
  if (above || below) body.scrollTop = Math.max(0, item.offsetTop - 8);
}

// The translation's type size, kept for next time, stepped as the editor's is.
function translationFontSize() {
  let stored = 0;
  try { stored = Number(localStorage.getItem("texMcpTranslationFontSize")); } catch { /* the default */ }
  return stored >= 10 && stored <= 28 ? stored : 14;
}

function stepTranslationFontSize(step) {
  const size = Math.min(28, Math.max(10, translationFontSize() + step));
  try { localStorage.setItem("texMcpTranslationFontSize", String(size)); } catch { /* per-viewer only */ }
  $("#translation-body").style.fontSize = `${size}px`;
}

async function applyCompletedCompile(result, pdfDigest) {
  const timestamp = result.timestamp;
  if (timestamp && timestamp === state.appliedCompileTimestamp) {
    if (state.compileRefreshPromise) await state.compileRefreshPromise;
    return;
  }

  state.appliedCompileTimestamp = timestamp;
  const refresh = (async () => {
    applyCompileResult(result);
    if (result.success) {
      const pdfView = capturePdfView();
      if (pdfDigest === undefined) await refreshPaper();
      else state.pdfDigest = pdfDigest;
      await initializePdfViewer(pdfView);
      await refreshComments();
    }
    if (pdfDigest !== undefined || !result.success) await refreshPaper();
  })();
  state.compileRefreshPromise = refresh;
  try {
    await refresh;
  } finally {
    if (state.compileRefreshPromise === refresh) state.compileRefreshPromise = null;
    applyCompiling(false);
  }
}

// Ctrl+Enter (Cmd+Enter), or :make with Vim keys, compiles as the button does, saving the
// editor's changes first so that the PDF shows them.
async function compileFromKeys() {
  if ($("#compile-btn").disabled) return;
  if (state.sourceDirty) await saveSource();
  await compile();
}

async function compile() {
  applyCompiling(true);
  try {
    const response = await fetch("/compile", { method: "POST" });
    if (!response.ok) throw new Error(await responseError(response));
    await applyCompletedCompile(await response.json());
  } catch (error) {
    applyCompiling(false);
    alert(`Could not compile: ${error.message}`);
  }
}

async function handleWebSocketMessage(message) {
  switch (message.type) {
    case "compiling":
      if (message.status) applyCompiling(true);
      break;
    case "compiled":
      await applyCompletedCompile(message.result, message.pdf_digest);
      scheduleTranslation(1500);
      scheduleCompletions(1500);
      break;
    case "comment_added":
    case "comment_updated":
    case "comment_deleted":
    case "comments_changed":
      await refreshComments();
      break;
    case "source_changed":
      readOverleafGitStateSoon();
      await handleSourceChanged(message);
      break;
    case "config_reloaded":
      await refreshPaper();
      if (state.editor) await refreshSourceFiles();
      break;
    case "config_error":
      $("#compile-status").textContent = `config error: ${message.error}`;
      break;
    case "state":
      if (message.result) applyCompileResult(message.result);
      if (message.compiling) applyCompiling(true);
      if (message.review_waiters !== undefined) showAgentWaiting(message.review_waiters);
      break;
    case "review_waiters":
      showAgentWaiting(message.waiters);
      break;
    case "goto":
      showGotoTarget(message);
      break;
    case "overleaf_live":
      renderOverleafLive(message);
      break;
    case "overleaf_waiting":
      state.overleafWaiting = message.count;
      renderOverleafThreadSync();
      break;
  }
}

// Green reaches a parked waiter now; red remains clickable because the server queues the
// call until the next waiter connects.
function showAgentWaiting(waiters) {
  const waiting = waiters > 0;
  const button = $("#call-agent-btn");
  button.classList.toggle("agent-ready", waiting);
  button.classList.toggle("agent-offline", !waiting);
  button.setAttribute("aria-label", waiting ? "Call agent" : "Queue call for agent");
  button.title = waiting
    ? "Call the waiting agent now (Ctrl+Alt+Enter)"
    : "Agent is not waiting; queue this call until it reconnects (Ctrl+Alt+Enter)";
}

async function callAgent() {
  const button = $("#call-agent-btn");
  const word = $("#call-agent-word");
  button.disabled = true;
  try {
    const response = await fetch("/review-request", { method: "POST" });
    if (!response.ok) throw new Error(await responseError(response));
    const reply = await response.json();
    // Delivered went straight to a parked agent; queued is kept by the server and
    // answers the agent's next wait at once.
    word.textContent = reply.delivered ? "Called" : "Queued";
  } catch (error) {
    word.textContent = "Failed";
    console.error(error);
  } finally {
    setTimeout(() => { word.textContent = ""; button.disabled = false; }, 2000);
  }
}

function connectWebSocket() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${location.host}/ws`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    handleWebSocketMessage(message).catch((error) => console.error(error));
  };
  socket.onclose = () => {
    showAgentWaiting(0);
    setTimeout(connectWebSocket, 2000);
  };
}

function jumpToPage(page) {
  if (!state.layoutReady || !state.scroll) return;
  state.scroll.scrollToPage({ pageNumber: page, behavior: "smooth", center: true });
}

function clearGotoHighlight() {
  if (!state.annotations) return;
  for (const tracked of state.annotations.getAnnotations()) {
    if (tracked.object.id === GOTO_ANNOTATION_ID) {
      state.annotations.purgeAnnotation(tracked.object.pageIndex, GOTO_ANNOTATION_ID);
    }
  }
}

function positionPdfTarget(page, bbox) {
  if (!state.layoutReady || !state.zoom || !bbox) {
    jumpToPage(page);
    return;
  }
  const [x1, y1, x2, y2] = bbox;
  const width = Math.max(x2 - x1 + 144, 320);
  const height = Math.max(y2 - y1 + 96, 120);
  const centerX = (x1 + x2) / 2;
  const centerY = (y1 + y2) / 2;
  state.zoom.zoomToArea(page - 1, {
    origin: {
      x: Math.max(0, centerX - width / 2),
      y: Math.max(0, centerY - height / 2),
    },
    size: { width, height },
  });
}

function showGotoTarget(target) {
  if (!target.page) return;
  positionPdfTarget(target.page, target.bbox);
  if (!target.bbox || !state.annotationsReady || !state.annotations) return;

  clearGotoHighlight();
  state.annotations.createAnnotation(target.page - 1, {
    id: GOTO_ANNOTATION_ID,
    pageIndex: target.page - 1,
    type: PdfAnnotationSubtype.HIGHLIGHT,
    rect: bboxToRect(target.bbox),
    segmentRects: (target.rects ?? [target.bbox]).map(bboxToRect),
    opacity: 0.55,
    strokeColor: "#fbdc00",
    contents: target.quote ?? "",
  });
}

async function jumpToSource(file, line) {
  if (state.view !== "pdf") {
    await initializeSourceEditor();
    await openSource(file, line);
  }
  if (state.view !== "source") await showPdfPageOf(file, line);
}

// The PDF page a source line compiles to.
async function showPdfPageOf(file, line) {
  const params = new URLSearchParams({ file, line: String(line) });
  const response = await fetch(`/synctex/source-to-pdf?${params}`);
  if (!response.ok) throw new Error(await responseError(response));
  const data = await response.json();
  jumpToPage(data.page);
}

// A place shown from the comments: the cursor on it, and its line a third of the way down the
// editor, as the ring is in the list, wherever the editor was scrolled, so the eye finds it there.
function showSourcePlace(row, column) {
  const editor = state.editor;
  editor.selection.clearSelection();
  editor.session.unfold({ row, column });
  editor.moveCursorTo(row, column);
  const top = editor.session.documentToScreenRow(row, column) * editor.renderer.lineHeight;
  editor.session.setScrollTop(Math.max(0, top - editor.renderer.scroller.clientHeight * RING_AT));
}

// Where a comment is, shown from the comments: the source moves its cursor there, and the
// keys stay with the comments, where the reader clicked or pressed Enter.
async function jumpToComment(commentId) {
  const comment = state.comments.find((item) => item.id === commentId);
  if (!comment) return;
  if (comment.anchor.kind === "source_range" || comment.suggested) {
    if (state.view === "pdf") await setWorkspaceView("source");
    const source = comment.resolved_source ?? comment.anchor;
    if (await openSource(source.file)) {
      showSourcePlace(source.line_start - 1, comment.stale ? 0 : source.column_start ?? 0);
    }
  } else if (comment.anchor.kind === "text_selection" || comment.anchor.kind === "area") {
    const selection = comment.anchor.kind === "text_selection"
      ? comment.anchor.selection
      : { page: comment.anchor.page, bbox: comment.anchor.bbox };
    positionPdfTarget(selection.page, selection.bbox);
    if (state.annotations) {
      state.annotations.selectAnnotation(
        selection.page - 1,
        annotationId(comment.id, selection.page),
      );
    }
  } else if (comment.resolved_source) {
    const { file, line_start: line } = comment.resolved_source;
    if (state.view !== "pdf" && await openSource(file)) showSourcePlace(line - 1, 0);
    if (state.view !== "source") await showPdfPageOf(file, line);
  }
  $("#comments-list").focus({ preventScroll: true });
}

// A comment shown from elsewhere, a mark on the paper or the editor: its card opens and flashes.
function focusComment(comment) {
  state.focusedCommentId = comment.id;
  state.expanded.add(comment.id);
  renderComments();
  const node = document.querySelector(`[data-comment-id="${comment.id}"]`);
  if (!node) return;
  node.scrollIntoView({ behavior: "smooth", block: "center" });
  node.classList.add("cmt-flash");
  setTimeout(() => node.classList.remove("cmt-flash"), 1600);
}

function attachKeyboardNavigation() {
  // EmbedPDF handles copy on document. Keep text selected outside the Preview, in the
  // sidebar, a dialog or a notice, out of that handler. A field such as the editor's copies
  // its own, which EmbedPDF leaves alone too.
  document.addEventListener("keydown", (event) => {
    if (!(event.metaKey || event.ctrlKey) || event.altKey || event.key.toLowerCase() !== "c") return;
    if (event.target.closest?.("input, textarea, [contenteditable]")) return;
    const selection = window.getSelection();
    const viewer = $("#pdf-viewer");
    if (selection && !selection.isCollapsed
      && !viewer.contains(selection.anchorNode) && !viewer.contains(selection.focusNode)) {
      event.stopImmediatePropagation();
    }
  }, { capture: true });
  attachCommentKeys();
}

// ----- Comment keys -----
// The comments go by keys as a notebook's cells do: the list takes the keys while the
// Comments pane is shown and nothing is being typed in, and a box being written in keeps them
// until Escape. A key presses the focused comment's button, so it does what that button does,
// and nothing on a comment without one. A key that changes the paper's file, posts to Overleaf
// or deletes is a letter pressed twice, as a notebook deletes a cell with dd.

const KEY_NAMES = { ArrowDown: "↓", ArrowUp: "↑", " ": "Space" };

// Each key as the ? dialog lists it. A key with a button names it, for the button's title. One
// that acts on the picked comments, when there are some, says so. While v holds a range, a key
// with a range action does that, and of the rest only those marked inRange act: the keys that
// move, which stretch it, and Space. The list's keys act while the Comments pane is shown, the
// tab keys while the sidebar is, and ? anywhere.
const COMMENT_KEYS = [
  ["Move", [
    { keys: ["j", "ArrowDown"], does: "Next comment", repeats: true, inRange: true,
      run: () => moveFocus((at, last) => Math.min(at + 1, last)) },
    { keys: ["k", "ArrowUp"], does: "Previous comment", repeats: true, inRange: true,
      run: () => moveFocus((at) => Math.max(at - 1, 0)) },
    { keys: ["gg", "Home"], does: "First comment", inRange: true, run: () => moveFocus(() => 0) },
    { keys: ["G", "End"], does: "Last comment", inRange: true, run: () => moveFocus((at, last) => last) },
    { keys: [" "], does: "Open or close it, opening it showing where it is in the paper or the source",
      inRange: true, run: () => foldFocused() },
    { keys: ["Shift+Space"], does: "Open or close every comment", run: () => foldAll() },
    { keys: ["K", "Ctrl+Alt+K"], does: "Go into the source at its words, the keys with the cursor. "
      + "K there, Ctrl+Alt+K without Vim keys, comes back", run: () => enterSource() },
  ]],
  ["Write", [
    { keys: ["r"], does: "Reply here, where the agent reads it and Overleaf does not", button: ["cmt-reply"],
      run: () => pressCard(".cmt-reply") },
    { keys: ["i"], does: "Write its reply for Overleaf", marks: ".cmt-overleaf", run: () => writeForOverleaf() },
    { keys: ["e"], does: "Edit the last thing you wrote in it", marks: ".cmt-edit", run: () => pressCard(".cmt-edit") },
    { keys: ["c"], does: "Add a paper note", run: () => $("#paper-comment-btn").click() },
    { keys: ["Escape"], does: "Leave a box: a reply or an edit being written is dropped, a reply for Overleaf keeps its words" },
  ]],
  ["Change", [
    { keys: ["R"], does: "Resolve it, or reopen it when it is not open", button: ["cmt-resolve", "cmt-reopen"], picked: true,
      marks: (comment) => resolveOrReopen(comment),
      run: () => pressPickedOr(["#resolve-picked-btn", "#reopen-picked-btn"], resolveOrReopen(focused())),
      range: () => pressForRange(["#resolve-picked-btn", "#reopen-picked-btn"]) },
    { keys: ["A"], does: "Archive it", button: ["cmt-archive"], picked: true,
      run: () => pressPickedOr(["#archive-picked-btn"], ".cmt-archive"),
      range: () => pressForRange(["#archive-picked-btn"]) },
    { keys: ["aa"], does: "Apply its suggestion to the file, or accept a suggested edit on Overleaf",
      button: ["sugg-apply", "change-accept"], run: () => pressCard(".sugg-apply, .change-accept") },
    { keys: ["ss"], does: "Send its reply to Overleaf, for everyone in the project", button: ["overleaf-send"], picked: true,
      run: () => pressPickedOr(["#send-picked-btn"], ".overleaf-send"),
      range: () => pressForRange(["#send-picked-btn"]) },
    { keys: ["dd"], does: "Delete it, once you say yes, or reject a suggested edit on Overleaf",
      button: ["cmt-delete", "change-reject"], run: () => pressCard(".cmt-delete, .change-reject") },
  ]],
  ["Pick and range", [
    { keys: ["x"], does: "Pick it, or let it go, and with a range held the whole range", marks: ".cmt-head",
      run: () => pickFocused(), range: () => pickRange() },
    { keys: ["v"], does: "Hold a range from it, or from the first comment, which the moving keys stretch: R, A, ss "
      + "and x then act on the whole range, and Escape or v lets it go", run: () => holdRange(), range: () => letRangeGo() },
    { keys: ["*"], does: "Pick every comment, or let them all go", run: () => $("#pick-all-btn").click() },
  ]],
  ["View", [
    { keys: ["f"], does: "Next view in the list atop the tab: open, resolved, …", run: () => cycleStatus(1) },
    { keys: ["F"], does: "Previous view", run: () => cycleStatus(-1) },
    { keys: ["t"], does: "Next tab", scope: "sidebar", run: () => cycleTab(1) },
    { keys: ["T"], does: "Previous tab", scope: "sidebar", run: () => cycleTab(-1) },
    { keys: ["?"], does: "These keys", scope: "page", run: () => showCommentKeys() },
  ]],
];

// The button R presses on a comment.
function resolveOrReopen(comment) {
  return comment?.status === "open" ? ".cmt-resolve" : ".cmt-reopen";
}

// Each control of the ringed comment a key works, marked with that key's letter for the list to
// show. A key that acts on the picked comments, while there are some, or on a held range, works
// none of the card's controls, and none is marked for it.
function markKeys() {
  const comment = focused();
  const card = comment && $(`#comments-list [data-comment-id="${CSS.escape(comment.id)}"]`);
  if (!card || rangeIds().length > 0) return;
  const picking = state.comments.some((item) => state.picked.has(item.id));
  for (const entry of COMMENT_KEYS.flatMap(([, group]) => group)) {
    if (picking && entry.picked) continue;
    const where = typeof entry.marks === "function" ? entry.marks(comment)
      : entry.marks ?? entry.button?.map((name) => `.${name}`).join(", ");
    if (!where) continue;
    const found = card.querySelectorAll(where);
    found[found.length - 1]?.setAttribute("data-key", KEY_NAMES[entry.keys[0]] ?? entry.keys[0]);
  }
}

// The letters show once the keys move through the list, as the reader takes to them, and go at
// a touch of the mouse or a box taken to type in.
function showKeyLetters(shown) {
  $("#comments-list").classList.toggle("show-keys", shown && state.keyLetters !== "off");
}

function applyKeyLetters() {
  for (const button of document.querySelectorAll("[data-letters-choice]")) {
    button.setAttribute("aria-checked", String(button.dataset.lettersChoice === (state.keyLetters ?? "on")));
  }
  if (state.keyLetters === "off") showKeyLetters(false);
}

// The key that presses a comment's button, for the button's title.
function keyHint(button) {
  const entry = COMMENT_KEYS.flatMap(([, group]) => group).find((item) => item.button?.includes(button));
  return entry ? ` (${entry.keys.join(", ")})` : "";
}

function focused() {
  return state.comments.find((comment) => comment.id === state.focusedCommentId);
}

// The focused comment's card, opened unless asked not to, since a closed card shows none of
// its buttons.
function focusedCard(open = true) {
  const comment = focused();
  if (comment === undefined) return null;
  if (open && !state.expanded.has(comment.id)) {
    state.expanded.add(comment.id);
    renderComments();
  }
  return $(`#comments-list [data-comment-id="${CSS.escape(comment.id)}"]`);
}

// The last of the focused card's buttons that match, which for Edit is the last thing written.
function pressCard(selector) {
  const buttons = focusedCard()?.querySelectorAll(selector) ?? [];
  buttons[buttons.length - 1]?.click();
}

// With comments picked, the row's first button that can act on them; else the focused card's.
function pressPickedOr(rows, card) {
  if (state.comments.some((comment) => state.picked.has(comment.id))) {
    rows.map((selector) => $(selector)).find((button) => !button.hidden && !button.disabled)?.click();
  } else {
    pressCard(card);
  }
}

// Where the keys keep the ringed card's middle: a third of the way down the list, its top never
// above the list's, so the eye stays put while the comments move under it. The list's two ends
// hold it back where it cannot scroll.
const RING_AT = 1 / 3;

// The keys move the ring alone: every card stays as it was, and the list scrolls at once, not
// smoothly, to put the ring in its place.
function moveFocus(to) {
  const last = state.comments.length - 1;
  if (last < 0) return;
  const comment = state.comments[to(state.comments.findIndex((item) => item.id === state.focusedCommentId), last)];
  state.focusedCommentId = comment.id;
  renderComments();
  const card = $(`#comments-list [data-comment-id="${CSS.escape(comment.id)}"]`);
  const list = $("#comments-list");
  if (!card) return;
  const box = list.getBoundingClientRect();
  const it = card.getBoundingClientRect();
  list.scrollTop += it.top - box.top - Math.max(0, list.clientHeight * RING_AT - it.height / 2);
}

// The keys taken into the source at the ringed comment's words, as K in the editor takes them
// from a comment's words to the comment. A comment with no place in the source, as a paper note,
// leaves the editor's cursor where it is.
async function enterSource() {
  const comment = focused();
  if (comment === undefined) return;
  const place = comment.anchor.kind === "source_range" ? comment.resolved_source ?? comment.anchor : comment.resolved_source;
  if (state.view === "pdf") await setWorkspaceView("source");
  if (place && await openSource(place.file)) {
    showSourcePlace(place.line_start - 1, comment.stale ? 0 : place.column_start ?? 0);
  }
  state.editor?.focus();
}

// A card opened shows its end, where the thread's latest words and the draft for Overleaf are,
// before its top when it is taller than the list, and the paper or the source shows where the
// comment is. The keys stay with the list.
function foldFocused() {
  const comment = focused();
  if (comment === undefined) return;
  const opening = !state.expanded.has(comment.id);
  if (opening) state.expanded.add(comment.id);
  else state.expanded.delete(comment.id);
  renderComments();
  const card = $(`#comments-list [data-comment-id="${CSS.escape(comment.id)}"]`);
  const list = $("#comments-list");
  const below = card ? card.getBoundingClientRect().bottom - list.getBoundingClientRect().bottom + 8 : 0;
  if (!opening) return;
  if (below > 0) list.scrollTop += below;
  jumpToComment(comment.id).catch((error) => console.error(error));
}

function writeForOverleaf() {
  const box = focusedCard()?.querySelector(".overleaf-reply-input");
  if (!box) return;
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);
}

// A click on the card's box, so picking by key is picking by mouse.
function pickFocused() {
  focusedCard(false)?.querySelector(".comment-pick")?.click();
}

// The comments from the one the range started at to the ringed one, both ends included, or
// none while no range is held or its start has left the view.
function rangeIds() {
  const ids = state.comments.map((comment) => comment.id);
  const from = ids.indexOf(state.rangeFrom);
  const to = ids.indexOf(state.focusedCommentId);
  return from === -1 || to === -1 ? [] : ids.slice(Math.min(from, to), Math.max(from, to) + 1);
}

function holdRange() {
  const comment = focused() ?? state.comments[0];
  if (comment === undefined) return;
  state.focusedCommentId = state.rangeFrom = comment.id;
  renderComments();
}

function letRangeGo() {
  state.rangeFrom = null;
  renderComments();
}

// The range picked, or let go when all of it is picked already.
function pickRange() {
  const ids = rangeIds();
  const all = ids.every((id) => state.picked.has(id));
  for (const id of ids) {
    if (all) state.picked.delete(id);
    else state.picked.add(id);
  }
  letRangeGo();
}

// What the row's button does to the picked comments, done to the range. The comments picked
// before stay picked: the button reads which comments it acts on as it is pressed.
function pressForRange(rows) {
  const kept = state.picked;
  state.picked = new Set(rangeIds());
  state.rangeFrom = null;
  renderPickedActions();
  rows.map((selector) => $(selector)).find((button) => !button.hidden && !button.disabled)?.click();
  state.picked = kept;
  renderComments();
}

function cycleStatus(step) {
  const filter = $("#comment-filter");
  const views = [...filter.options];
  filter.value = views[(filter.selectedIndex + step + views.length) % views.length].value;
  filter.dispatchEvent(new Event("change"));
}

function cycleTab(step) {
  const tabs = $$(".tab-btn").filter((button) => !button.hidden);
  const at = tabs.findIndex((button) => button.classList.contains("active"));
  tabs[(at + step + tabs.length) % tabs.length].click();
}

function showCommentKeys() {
  const list = $("#keys-list");
  clear(list);
  // One grid for every group, so the words line up down the whole list.
  for (const [group, entries] of COMMENT_KEYS) {
    list.append(h("h4", { text: group }), ...entries.flatMap((entry) => [
      h("span", { class: "keys-of" }, ...entry.keys.map((keys) => h("kbd", { text: KEY_NAMES[keys] ?? keys }))),
      h("span", { text: entry.does }),
    ]));
  }
  const picked = COMMENT_KEYS.flatMap(([, group]) => group).filter((entry) => entry.picked).map((entry) => entry.keys[0]);
  $("#keys-picked").textContent = `With comments picked, ${picked.slice(0, -1).join(", ")} and ${picked.at(-1)} act on them.`;
  $("#keys-dialog").showModal();
}

function attachCommentKeys() {
  // A click on a comment puts the ring on it, as a notebook selects the cell clicked, and a
  // click anywhere in the list gives it the keys, from the editor too.
  const list = $("#comments-list");
  list.addEventListener("mousedown", (event) => {
    const card = event.target.closest(".cmt");
    if (card !== null) {
      state.focusedCommentId = card.dataset.commentId;
      for (const node of list.querySelectorAll(".cmt.is-focused")) node.classList.remove("is-focused");
      card.classList.add("is-focused");
    }
    if (!event.target.closest("textarea, input, select, button, a, [contenteditable]")) {
      list.focus({ preventScroll: true });
    }
  });
  const entries = COMMENT_KEYS.flatMap(([, group]) => group).filter((entry) => entry.run);
  const byKeys = new Map(entries.flatMap((entry) => entry.keys.map((keys) => [keys, entry])));
  // A letter pressed twice: the first waits a second for the second.
  const firsts = new Set([...byKeys.keys()].filter((keys) => keys.length === 2).map((keys) => keys[0]));
  let first = "";
  let timer = null;
  document.addEventListener("keydown", (event) => {
    const target = event.target;
    // Ctrl+Alt+K, Cmd+Option+K on macOS, is K with the editor's own keys: read by where K sits,
    // since Option makes it another letter.
    const editorK = (event.ctrlKey || event.metaKey) && event.altKey && event.code === "KeyK";
    if ((!editorK && (event.metaKey || event.ctrlKey || event.altKey)) || document.querySelector("dialog[open]")) return;
    if (target.isContentEditable || target.closest?.("textarea, select, [contenteditable]")
        || (target.tagName === "INPUT" && !["checkbox", "radio"].includes(target.type))) return;
    // Space on a button or a box presses it.
    if (event.key === " " && target.closest?.("button, a, summary, input")) return;
    // A Korean input method gives a letter key as the letter it composes.
    const typed = event.isComposing || event.key === "Process" || COMPOSED.test(event.key)
      ? keyByPosition(event) : event.key;
    // Shift widens Space from the ringed card to every card.
    const key = editorK ? "Ctrl+Alt+K" : typed === " " && event.shiftKey ? "Shift+Space" : typed;
    if (key === undefined) return;
    clearTimeout(timer);
    if (key === "Escape") {
      first = "";
      if (rangeIds().length > 0) {
        event.preventDefault();
        letRangeGo();
      } else if (state.activeForm) {
        event.preventDefault();
        state.activeForm = null;
        renderComments();
      }
      return;
    }
    const keys = byKeys.has(first + key) ? first + key : key;
    first = "";
    const entry = byKeys.get(keys);
    if (entry === undefined) {
      if (firsts.has(keys)) {
        first = keys;
        timer = setTimeout(() => { first = ""; }, 1000);
      }
      return;
    }
    const sidebar = !$(".layout").classList.contains("sidebar-collapsed");
    const scope = entry.scope ?? "list";
    if (scope === "sidebar" ? !sidebar
      : scope === "list" ? !sidebar || !$("#tab-comments").classList.contains("active") : false) return;
    // Held down, only moving repeats: a held R would resolve one comment after another.
    if (event.repeat && !entry.repeats) return;
    if (rangeIds().length > 0) {
      if (entry.range === undefined && !entry.inRange) return;
      event.preventDefault();
      (entry.range ?? entry.run)();
      return;
    }
    event.preventDefault();
    entry.run();
    if (scope === "list") showKeyLetters(true);
  });
  document.addEventListener("pointerdown", () => showKeyLetters(false), true);
  document.addEventListener("pointermove", (event) => {
    if (event.pointerType === "mouse" && Math.abs(event.movementX) + Math.abs(event.movementY) > 2) showKeyLetters(false);
  }, true);
  document.addEventListener("focusin", (event) => {
    if (event.target.matches?.("textarea, select, [contenteditable], input:not([type=checkbox]):not([type=radio])")) {
      showKeyLetters(false);
    }
  });
  for (const button of document.querySelectorAll("[data-letters-choice]")) {
    button.addEventListener("click", () => {
      state.keyLetters = button.dataset.lettersChoice === "off" ? "off" : null;
      try { localStorage.setItem("texMcpKeyLetters", button.dataset.lettersChoice); } catch { /* the choice lasts this visit */ }
      applyKeyLetters();
    });
  }
  try { state.keyLetters = localStorage.getItem("texMcpKeyLetters") === "off" ? "off" : null; } catch { /* letters show */ }
  applyKeyLetters();
}

function setSidebarCollapsed(collapsed) {
  $(".layout").classList.toggle("sidebar-collapsed", collapsed);
  $("#sidebar-toggle-btn").classList.toggle("active", collapsed);
  localStorage.setItem("sidebarCollapsed", collapsed ? "1" : "0");
}

function attachSidebarToggle() {
  const closeMore = () => {
    $("#more-menu").hidden = true;
    $("#more-btn").setAttribute("aria-expanded", "false");
  };
  $("#more-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    const open = $("#more-menu").hidden;
    $("#download-pdf-btn").disabled = !state.pdfDigest;
    $("#more-menu").hidden = !open;
    $("#more-btn").setAttribute("aria-expanded", String(open));
  });
  // A choice in the menu closes it.
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".more") || event.target.closest(".more-menu button")) closeMore();
  });
  $("#settings-btn").addEventListener("click", () => showSettings());
  $("#settings-menu-btn").addEventListener("click", () => showSettings());
  $("#download-pdf-btn").addEventListener("click", () => h("a", { href: "/pdf/download", download: "" }).click());
  $("#overleaf-connect-btn").addEventListener("click", async () => {
    const link = $("#overleaf-link").value.trim();
    if (!link) return;
    if (await connectOverleaf({ project: link }, "Connected. Who is on Overleaf shows in the top bar.")) {
      $("#overleaf-link").value = "";
    }
  });
  $("#overleaf-link").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); $("#overleaf-connect-btn").click(); }
  });
  $("#overleaf-disconnect-btn").addEventListener("click", () => {
    if (confirm("Disconnect this paper from its Overleaf project? Nothing is sent or followed until it is connected again.")) {
      connectOverleaf({ project: null }, "Disconnected.");
    }
  });
  for (const box of $$('#overleaf-mode input[name="overleaf-mode"]')) {
    box.addEventListener("change", () => switchOverleafMode(box.value === "live"));
  }
  $("#overleaf-live-btn").addEventListener("click", () => showSettings());
  // Ctrl+Enter (Cmd+Enter) compiles from anywhere but a field being written in, where it
  // sends what is written, and an open dialog.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
    const inEditor = event.target.closest?.("#source-editor");
    if (document.querySelector("dialog[open]")
      || (!inEditor && event.target.matches?.("textarea, input, select, [contenteditable]"))) return;
    event.preventDefault();
    event.stopPropagation();
    compileFromKeys().catch((error) => alert(`Could not compile: ${error.message}`));
  }, true);
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
  const overleafFailed = (error) => showSettings(`Could not reach the server: ${error.message}`);
  $("#overleaf-cookie-save").addEventListener("click", () => saveOverleafCookie().catch(overleafFailed));
  $("#overleaf-cookie").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();  // Enter keeps the cookie rather than closing the dialog.
    saveOverleafCookie().catch(overleafFailed);
  });
  $("#overleaf-cookie-forget").addEventListener("click", () => forgetOverleafCookie().catch(overleafFailed));
  $("#overleaf-sync-comments-btn").addEventListener("click", () => syncOverleafComments().catch(overleafFailed));
  $("#git-pull-btn").addEventListener("click", () => overleafGitAction("pull").catch(overleafFailed));
  $("#git-push-btn").addEventListener("click", () =>
    (state.overleafFiles ? overleafGitAction("push") : openCommitDialog()).catch(overleafFailed));
  $("#commit-form").addEventListener("submit", () => {
    const files = state.committing;
    overleafGitAction("push", files.length ? { message: $("#commit-message").value.trim(), files } : undefined)
      .catch(overleafFailed);
  });
  $("#commit-cancel").addEventListener("click", () => $("#commit-dialog").close());
  window.addEventListener("focus", () => {
    if (Date.now() - state.overleafGitRead > 60000) readOverleafGitSoon(0);
    readSuggestedEdits();
  });
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.addEventListener("click", () => {
      const choice = button.dataset.themeChoice;
      themeChosen = THEMES.includes(choice) ? choice : null;
      try {
        if (themeChosen === null) localStorage.removeItem("texMcpTheme");
        else localStorage.setItem("texMcpTheme", themeChosen);
      } catch { /* the choice lasts this visit */ }
      applyTheme();
    });
  }
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (themeChosen === null) applyTheme();
  });
  applyTheme();
  // The top bar's and the panels' text at the size Settings chooses, kept for next time.
  const applyTextSize = (percent) => {
    document.documentElement.style.setProperty("--text-scale", String(percent / 100));
    for (const button of document.querySelectorAll("[data-text-choice]")) {
      button.setAttribute("aria-checked", String(Number(button.dataset.textChoice) === percent));
    }
  };
  for (const button of document.querySelectorAll("[data-text-choice]")) {
    button.addEventListener("click", () => {
      try { localStorage.setItem("texMcpTextSize", button.dataset.textChoice); } catch { /* the choice lasts this visit */ }
      applyTextSize(Number(button.dataset.textChoice));
    });
  }
  let textSize = "100";
  try { textSize = localStorage.getItem("texMcpTextSize") ?? "100"; } catch { /* the page's own size */ }
  applyTextSize($(`[data-text-choice="${CSS.escape(textSize)}"]`) ? Number(textSize) : 100);
  for (const button of document.querySelectorAll("[data-send-choice]")) {
    button.addEventListener("click", () => {
      state.sendKey = button.dataset.sendChoice;
      try { localStorage.setItem("texMcpSendKey", state.sendKey); } catch { /* the choice lasts this visit */ }
      applySendKey();
    });
  }
  try { state.sendKey = localStorage.getItem("texMcpSendKey"); } catch { /* shift with enter sends */ }
  applySendKey();
  $("#sidebar-toggle-btn").addEventListener("click", () =>
    setSidebarCollapsed(!$(".layout").classList.contains("sidebar-collapsed")));
  document.addEventListener("keydown", (event) => {
    const tag = event.target.tagName.toUpperCase();
    if (["TEXTAREA", "INPUT", "SELECT"].includes(tag)) return;
    if (event.target.isContentEditable || document.querySelector("dialog[open]")) return;
    if (event.metaKey || event.ctrlKey || event.altKey || event.key !== "\\") return;
    event.preventDefault();
    setSidebarCollapsed(!$(".layout").classList.contains("sidebar-collapsed"));
  });
  setSidebarCollapsed(localStorage.getItem("sidebarCollapsed") === "1");
}

function attachSidebarResize() {
  const grip = $("#sidebar-grip");
  const layout = $(".layout");
  const setPanelHeight = (pixels) => {
    const limited = Math.round(Math.max(90, Math.min(pixels, window.innerHeight - 140)));
    layout.style.setProperty("--tex-mcp-panel-height", `${limited}px`);
    return limited;
  };
  const savedHeight = Number(localStorage.getItem("texMcpPanelHeight"));
  if (savedHeight > 0) layout.style.setProperty("--tex-mcp-panel-height", `${savedHeight}px`);

  $("#sidebar").addEventListener("click", (event) => {
    if (performance.now() - state.panelDraggedAt >= 400) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);

  grip.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    grip.setPointerCapture(event.pointerId);
    const bottom = layout.getBoundingClientRect().bottom;
    // A split stacked above keeps its bar where it is on the screen while this one moves.
    const splitY = state.split.holdable() ? state.split.centerY() : null;
    let pointerY = null;
    let frame = null;
    let height = null;
    const apply = () => {
      frame = null;
      height = setPanelHeight(bottom - pointerY);
      if (splitY !== null) state.split.holdAt(splitY);
      state.editor?.resize();
    };
    const move = (moved) => {
      pointerY = moved.clientY;
      if (frame === null) frame = requestAnimationFrame(apply);
    };
    const done = (ended) => {
      if (frame !== null) cancelAnimationFrame(frame);
      if (pointerY !== null) apply();
      if (ended.cancelable) ended.preventDefault();
      if (pointerY !== null) state.panelDraggedAt = performance.now();
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", done);
      grip.removeEventListener("pointercancel", done);
      if (height !== null) localStorage.setItem("texMcpPanelHeight", String(height));
      if (splitY !== null) state.split.keep();
      state.editor?.resize();
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", done);
    grip.addEventListener("pointercancel", done);
  });
  // The keys move the bar as the split's do: up and down by a step, Home and End to the
  // ends. A split stacked above keeps its bar where it is, as it does through a drag.
  grip.addEventListener("keydown", (event) => {
    if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const splitY = state.split.holdable() ? state.split.centerY() : null;
    const current = $("#sidebar").getBoundingClientRect().height;
    const height = setPanelHeight(event.key === "Home" ? Infinity : event.key === "End" ? 0
      : current + (event.key === "ArrowUp" ? 40 : -40));
    localStorage.setItem("texMcpPanelHeight", String(height));
    if (splitY !== null) {
      state.split.holdAt(splitY);
      state.split.keep();
    }
    state.editor?.resize();
  });
}

// The panel's left edge, where the panel sits beside the PDF: a drag or the arrow keys
// set its width, kept per viewer. Stacked layouts hide this edge and use the grip above.
// The Preview keeps its zoom mode, such as fitting the page width, through a change of
// the room it has.
function refitPdf() {
  const zoomMode = state.zoom?.getState().zoomLevel;
  requestAnimationFrame(() => { if (typeof zoomMode === "string") state.zoom?.requestZoom(zoomMode); });
}

function attachSidebarWidthResize() {
  const grip = $("#sidebar-width-grip");
  const layout = $(".layout");
  const setWidth = (pixels) => {
    const limited = Math.round(Math.max(240, Math.min(pixels, window.innerWidth * 0.7)));
    layout.style.setProperty("--tex-mcp-sidebar-width", `${limited}px`);
    return limited;
  };
  const save = (width) => {
    try { localStorage.setItem("texMcpSidebarWidth", String(width)); } catch { /* per-viewer only */ }
  };
  let saved = 0;
  try { saved = Number(localStorage.getItem("texMcpSidebarWidth")); } catch { /* per-viewer only */ }
  if (saved > 0) layout.style.setProperty("--tex-mcp-sidebar-width", `${saved}px`);

  grip.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    try { grip.setPointerCapture(event.pointerId); } catch { /* synthetic or lost pointer */ }
    grip.classList.add("dragging");
    document.body.classList.add("sidebar-resizing");
    const right = layout.getBoundingClientRect().right;
    let pointerX = null;
    let frame = null;
    let width = null;
    const apply = () => {
      frame = null;
      width = setWidth(right - pointerX);
      state.editor?.resize();
    };
    const move = (moved) => {
      pointerX = moved.clientX;
      if (frame === null) frame = requestAnimationFrame(apply);
    };
    const done = () => {
      if (frame !== null) cancelAnimationFrame(frame);
      if (pointerX !== null) {
        apply();
        state.panelDraggedAt = performance.now();
      }
      grip.classList.remove("dragging");
      document.body.classList.remove("sidebar-resizing");
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", done);
      grip.removeEventListener("pointercancel", done);
      if (width !== null) save(width);
      state.editor?.resize();
      refitPdf();
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", done);
    grip.addEventListener("pointercancel", done);
  });
  grip.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = $("#sidebar").getBoundingClientRect().width;
    const width = setWidth(event.key === "Home" ? Infinity : event.key === "End" ? 0
      : current + (event.key === "ArrowLeft" ? 40 : -40));
    save(width);
    state.editor?.resize();
    refitPdf();
  });
}

// ----- Workspace layout -----
// The workspace shows the Preview, Source and the translation as the view asks: one pane,
// or two or three side by side or stacked, each pair parted by a grip dragged to share the
// room between them. A layout is a pane's name or [direction, first, second, grip]: "row"
// puts the two side by side and "col" stacks them. Preview and Source put the translation
// under their pane; Split puts it where the layout menu says. At 950px wide or less every
// pair is stacked, as Split always stacked its two panes there.
const WORKSPACE_PANES = { preview: "#pdf-pane", source: "#source-pane", translation: "#translation-pane" };
const WORKSPACE_GRIPS = { split: "#split-grip", tgrip: "#translation-grip" };
const TRANSLATION_PLACES = ["preview", "source", "column", "replace"];
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

function stackLayout(node) {
  return typeof node === "string" ? node : ["col", stackLayout(node[1]), stackLayout(node[2]), node[3]];
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

function layoutNode(node, grip) {
  if (typeof node === "string") return null;
  if (node[3] === grip) return node;
  return layoutNode(node[1], grip) ?? layoutNode(node[2], grip);
}

function layoutPanes(node) {
  return typeof node === "string" ? [node] : [...layoutPanes(node[1]), ...layoutPanes(node[2])];
}

function layoutShows(pane) {
  return layoutPanes(workspaceLayout.tree).includes(pane);
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

function refitWorkspace() {
  state.editor?.resize();
  const zoomMode = state.zoom?.getState().zoomLevel;
  if (typeof zoomMode === "string") state.zoom?.requestZoom(zoomMode);
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
  const menu = $("#layout-select");
  menu.classList.toggle("hidden", !(state.view === "split" && translationShown()));
  menu.value = workspaceLayout.place;
  state.editor?.resize();
  if (refit) requestAnimationFrame(refitWorkspace);
}

// Source beside the Preview is Split, unless Split's translation stands in for Source:
// then the Source view, where the translation sits under the editor.
async function showSourceBesidePreview() {
  if (state.view !== "split") await setWorkspaceView("split");
  if (!layoutShows("source")) await setWorkspaceView("source");
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
  $("#layout-select").addEventListener("change", (event) => {
    workspaceLayout.place = event.target.value;
    keepLayout();
    applyLayout();
    if (layoutShows("source")) initializeSourceEditor().catch((error) => console.error(error));
  });
  stackedWorkspace.addEventListener("change", () => applyLayout());
  applyLayout();
}

// An iPhone zooms the page into a field whose text is under 16px the moment it takes the
// cursor, and leaves it zoomed. A viewport that may not grow past 1, from a finger landing
// on a field until just after it has the cursor, keeps that from happening, and pinching
// zooms the page as before at any other moment. A page already zoomed in is left as it is.
function holdZoomOnFields() {
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
    if (event.touches.length === 1 && event.target.closest?.("textarea, input, select")
      && (window.visualViewport?.scale ?? 1) < 1.01) {
      clearTimeout(timer);
      viewport.content = `${free}, maximum-scale=1`;
    } else {
      release(0);
    }
  }, { capture: true, passive: true });
  document.addEventListener("touchend", () => release(500), { capture: true, passive: true });
  document.addEventListener("focusin", () => release(500), true);
}

// A pinch on a trackpad reaches the page as a wheel turned with Ctrl held. Over the Preview
// the viewer zooms the PDF with it. Anywhere else the browser would magnify the whole page
// and push the panel's right edge out of the window, so there it does nothing. Cmd or Ctrl
// with + and - still zoom the page.
function holdPinchOutsidePreview() {
  document.addEventListener("wheel", (event) => {
    if (event.ctrlKey && !event.target.closest?.("#pdf-viewer")) event.preventDefault();
  }, { passive: false });
}

async function init() {
  holdZoomOnFields();
  holdPinchOutsidePreview();
  attachKeyboardNavigation();
  attachSidebarToggle();
  attachSidebarResize();
  attachSidebarWidthResize();
  attachWorkspaceLayout();
  window.addEventListener("beforeunload", (event) => {
    if (!state.sourceDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  document.addEventListener("pointerdown", (event) => {
    clearGotoHighlight();
    if (!event.target.closest("#reference-preview")) hideReferencePreview();
  }, { capture: true });
  $("#reference-preview").addEventListener("click", () => {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents($("#reference-preview-text"));
    selection.removeAllRanges();
    selection.addRange(range);
  });
  $("#compile-btn").addEventListener("click", () => compile());
  $("#translation-btn").addEventListener("click", () => setTranslation(!translation.on));
  $("#translation-file").addEventListener("change", () => scheduleTranslation(0));
  $("#translation-body").style.fontSize = `${translationFontSize()}px`;
  $("#translation-font-smaller-btn").addEventListener("click", () => stepTranslationFontSize(-1));
  $("#translation-font-larger-btn").addEventListener("click", () => stepTranslationFontSize(1));
  try { if (localStorage.getItem("texMcpTranslation") === "1") setTranslation(true); } catch { /* per-viewer only */ }
  $("#source-save-btn").addEventListener("click", () => {
    saveSource().catch((error) => alert(`Could not save: ${error.message}`));
  });
  $("#source-reload-btn").addEventListener("click", () => {
    reloadSource().catch((error) => alert(`Could not reload: ${error.message}`));
  });
  $("#source-font-smaller-btn").addEventListener("click", () => stepSourceFontSize(-1));
  $("#source-font-larger-btn").addEventListener("click", () => stepSourceFontSize(1));
  $("#source-wrap-btn").addEventListener("click", () => {
    localStorage.setItem("texMcpSourceWrap", sourceWraps() ? "0" : "1");
    applySourceWrap();
  });
  $("#source-vim-btn").addEventListener("click", () => {
    localStorage.setItem("texMcpVimKeys", vimKeys() ? "0" : "1");
    applyVimKeys();
    state.editor?.focus();
  });
  applySourceWrap();
  applyVimKeys();
  $("#source-comment-btn").addEventListener("click", openSourceCommentCompose);
  $("#source-pdf-btn").addEventListener("click", () => {
    showSourceSelectionInPdf().catch((error) => alert(`Could not show it in the Preview: ${error.message}`));
  });
  $("#source-file").addEventListener("change", (event) => {
    openSource(event.target.value).catch((error) => alert(`Could not open source: ${error.message}`));
  });
  for (const button of $$(".view-tab")) {
    button.addEventListener("click", () => {
      setWorkspaceView(button.dataset.view).catch((error) => alert(error.message));
    });
  }
  // Ctrl+Alt+Enter (Cmd+Option+Enter) presses the bell from anywhere on the page, the
  // editor included, but not over a dialog.
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || !event.altKey || !(event.ctrlKey || event.metaKey)) return;
    if (document.querySelector("dialog[open]")) return;
    event.preventDefault();
    event.stopPropagation();
    if (!$("#call-agent-btn").disabled) $("#call-agent-btn").click();
  }, true);
  $("#call-agent-btn").addEventListener("click", () => callAgent());
  $("#fold-all-btn").addEventListener("click", foldAll);
  $("#pick-all-btn").addEventListener("click", pickAll);
  for (const [id, action, word] of [["#accept-picked-btn", "accept", "Accept"], ["#reject-picked-btn", "reject", "Reject"]]) {
    $(id).addEventListener("click", () => {
      const edits = state.comments.filter((item) => item.suggested && state.picked.has(item.id));
      if (edits.length === 0 || !confirm(`${word} ${edits.length} suggested edit${edits.length === 1 ? "" : "s"} `
        + "on Overleaf, for everyone in the project?")) return;
      reviewSuggestedEdits(action, edits).catch((error) => alert(error.message));
    });
  }
  $("#send-picked-btn").addEventListener("click", async () => {
    const button = $("#send-picked-btn");
    const ids = button.dataset.ids ? button.dataset.ids.split(" ") : [];
    if (ids.length === 0 || !confirm(`Send ${replies(ids.length)} to Overleaf, for everyone in the project?`)) return;
    for (const id of ids) state.sending.add(id);
    renderComments();
    try {
      await sendPickedToOverleaf(ids);
    } catch (error) {
      alert(`Could not send: ${error.message}`);
    } finally {
      for (const id of ids) state.sending.delete(id);
      renderComments();
    }
  });
  // The row is fitted again whenever it, or a button that is always in it, changes width.
  const row = $(".filter-row");
  const refit = new ResizeObserver(() => fitCommentRow());
  refit.observe(row);
  for (const node of row.children) if (node.id !== "comment-rare") refit.observe(node);
  $("#comment-rare-btn").addEventListener("click", (event) => {
    event.stopPropagation();
    const open = !$("#comment-rare").classList.contains("open");
    $("#comment-rare").classList.toggle("open", open);
    $("#comment-rare-btn").setAttribute("aria-expanded", String(open));
  });
  // A choice behind the dots closes them, and so does a click anywhere else.
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#comment-rare") || event.target.closest(".comment-rare-group button")) closeCommentRare();
  });
  // The buttons carry the ids they were drawn with, so a press acts on what its label counted.
  for (const [id, status] of [["#resolve-picked-btn", "resolved"], ["#archive-picked-btn", "archived"], ["#reopen-picked-btn", "open"]]) {
    $(id).addEventListener("click", async () => {
      const button = $(id);
      const ids = button.dataset.ids ? button.dataset.ids.split(" ") : [];
      if (ids.length === 0) return;
      button.disabled = true;
      try {
        await setPickedStatus(ids, status);
      } catch (error) {
        alert(`Could not save: ${error.message}`);
      } finally {
        button.disabled = false;
      }
    });
  }
  $("#paper-comment-btn").addEventListener("click", () =>
    openCompose({ kind: "paper" }, "Paper-level comment"));
  $("#keys-btn").addEventListener("click", () => showCommentKeys());
  $("#compose-form").addEventListener("submit", (event) => {
    submitCompose(event).catch((error) => alert(error.message));
  });
  for (const box of $$("#compose-form textarea")) {
    sendOn(box, () => $("#compose-form").requestSubmit());
  }
  $("#compose-cancel").addEventListener("click", (event) => {
    event.preventDefault();
    $("#compose-dialog").close();
    clearPendingSelection();
  });
  renderCommentFilter();
  // A view chosen in the list, and a comment tab chosen, give the comment keys back to the
  // comments: the list would keep them, and Safari focuses no button it clicks.
  $("#comment-filter").addEventListener("change", () => {
    setCommentFilter($("#comment-filter").value);
    if ($("#comment-filter").value === "changes") readSuggestedEdits();
    $("#comments-list").focus({ preventScroll: true });
    refreshComments().catch((error) => console.error(error));
  });
  for (const button of $$(".tab-btn")) {
    button.addEventListener("click", () => {
      switchTab(button.dataset.tab);
      if ($("#tab-comments").classList.contains("active")) $("#comments-list").focus({ preventScroll: true });
    });
  }

  await refreshPaper();
  await refreshComments();
  // A git clone of the Overleaf project: where it stands shows in the top bar from the start.
  loadOverleafStatus()
    .then((linked) => (linked?.git ? overleafGitAction("fetch") : null))
    .catch((error) => console.error(error));
  if (state.pdfDigest) await initializePdfViewer(null);
  const savedView = localStorage.getItem("workspaceView");
  if (["source", "split"].includes(savedView)) await setWorkspaceView(savedView);
  connectWebSocket();
}

document.addEventListener("DOMContentLoaded", () => {
  init().catch((error) => {
    console.error(error);
    alert(`tex-web failed to start: ${error.message}`);
  });
});
