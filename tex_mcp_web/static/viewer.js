import EmbedPDF, { PdfAnnotationSubtype } from "/static/embedpdf/embedpdf.js?v=selection-yellow";

const DOCUMENT_ID = "paper";
const ANNOTATION_PREFIX = "tex-web:";
const GOTO_ANNOTATION_ID = "tex-web-goto";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const state = {
  viewer: null,
  selection: null,
  annotations: null,
  scroll: null,
  zoom: null,
  annotationsReady: false,
  layoutReady: false,
  autoCompile: false,
  pdfDigest: null,
  comments: [],
  paper: null,
  pendingAnchor: null,
  composeSubmitting: false,
  errors: [],
  warnings: [],
  expanded: new Set(),
  picked: new Set(),
  activeForm: null,
  editingEntry: null,
  focusedCommentId: null,
  annotationCommentById: new Map(),
  referencePreviewRequest: 0,
  lastViewerPointer: null,
  appliedCompileTimestamp: null,
  compileRefreshPromise: null,
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

async function initializePdfViewer(pdfView) {
  clearGotoHighlight();
  hideReferencePreview();
  const host = $("#pdf-viewer");
  clear(host);
  state.selection = null;
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
    theme: {
      preference: "light",
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
    },
    tabBar: "never",
    disabledCategories: ["annotation", "redaction", "insert", "form", "panel-comment"],
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
    /* While a selection is being dragged, existing highlights take no pointer events:
       their hit boxes are excluded from interaction, so a drag that reached one never
       saw the pointer come up and followed the mouse until it left the box. */
    :host([data-tex-selecting]) [data-no-interaction] * {
      pointer-events: none !important;
    }
  `;
  state.viewer.shadowRoot.appendChild(viewerStyle);
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
  state.selection.onBeginSelection(() => state.viewer.setAttribute("data-tex-selecting", ""));
  state.selection.onEndSelection(() => state.viewer.removeAttribute("data-tex-selecting"));
  state.annotations = annotationCapability.forDocument(DOCUMENT_ID);
  state.scroll = scrollCapability.forDocument(DOCUMENT_ID);
  state.zoom = zoomCapability.forDocument(DOCUMENT_ID);

  commands.registerCommand({
    id: "tex-web:comment-selection",
    label: "Comment",
    icon: "comment",
    action: openTextSelectionCompose,
  });
  ui.mergeSchema({
    selectionMenus: {
      selection: {
        id: "selection",
        visibilityDependsOn: { itemIds: ["tex-web-comment-selection"] },
        items: [{
          type: "command-button",
          id: "tex-web-comment-selection",
          commandId: "tex-web:comment-selection",
          variant: "icon-text",
        }],
      },
    },
  });

  state.annotations.onStateChange((annotationState) => {
    if (annotationState.selectedUids.length !== 1) return;
    const commentId = state.annotationCommentById.get(annotationState.selectedUids[0]);
    if (commentId) {
      setSidebarCollapsed(false);
      switchTab("comments");
      const comment = state.comments.find((item) => item.id === commentId);
      if (comment) focusComment(comment);
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

function openCompose(anchor, label, selectionText = "") {
  state.pendingAnchor = anchor;
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

// Enter writes a new line, as it does in any box of text. What sends it is shift with
// enter, which keeps the hand on the keyboard, or the command or control key with it.
function sendOn(input, send) {
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    if (!(event.shiftKey || event.metaKey || event.ctrlKey) || event.altKey) return;
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
    await refreshComments();
  } finally {
    applyComposeSubmitting(false);
  }
}

async function refreshComments() {
  const status = $("#comment-filter").value;
  const url = status === "all" ? "/comments" : `/comments?status=${status}`;
  const response = await fetch(url);
  if (!response.ok) throw new Error(await responseError(response));
  const data = await response.json();
  state.comments = data.comments;
  renderComments();
  syncCommentAnnotations();
}

function renderComments() {
  const list = $("#comments-list");
  const scrollTop = list.scrollTop;
  const activeInput = document.activeElement?.classList.contains("cmt-form-input")
    ? document.activeElement
    : null;
  const inputState = activeInput ? {
    commentId: activeInput.closest(".cmt")?.dataset.commentId,
    selectionStart: activeInput.selectionStart,
    selectionEnd: activeInput.selectionEnd,
    selectionDirection: activeInput.selectionDirection,
    scrollTop: activeInput.scrollTop,
  } : null;
  clear(list);
  if (state.comments.length === 0) list.appendChild(placeholder("No comments at this filter."));
  else for (const comment of state.comments) list.appendChild(renderCommentItem(comment));
  list.scrollTop = scrollTop;
  if (inputState?.commentId) {
    const commentNode = Array.from(list.children).find(
      (node) => node.dataset.commentId === inputState.commentId,
    );
    const input = commentNode?.querySelector(".cmt-form-input");
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
  updateCommentCount();
  renderPickedActions();
  renderFoldAction();
}

// What the picked comments can be sent to, which is decided by the state they are in:
// open ones close, closed ones reopen. Both buttons show a count, so a mixed pick says
// exactly what each press will touch.
function renderPickedActions() {
  const picked = state.comments.filter((comment) => state.picked.has(comment.id));
  const open = picked.filter((comment) => comment.status === "open").map((comment) => comment.id);
  const closed = picked.filter((comment) => comment.status !== "open").map((comment) => comment.id);
  const shown = state.comments.length;
  const all = $("#pick-all-btn");
  all.disabled = shown === 0;
  const clearing = picked.length === shown && shown > 0;
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
  const reopen = $("#reopen-picked-btn");
  reopen.disabled = closed.length === 0;
  reopen.querySelector(".count").textContent = closed.length > 0 ? String(closed.length) : "";
  reopen.setAttribute("aria-label", closed.length > 0 ? `Reopen ${closed.length}` : "Reopen");
  reopen.dataset.ids = closed.join(" ");
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
  const shown = state.comments.map((comment) => comment.id);
  const allOpen = shown.every((id) => state.expanded.has(id));
  for (const id of shown) {
    if (allOpen) state.expanded.delete(id);
    else state.expanded.add(id);
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

// Closing is one call per comment with an empty summary: the thread already holds what
// was said. Reopening flips the status back and adds nothing.
async function setPickedStatus(ids, status) {
  const action = status === "open" ? "reopen" : "resolve";
  for (const id of ids) {
    const response = await fetch(`/comments/${id}/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(action === "resolve" ? { summary: "" } : {}),
    });
    if (!response.ok) throw new Error(await responseError(response));
    state.picked.delete(id);
  }
  await refreshComments();
}

function renderCommentItem(comment) {
  const expanded = state.expanded.has(comment.id);
  const replies = comment.thread.length - 1;
  const box = h("input", { class: "comment-pick", type: "checkbox", title: "Pick this comment" });
  box.checked = state.picked.has(comment.id);
  // The box sits in the head, which opens the card: picking is not opening.
  box.addEventListener("click", (event) => event.stopPropagation());
  box.addEventListener("change", () => {
    if (box.checked) state.picked.add(comment.id);
    else state.picked.delete(comment.id);
    renderPickedActions();
  });
  const head = h("div", {
    class: "cmt-head",
    title: expanded ? "collapse" : "expand thread",
    onclick: (event) => {
      event.stopPropagation();
      if (expanded) state.expanded.delete(comment.id);
      else state.expanded.add(comment.id);
      renderComments();
      jumpToComment(comment.id);
    },
  },
  box,
  h("span", {
    class: "cmt-toggle",
    text: expanded ? "▾" : replies > 0 ? `▸ ${replies} repl${replies > 1 ? "ies" : "y"}` : "▸",
  }),
  h("span", { class: "cmt-id", text: comment.id }),
  h("span", { class: "cmt-status", text: `[${comment.status}]` }),
  comment.stale ? h("span", { class: "stale", text: "STALE" }) : null,
  h("span", { class: "cmt-anchor", text: anchorLabel(comment.anchor) }));

  const children = [head];
  if (!expanded) children.push(h("div", { class: "cmt-preview", text: comment.thread[0]?.text ?? "" }));
  if (comment.suggestion) children.push(renderSuggestion(comment.suggestion));
  if (expanded) {
    children.push(
      h("div", { class: "cmt-thread" },
        ...comment.thread.map((entry, index) => renderThreadEntry(entry, comment.id, index))),
      h("div", { class: "cmt-actions" }, ...actionButtons(comment)),
    );
    const form = renderActiveForm(comment);
    if (form) children.push(form);
  }
  const focused = state.focusedCommentId === comment.id;
  return h("div", {
    class: `cmt status-${comment.status}${focused ? " is-focused" : ""}`,
    data: { commentId: comment.id },
  }, ...children);
}

function renderSuggestion(suggestion) {
  return h("div", { class: "cmt-suggestion" },
    h("div", { class: "sugg-old", title: "current text" },
      h("span", { class: "sugg-marker", text: "−" }),
      h("span", { class: "sugg-text", text: suggestion.old })),
    h("div", { class: "sugg-new", title: "proposed replacement" },
      h("span", { class: "sugg-marker", text: "+" }),
      h("span", { class: "sugg-text", text: suggestion.new })),
  );
}

function renderThreadEntry(entry, commentId, index) {
  const editing = state.editingEntry?.commentId === commentId
    && state.editingEntry.index === index;
  const children = [
    h("div", { class: "thread-meta" },
      h("span", { text: `${entry.author} · ${entry.at}` }),
      entry.author === "human" && !editing
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
  sendOn(textarea, save);
  return h("div", { class: "cmt-form mode-edit" }, textarea,
    h("div", { class: "cmt-form-actions" },
      actionButton("cmt-form-cancel", "Cancel", () => {
        state.editingEntry = null;
        renderComments();
      }),
      actionButton("cmt-form-submit", "Save", save)));
}

function actionButton(className, label, onclick) {
  return h("button", { class: className, type: "button", text: label, onclick });
}

function actionButtons(comment) {
  const deleteButton = actionButton("cmt-delete", "Delete", () => {
    if (confirm(`Permanently delete ${comment.id}?`)) {
      mutateAndRefresh(comment.id, "delete", null);
    }
  });
  if (comment.status !== "open") {
    return [
      actionButton("cmt-reopen", "Reopen", () => mutateAndRefresh(comment.id, "reopen", {})),
      deleteButton,
    ];
  }
  return [
    actionButton("cmt-reply", "Reply", () => setActiveForm(comment.id, "reply")),
    // Closing is one click: the thread already holds what was said, so an empty
    // summary flips the status without adding an entry.
    actionButton("cmt-resolve", "Resolve", () => closeComment(comment.id, "resolve", "summary")),
    deleteButton,
  ];
}

function setActiveForm(commentId, mode) {
  state.activeForm = { commentId, mode, draft: "" };
  state.expanded.add(commentId);
  renderComments();
  const commentNode = Array.from($("#comments-list").children).find(
    (node) => node.dataset.commentId === commentId,
  );
  const input = commentNode?.querySelector(".cmt-form-input");
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

function updateCommentCount() {
  const openCount = state.comments.filter((comment) => comment.status === "open").length;
  const badge = $("#comments-tab-count");
  badge.textContent = String(openCount);
  badge.classList.toggle("hidden", openCount === 0);
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
  applyAutoCompile(state.paper.auto_compile);
  renderSections();
}

function applyAutoCompile(enabled) {
  state.autoCompile = enabled;
  const button = $("#auto-compile-btn");
  button.textContent = enabled ? "Auto: On" : "Auto: Off";
  button.classList.toggle("active", enabled);
  button.setAttribute("aria-pressed", String(enabled));
}

async function toggleAutoCompile() {
  const button = $("#auto-compile-btn");
  button.disabled = true;
  try {
    const response = await fetch("/auto-compile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !state.autoCompile }),
    });
    if (!response.ok) throw new Error(await responseError(response));
    const result = await response.json();
    applyAutoCompile(result.auto_compile);
  } catch (error) {
    alert(`Could not change automatic compilation: ${error.message}`);
  } finally {
    button.disabled = false;
  }
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
        openCompose({ kind: "section", title: section.title }, `Section: ${section.title}`))));
  }
  host.appendChild(list);
}

function switchTab(name) {
  for (const button of $$(".tab-btn")) button.classList.toggle("active", button.dataset.tab === name);
  for (const pane of $$(".tab-pane")) pane.classList.toggle("active", pane.id === `tab-${name}`);
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
  const button = $("#recompile-btn");
  button.disabled = compiling;
  button.textContent = compiling ? "Compiling…" : "Recompile";
  if (compiling) $("#compile-status").textContent = "compiling…";
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

async function recompile() {
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
      break;
    case "comment_added":
    case "comment_updated":
    case "comment_deleted":
    case "comments_changed":
      await refreshComments();
      break;
    case "state":
      applyAutoCompile(message.auto_compile);
      if (message.result) applyCompileResult(message.result);
      if (message.compiling) applyCompiling(true);
      if (message.review_waiters !== undefined) showAgentWaiting(message.review_waiters);
      break;
    case "review_waiters":
      showAgentWaiting(message.waiters);
      break;
    case "auto_compile":
      applyAutoCompile(message.enabled);
      break;
    case "goto":
      showGotoTarget(message);
      break;
  }
}

// The green dot beside Call agent: an agent is parked on wait_review right now, so a
// press reaches it at once rather than waiting to be picked up.
function showAgentWaiting(waiters) {
  $("#agent-waiting-dot").classList.toggle("hidden", !(waiters > 0));
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
  socket.onclose = () => setTimeout(connectWebSocket, 2000);
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
  const params = new URLSearchParams({ file, line: String(line) });
  const response = await fetch(`/synctex/source-to-pdf?${params}`);
  if (!response.ok) throw new Error(await responseError(response));
  const data = await response.json();
  jumpToPage(data.page);
}

async function jumpToComment(commentId) {
  const comment = state.comments.find((item) => item.id === commentId);
  if (!comment) return;
  if (comment.anchor.kind === "text_selection" || comment.anchor.kind === "area") {
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
    await jumpToSource(comment.resolved_source.file, comment.resolved_source.line_start);
  }
}

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
  document.addEventListener("keydown", (event) => {
    const tag = event.target.tagName.toUpperCase();
    if (["TEXTAREA", "INPUT", "SELECT"].includes(tag)) return;
    if (event.target.isContentEditable || document.querySelector("dialog[open]")) return;
    if (event.metaKey || event.ctrlKey || event.altKey || state.comments.length === 0) return;
    const index = state.comments.findIndex((comment) => comment.id === state.focusedCommentId);
    if (event.key === "j" || event.key === "ArrowDown") {
      event.preventDefault();
      focusComment(state.comments[Math.min(Math.max(index + 1, 0), state.comments.length - 1)]);
    } else if (event.key === "k" || event.key === "ArrowUp") {
      event.preventDefault();
      focusComment(state.comments[Math.max(index - 1, 0)]);
    } else if (event.key === "r" && !event.shiftKey && index >= 0) {
      event.preventDefault();
      setActiveForm(state.comments[index].id, "reply");
    } else if ((event.key === "R" || (event.key === "r" && event.shiftKey)) && index >= 0) {
      event.preventDefault();
      closeComment(state.comments[index].id, "resolve", "summary");
    } else if (event.key === "Escape" && state.activeForm) {
      event.preventDefault();
      state.activeForm = null;
      renderComments();
    }
  });
}

function setSidebarCollapsed(collapsed) {
  $(".layout").classList.toggle("sidebar-collapsed", collapsed);
  $("#sidebar-toggle-btn").classList.toggle("active", collapsed);
  localStorage.setItem("sidebarCollapsed", collapsed ? "1" : "0");
}

function attachSidebarToggle() {
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

async function init() {
  attachKeyboardNavigation();
  attachSidebarToggle();
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
  $("#auto-compile-btn").addEventListener("click", () => toggleAutoCompile());
  $("#recompile-btn").addEventListener("click", () => recompile());
  $("#call-agent-btn").addEventListener("click", () => callAgent());
  $("#fold-all-btn").addEventListener("click", foldAll);
  $("#pick-all-btn").addEventListener("click", pickAll);
  // The buttons carry the ids they were drawn with, so a press acts on what its label counted.
  for (const [id, status] of [["#resolve-picked-btn", "resolved"], ["#reopen-picked-btn", "open"]]) {
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
  $("#comment-filter").addEventListener("change", () => {
    refreshComments().catch((error) => console.error(error));
  });
  for (const button of $$(".tab-btn")) {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  }

  await refreshPaper();
  await refreshComments();
  if (state.pdfDigest) await initializePdfViewer(null);
  connectWebSocket();
}

document.addEventListener("DOMContentLoaded", () => {
  init().catch((error) => {
    console.error(error);
    alert(`tex-web failed to start: ${error.message}`);
  });
});
