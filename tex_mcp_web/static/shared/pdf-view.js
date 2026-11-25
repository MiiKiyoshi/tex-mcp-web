// The PDF view of the review pages (EmbedPDF): the viewer, set up afresh for each PDF the page
// opens and at the place the reader had in the one before when the page asks; its Area tool; the
// menu over a selection, which comments on the words or shows them in their source; the preview
// of a reference clicked; and the places the page scrolls it to. It draws into the page's
// #pdf-viewer, shows a reference's words in #reference-preview-text inside #reference-preview,
// and loads the viewer from the page's /static/embedpdf/. It takes the page's hooks DEFAULTS
// lists. Its look is pdf-theme.js, the presses it reads first pdf-touch.js, and the marks on its
// pages pdf-marks.js. A page holds none of what the view holds: it reads it, and changes it,
// through the view createPdfView gives.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

import EmbedPDF from "/static/embedpdf/embedpdf.js?v=selection-yellow";
import { $, clear } from "./dom.js";
import { viewerTheme, viewerStyle } from "./pdf-theme.js";
import { attachPdfPresses } from "./pdf-touch.js";
import { createPdfMarks } from "./pdf-marks.js";
import { setSidebarCollapsed } from "./sidebar.js";

const DOCUMENT_ID = "paper";

// The hooks the page gives createPdfView, each with what the view does without it, so a page
// gives only what it changes. prefix, url, openCompose and the comments' members but showTab are
// required. A hook is called each time it is needed, so what it answers may change. A name the
// view does not know is refused.
const DEFAULTS = {
  // The page's name for what it adds to the viewer: its classes and attributes there, as
  // .<prefix>-comment-badge and data-<prefix>-selecting (pdf-theme.js), its Area tool's mode
  // <prefix>Area, its commands, as <prefix>-web:area, and the ids of its marks.
  prefix: null,
  // The address of the PDF open() opens, asked at each open.
  url: null,
  // The root of the route a reference clicked is read from, before
  // /reference-preview?page=&bbox=, which answers { text }.
  base: () => "",
  // The comment panel as the view reaches it: marked(), the open comments its highlights and
  // badges mark; ringed(), the ringed comment's id; opened(id), whether its card is open;
  // show(id), which shows a comment in the panel; showTab(), which first brings the panel's
  // tab into sight; and inSight(), bringIntoSight() and putOutOfSight(), whether the panel is
  // in sight and the two ways to change it, by default the page's sidebar (sidebar.js). A view
  // in a frame of its own, whose page holds the panel, gives its own three.
  comments: {
    marked: null, ringed: null, opened: null, show: null, showTab: () => {},
    inSight: () => !$(".layout").classList.contains("sidebar-collapsed"),
    bringIntoSight: () => setSidebarCollapsed(false),
    putOutOfSight: () => setSidebarCollapsed(true),
  },
  // Opens the new comment dialog on an anchor, with the label it shows (the panel's openCompose).
  openCompose: null,
  // The comment Comment opens over words selected on one page, given { page, bbox, rects, quote,
  // excerpt }: bbox and rects in PDF points, and excerpt the quote's first 80 characters, as a
  // label quotes it. It answers [anchor, label], as openCompose takes them, or null for none.
  words: ({ page, bbox, rects, quote, excerpt }) =>
    [{ kind: "text_selection", quote, selection: { page, bbox, rects } }, `Text on p${page}: "${excerpt}"`],
  // The comment a box drawn with the Area tool opens, given { page, bbox }: [anchor, label], or
  // null for none.
  box: ({ page, bbox }) => [{ kind: "area", page, bbox }, `Area on p${page}`],
  // Shows words selected on one page in the source they are written in, given { page, rects,
  // quote }, the quote as the viewer reads its lines, joined by spaces: the Source button over a
  // selection, and a double click with Cmd (macOS) or Ctrl. Without it neither is there.
  source: null,
  // Told as the PDF turns to another page.
  pageChanged: () => {},
  // Told once the pages of each PDF opened are laid out, which the first page shown tells no
  // turn of.
  layoutReady: () => {},
};

// The hooks the page gives over the defaults, the comments' over their own. A hook the view does
// not know is refused, so a misspelt or retired name does not pass unseen.
function withDefaults(given) {
  const known = (names, defaults, where) => {
    for (const name of names) if (!(name in defaults)) throw new Error(`createPdfView: no hook ${where}${name}`);
  };
  known(Object.keys(given), DEFAULTS, "");
  known(Object.keys(given.comments ?? {}), DEFAULTS.comments, "comments.");
  const host = { ...DEFAULTS, ...given, comments: { ...DEFAULTS.comments, ...given.comments } };
  for (const name of ["prefix", "url", "openCompose"]) {
    if (host[name] == null) throw new Error(`createPdfView: ${name} is required`);
  }
  for (const name of ["marked", "ringed", "opened", "show"]) {
    if (host.comments[name] == null) throw new Error(`createPdfView: comments.${name} is required`);
  }
  return host;
}

function rectToBBox(rect) {
  return [
    rect.origin.x,
    rect.origin.y,
    rect.origin.x + rect.size.width,
    rect.origin.y + rect.size.height,
  ];
}

// The view of the page's PDF, which open() draws into #pdf-viewer. `given` is the page's hooks,
// over DEFAULTS.
//
// It gives the view. Its readers each give the value at the moment they are called: shown(),
// whether a viewer is made; loaded(), whether its PDF is loaded, so that its page can be read and
// its pages marked; laidOut(), whether its pages are laid out, so that a place on them can be
// scrolled to; marksReady(), whether the PDF's own annotations are read, so that the place mark
// can be drawn; currentPage(), the page shown, undefined before a PDF is loaded; view(), the place
// to open the next PDF at, or null before the pages are laid out; and zoomMode(), the zoom, a mode
// such as fitting the page's width, or a level. Its commands: attach(); open(at), which opens the
// PDF url() names afresh, at `at`, a place view() gave, or at its start; setTheme(theme);
// refit(zoomMode) and refitSoon(), which keep a zoom mode through a change of the room the view
// has; scrollTo(place), as EmbedPDF's scrollToPage takes it; jumpToPage(page); zoomTo(page,
// bbox), the page zoomed to a box on it in PDF points, or brought to the middle without one;
// selectComment(id, page); markComments(), the comments marked() gives drawn again;
// markPlace(page, bbox, rects, { opacity, color, contents }), the one mark that shows a place,
// which the next press anywhere takes away; outline(page, bbox, ms), a box outlined for ms
// milliseconds; and clearSelection().
export function createPdfView(given) {
  const host = withDefaults(given);
  const { prefix, comments } = host;
  const marks = createPdfMarks({ prefix, comments });
  // The Area tool sits with the pan and pointer tools in the viewer's toolbar and is a mode
  // of the viewer as they are. In it a drag on a page draws a box instead of selecting text,
  // and the comment written for the box opens when the drag ends. The tool then gives the
  // pointer back.
  const AREA_MODE = `${prefix}Area`;
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
    const target = $("#pdf-viewer");
    clear(target);
    state.selection = null;
    state.areaMode = false;
    state.annotations = null;
    state.scroll = null;
    state.zoom = null;
    state.layoutReady = false;

    state.viewer = EmbedPDF.init({
      type: "container",
      target,
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
        initialDocuments: [{ url: host.url(), documentId: DOCUMENT_ID }],
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
    const style = document.createElement("style");
    style.textContent = viewerStyle(prefix);
    state.viewer.shadowRoot.appendChild(style);
    state.viewer.setAttribute(`data-${prefix}-theme`, document.documentElement.dataset.theme);
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
    state.selection.onBeginSelection(() => state.viewer.setAttribute(`data-${prefix}-selecting`, ""));
    state.selection.onEndSelection(() => state.viewer.removeAttribute(`data-${prefix}-selecting`));
    if (host.source) {
      // Cmd (macOS) or Ctrl + double click: the double click selects the word as usual,
      // and Source shows it where it is written.
      state.viewer.shadowRoot.addEventListener("dblclick", (event) => {
        if (!event.metaKey && !event.ctrlKey) return;
        setTimeout(() => openSelectionInSource().catch(sourceFailed), 120);
      });
    }
    attachPdfPresses(state.viewer, () => state.areaMode, prefix);
    state.annotations = annotationCapability.forDocument(DOCUMENT_ID);
    marks.start(state.viewer, state.annotations);
    state.scroll = scrollCapability.forDocument(DOCUMENT_ID);
    state.scroll.onPageChange(() => host.pageChanged());
    state.zoom = zoomCapability.forDocument(DOCUMENT_ID);

    commands.registerCommand({
      id: `${prefix}-web:comment-selection`,
      label: "Comment",
      icon: "comment",
      action: openTextSelectionCompose,
    });
    const item = (id, commandId) => ({ type: "command-button", id, commandId, variant: "icon-text" });
    const items = [item(`${prefix}-web-comment-selection`, `${prefix}-web:comment-selection`)];
    if (host.source) {
      state.viewer.registerIcon(`${prefix}-source`, { viewBox: "0 0 24 24", path: "M8 7l-5 5 5 5 M16 7l5 5-5 5", strokeWidth: 2 });
      commands.registerCommand({
        id: `${prefix}-web:selection-source`,
        label: "Source",
        icon: `${prefix}-source`,
        action: () => openSelectionInSource().catch(sourceFailed),
      });
      items.push(item(`${prefix}-web-selection-source`, `${prefix}-web:selection-source`));
    }
    ui.mergeSchema({
      selectionMenus: {
        selection: {
          id: "selection",
          visibilityDependsOn: { itemIds: [`${prefix}-web-comment-selection`] },
          items,
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
        comments.bringIntoSight();
        comments.showTab();
        const comment = comments.marked().find((each) => each.id === commentId);
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
      host.layoutReady();
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
    const response = await fetch(`${host.base()}/reference-preview?${params}`);
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
    if (!state.selection) return;
    const formatted = state.selection.getFormattedSelection();
    if (formatted.length === 0) return;
    if (formatted.length !== 1) {
      alert("Select text on one page at a time.");
      return;
    }
    const lines = await state.selection.getSelectedText().toPromise();
    const quote = lines.join(" ").replace(/\s+/g, " ").trim();
    if (!quote) return;

    const [{ pageIndex, rect, segmentRects }] = formatted;
    const comment = host.words({
      page: pageIndex + 1,
      bbox: rectToBBox(rect),
      rects: segmentRects.map(rectToBBox),
      quote,
      excerpt: `${quote.slice(0, 80)}${quote.length > 80 ? "…" : ""}`,
    });
    if (comment) host.openCompose(...comment);
  }


  function installAreaTool(registry, selectionCapability, commands, ui) {
    const interaction = registry.getPlugin("interaction-manager").provides();
    interaction.registerMode({ id: AREA_MODE, scope: "page", exclusive: false, cursor: "crosshair" });
    const scope = interaction.forDocument(DOCUMENT_ID);
    interaction.onModeChange(({ activeMode }) => {
      state.areaMode = activeMode === AREA_MODE;
      state.viewer.toggleAttribute(`data-${prefix}-area`, state.areaMode);
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
      const comment = host.box({ page: pageIndex + 1, bbox: rectToBBox(rect) });
      if (comment) host.openCompose(...comment);
    });
    state.viewer.registerIcon(`${prefix}-area`, {
      viewBox: "0 0 24 24",
      path: "M3 9V3h6 M15 3h6v6 M21 15v6h-6 M9 21H3v-6 M8 12h8 M12 8v8",
      strokeWidth: 2,
    });
    commands.registerCommand({
      id: `${prefix}-web:area`,
      label: "Draw a box to comment on",
      icon: `${prefix}-area`,
      categories: ["tools"],
      action: () => (scope.getActiveMode() === AREA_MODE ? scope.activateDefaultMode() : scope.activate(AREA_MODE)),
      active: ({ state: viewerState, documentId }) =>
        viewerState.plugins["interaction-manager"]?.documents?.[documentId]?.activeMode === AREA_MODE,
    });
    const toolbar = ui.getSchema().toolbars["main-toolbar"];
    const items = toolbar.items.map((item) => item.id !== "center-group" ? item : {
      ...item,
      items: [...item.items, { type: "command-button", id: "area-button", commandId: `${prefix}-web:area`, variant: "icon" }],
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

  // Source shows the selected lines where they are written, and selects the words there.
  async function openSelectionInSource() {
    if (!state.selection) return;
    const formatted = state.selection.getFormattedSelection();
    if (formatted.length !== 1) {
      alert("Select text on one page at a time.");
      return;
    }
    const quote = (await state.selection.getSelectedText().toPromise()).join(" ");
    await host.source({
      page: formatted[0].pageIndex + 1, rects: formatted[0].segmentRects.map(rectToBBox), quote,
    });
  }

  function sourceFailed(error) {
    alert(`Could not open the source: ${error.message}`);
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

  // The view keeps its zoom mode, such as fitting the page width, through a change of the room
  // it has.
  function refitPdf() {
    const zoomMode = state.zoom?.getState().zoomLevel;
    requestAnimationFrame(() => { if (typeof zoomMode === "string") state.zoom?.requestZoom(zoomMode); });
  }

  function setTheme(theme) {
    state.viewer?.setTheme?.(viewerTheme(theme));
    // The viewer's scroll bars take the page's scheme, which its own theme does not set.
    state.viewer?.setAttribute(`data-${prefix}-theme`, theme);
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
    shown: () => Boolean(state.viewer),
    loaded: () => Boolean(state.scroll && state.annotations),
    laidOut: () => state.layoutReady && Boolean(state.scroll),
    marksReady: marks.ready,
    currentPage: () => state.scroll?.getCurrentPage(),
    view: capturePdfView,
    zoomMode: () => state.zoom?.getState().zoomLevel,
    attach,
    open: initializePdfViewer,
    setTheme,
    refit: (zoomMode) => { if (typeof zoomMode === "string") state.zoom?.requestZoom(zoomMode); },
    refitSoon: refitPdf,
    scrollTo: (place) => state.scroll?.scrollToPage(place),
    jumpToPage,
    zoomTo: positionPdfTarget,
    selectComment: marks.select,
    markComments: marks.sync,
    markPlace: marks.place,
    outline: marks.outline,
    clearSelection: () => state.selection?.clear(),
  };
}
