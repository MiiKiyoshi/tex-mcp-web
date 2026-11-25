// The Preview: the PDF viewer (EmbedPDF), set up afresh for each build's PDF where the reader was
// in the one before, its Area tool and the menu over a selection, which comments on the words or
// shows them in the source, the preview of a reference clicked, and the places the page scrolls
// it to. Its look is pdf-theme.js, the presses it reads first pdf-touch.js, and the marks on its
// pages pdf-marks.js.
//
// createPdfView takes digest(), the digest of the PDF to show; comments, the comment panel as
// pdf-marks.js takes it, with opened(id), whether a comment's card is open; openCompose(anchor,
// label); showWhereWritten({ page, rects, quote }), which shows words selected on a page where
// the source writes them (places.js); and scheduleTranslation(delay). It gives attach(),
// open(view), view(), the place to open the next PDF at, shown(), setTheme(theme), zoomMode() and
// refit(zoomMode), refitSoon(), loaded(), scrolls(), currentPage(), scrollTo(place),
// jumpToPage(page), zoomTo(page, bbox), showTarget(target), selectComment(id, page),
// markComments(), marksReady(), markPlace(page, bbox, rects, look), and compose, the panel's hooks
// for a comment written on the PDF.

import EmbedPDF from "/static/embedpdf/embedpdf.js?v=selection-yellow";
import { $, clear } from "../shared/dom.js";
import { viewerTheme, VIEWER_STYLE } from "./pdf-theme.js";
import { attachPdfPresses } from "./pdf-touch.js";
import { createPdfMarks } from "./pdf-marks.js";
import { setSidebarCollapsed } from "./sidebar.js";

const DOCUMENT_ID = "paper";

function rectToBBox(rect) {
  return [
    rect.origin.x,
    rect.origin.y,
    rect.origin.x + rect.size.width,
    rect.origin.y + rect.size.height,
  ];
}

// The Area tool sits with the pan and pointer tools in the viewer's toolbar and is a mode
// of the viewer as they are. In it a drag on a page draws a box instead of selecting text,
// and the comment written for the box opens when the drag ends. The tool then gives the
// pointer back.
const AREA_MODE = "texArea";

export function createPdfView({ digest, comments, openCompose, showWhereWritten, scheduleTranslation }) {
  const marks = createPdfMarks({ comments });
  const state = {
    viewer: null,
    selection: null,
    areaMode: false,
    annotations: null,
    scroll: null,
    zoom: null,
    layoutReady: false,
    referencePreviewRequest: 0,
    lastViewerPointer: null,
  };

  function pdfUrl() {
    const version = digest() ?? Date.now().toString();
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

  async function initializePdfViewer(pdfView) {
    marks.stop();
    hideReferencePreview();
    const host = $("#pdf-viewer");
    clear(host);
    state.selection = null;
    state.areaMode = false;
    state.annotations = null;
    state.scroll = null;
    state.zoom = null;
    state.layoutReady = false;

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
    viewerStyle.textContent = VIEWER_STYLE;
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
    // Cmd (macOS) or Ctrl + double click: the double click selects the word as usual,
    // and Source opens it beside the PDF.
    root.addEventListener("dblclick", (event) => {
      if (!event.metaKey && !event.ctrlKey) return;
      setTimeout(() => openSelectionInSource()
        .catch((error) => alert(`Could not open the source: ${error.message}`)), 120);
    });
    attachPdfPresses(state.viewer, () => state.areaMode);
    state.annotations = annotationCapability.forDocument(DOCUMENT_ID);
    marks.start(state.viewer, state.annotations);
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

    // A highlight newly selected, as a click on it selects it, opens its comment. The highlights
    // are drawn again each time the list is, and one left selected comes back selected, which is
    // no new choice of the reader's: it took the ring off the comment the reader was on.
    let selectedBefore = null;
    state.annotations.onStateChange((annotationState) => {
      const selectedNow = annotationState.selectedUids.length === 1 ? annotationState.selectedUids[0] : null;
      const chosen = selectedNow !== selectedBefore;
      selectedBefore = selectedNow;
      if (selectedNow === null || !chosen) return;
      const commentId = marks.commentOf(selectedNow);
      if (commentId) {
        setSidebarCollapsed(false);
        comments.showTab();
        const comment = comments.marked().find((item) => item.id === commentId);
        // The comment the list has just opened and shown here is left as the list has it.
        if (comment && !(comment.id === comments.ringed() && comments.opened(comment.id))) {
          comments.show(comment.id).catch((error) => console.error(error));
        }
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
      marks.loaded();
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
    if (!state.selection || !digest()) return;
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
        pdf_digest: digest(),
      },
      `PDF text: "${quote.slice(0, 80)}${quote.length > 80 ? "…" : ""}"`,
    );
  }


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
        { kind: "area", page: pageIndex + 1, bbox: rectToBBox(rect), pdf_digest: digest() },
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
    if (!state.selection || !digest()) return;
    const formatted = state.selection.getFormattedSelection();
    if (formatted.length !== 1) {
      alert("Select text on one page at a time.");
      return;
    }
    const quote = (await state.selection.getSelectedText().toPromise()).join(" ");
    await showWhereWritten({
      page: formatted[0].pageIndex + 1, rects: formatted[0].segmentRects.map(rectToBBox), quote,
    });
  }


  function jumpToPage(page) {
    if (!state.layoutReady || !state.scroll) return;
    state.scroll.scrollToPage({ pageNumber: page, behavior: "smooth", center: true });
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
    if (!target.bbox || !marks.ready()) return;
    marks.place(target.page, target.bbox, target.rects ?? [target.bbox],
      { opacity: 0.55, color: "#fbdc00", contents: target.quote ?? "" });
  }

  // The Preview keeps its zoom mode, such as fitting the page width, through a change of the
  // room it has.
  function refitPdf() {
    const zoomMode = state.zoom?.getState().zoomLevel;
    requestAnimationFrame(() => { if (typeof zoomMode === "string") state.zoom?.requestZoom(zoomMode); });
  }

  function setTheme(theme) {
    state.viewer?.setTheme?.(viewerTheme(theme));
    // The viewer's scroll bars take the page's scheme, which its own theme does not set.
    state.viewer?.setAttribute("data-tex-theme", theme);
  }

  function attach() {
    document.addEventListener("pointerdown", (event) => {
      marks.clearPlace();
      if (!event.target.closest("#reference-preview")) hideReferencePreview();
    }, { capture: true });
    $("#reference-preview").addEventListener("click", () => {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents($("#reference-preview-text"));
      selection.removeAllRanges();
      selection.addRange(range);
    });
  }

  return {
    attach,
    open: initializePdfViewer,
    view: capturePdfView,
    shown: () => Boolean(state.viewer),
    setTheme,
    zoomMode: () => state.zoom?.getState().zoomLevel,
    refit: (zoomMode) => { if (typeof zoomMode === "string") state.zoom?.requestZoom(zoomMode); },
    refitSoon: refitPdf,
    loaded: () => Boolean(state.scroll && state.annotations),
    scrolls: () => Boolean(state.scroll),
    currentPage: () => state.scroll.getCurrentPage(),
    scrollTo: (place) => state.scroll?.scrollToPage(place),
    jumpToPage,
    zoomTo: positionPdfTarget,
    showTarget: showGotoTarget,
    selectComment: marks.select,
    markComments: marks.sync,
    marksReady: marks.ready,
    markPlace: marks.place,
    // The panel's hooks for a comment written on the PDF.
    compose: {
      // The PDF the selection was made on is being rebuilt, or was rebuilt since: a comment is
      // placed only on a selection made on the PDF the server has. What was written waits in the
      // comment box for the next selection.
      refused: (refusal, body) => {
        if (refusal.reason !== "rebuilding" && refusal.reason !== "rebuilt") return null;
        const redo = body.anchor.kind === "area" ? "draw the box again" : "select the text again";
        return { keep: true, say: refusal.reason === "rebuilding"
          ? `The PDF is being rebuilt, so this comment can't be placed on it yet. When the new PDF appears, ${redo}. `
            + "What you wrote will still be in the comment box."
          : `The PDF was rebuilt after you made this selection, so the comment wasn't posted. Please ${redo}. `
            + "What you wrote will still be in the comment box." };
      },
      // The selection the dialog was opened on goes, and the viewer's bubble with it.
      closed: () => {
        if (state.selection) state.selection.clear();
      },
    },
  };
}
