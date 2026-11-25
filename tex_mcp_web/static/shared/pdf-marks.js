// The marks a review page's PDF view (pdf-view.js) draws on the PDF's pages: each open comment's
// highlight, with its numbered badge beside it, faint red where the comment's words are gone; the
// one mark that shows a place; and a box outlined for a moment.
//
// createPdfMarks takes prefix, the page's name for its classes and ids in the viewer, as
// pdf-view.js takes it; and comments, the comment panel as pdf-view.js takes it. It gives
// start(viewer, annotations) as a PDF is shown, stop() before another is, loaded(), once the
// PDF's own annotations are read, sync(), commentOf(uid), the comment a highlight is of,
// select(id, page), place(page, bbox, rects, look), clearPlace(), outline(page, bbox, ms), and
// ready(), whether marks can be drawn.
//
// Shared by the review pages. It is written in tex-mcp-web, and scripts/share_page.py copies
// it to the others. A copy is never edited by hand.

// The same specifier as pdf-view.js's, so that both have the one module.
import { PdfAnnotationBorderStyle, PdfAnnotationSubtype } from "/static/embedpdf/embedpdf.js?v=selection-yellow";
import { $ } from "./dom.js";
import { setSidebarCollapsed } from "./sidebar.js";

function bboxToRect([x1, y1, x2, y2]) {
  return {
    origin: { x: x1, y: y1 },
    size: { width: x2 - x1, height: y2 - y1 },
  };
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

export function createPdfMarks({ prefix, comments }) {
  // The ids of the comments' highlights start with this, and the place's and an outline's are
  // apart from it, so that drawing the highlights again leaves those.
  const ANNOTATION_PREFIX = `${prefix}-web:`;
  const GOTO_ANNOTATION_ID = `${prefix}-web-goto`;
  const BADGE = `${prefix}-comment-badge`;
  const SEGMENT = `${prefix}-comment-segment`;
  const state = {
    viewer: null,
    annotations: null,
    annotationsReady: false,
    annotationCommentById: new Map(),
    annotationObserver: null,
    badgeFrame: null,
  };

  function annotationId(commentId, page) {
    return `${ANNOTATION_PREFIX}${commentId}:p${page}`;
  }

  function commentSegments(layer) {
    return Array.from(layer.querySelectorAll("div")).filter((node) =>
      node.classList.contains(SEGMENT)
        || (node.style.position === "absolute"
          && node.style.zIndex === "1"
          && node.style.cursor === "pointer"
          && (node.style.opacity === "0.2" || node.style.opacity === "0.35")));
  }

  function start(viewer, annotations) {
    state.viewer = viewer;
    state.annotations = annotations;
    const root = viewer.shadowRoot;
    state.annotationObserver = new MutationObserver((records) => {
      const onlyOwnedChanges = records.every((record) => {
        if (record.type === "attributes") {
          return record.target.classList.contains(BADGE)
            || record.target.classList.contains(SEGMENT);
        }
        return [...record.addedNodes, ...record.removedNodes].every((node) =>
          node.nodeType === Node.ELEMENT_NODE && node.classList.contains(BADGE));
      });
      if (!onlyOwnedChanges) scheduleCommentBadges();
    });
    state.annotationObserver.observe(root, {
      attributes: true,
      attributeFilter: ["class", "style"],
      childList: true,
      subtree: true,
    });
  }

  function stop() {
    state.annotationObserver?.disconnect();
    state.annotationObserver = null;
    if (state.badgeFrame !== null) cancelAnimationFrame(state.badgeFrame);
    state.badgeFrame = null;
    clearGotoHighlight();
    state.annotations = null;
    state.annotationsReady = false;
    state.annotationCommentById.clear();
    state.viewer = null;
  }

  function loaded() {
    state.annotationsReady = true;
    syncCommentAnnotations();
  }


  function commentBadgePlans() {
    let number = 0;
    const plans = new Map();
    for (const comment of comments.marked()) {
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
        segment.classList.add(SEGMENT);
        segment.style.pointerEvents = "none";
        segment.style.cursor = "text";
      }
      if (!frame) continue;
      shown.add(plan.comment.id);
      let badge = layer.querySelector(
        `.${BADGE}[data-comment-id="${CSS.escape(plan.comment.id)}"]`,
      );
      if (badge === null) {
        badge = document.createElement("button");
        badge.type = "button";
        badge.className = BADGE;
        badge.dataset.commentId = plan.comment.id;
        badge.addEventListener("pointerdown", (event) => event.stopPropagation());
        // A click shows the comment in the panel, and a second click on the badge of the comment
        // the panel already shows hides the panel, to read the paper at its full width.
        badge.addEventListener("click", (event) => {
          event.stopPropagation();
          if (!$(".layout").classList.contains("sidebar-collapsed")
            && comments.ringed() === plan.comment.id) {
            setSidebarCollapsed(true);
            return;
          }
          setSidebarCollapsed(false);
          comments.showTab();
          comments.show(plan.comment.id).catch((error) => console.error(error));
        });
        layer.appendChild(badge);
      }
      badge.classList.toggle("stale", plan.comment.stale === true);
      if (badge.textContent !== String(plan.number)) badge.textContent = String(plan.number);
      badge.title = `${plan.comment.thread[0]?.text ?? ""}\n\nClick to show this comment in the panel. Click again to hide the panel.`;
      badge.setAttribute("aria-label", `Open comment ${plan.number}`);
      const localWidth = parseFloat(frame.style.width);
      const scale = localWidth > 0 ? frame.getBoundingClientRect().width / localWidth : 1;
      badge.style.left = `${Math.max(0, parseFloat(frame.style.left) - 20 / scale)}px`;
      badge.style.top = `${Math.max(0, parseFloat(frame.style.top) - 1 / scale)}px`;
      badge.style.transform = `scale(${1 / scale})`;
    }
    for (const badge of state.viewer.shadowRoot.querySelectorAll(`.${BADGE}`)) {
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
    for (const comment of comments.marked()) {
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
          custom: { [`${prefix}WebCommentId`]: comment.id },
        });
      }
    }
    scheduleCommentBadges();
  }

  function clearGotoHighlight() {
    if (!state.annotations) return;
    for (const tracked of state.annotations.getAnnotations()) {
      if (tracked.object.id === GOTO_ANNOTATION_ID) {
        state.annotations.purgeAnnotation(tracked.object.pageIndex, GOTO_ANNOTATION_ID);
      }
    }
  }

  function select(id, page) {
    if (state.annotations) {
      state.annotations.selectAnnotation(
        page - 1,
        annotationId(id, page),
      );
    }
  }

  function place(page, bbox, rects, { opacity, color, contents = "" }) {
    clearGotoHighlight();
    state.annotations.createAnnotation(page - 1, {
      id: GOTO_ANNOTATION_ID,
      pageIndex: page - 1,
      type: PdfAnnotationSubtype.HIGHLIGHT,
      rect: bboxToRect(bbox),
      segmentRects: rects.map(bboxToRect),
      opacity,
      strokeColor: color,
      contents,
    });
  }

  // A box on a page outlined for `ms` milliseconds, a little outside it.
  function outline(page, [x1, y1, x2, y2], ms) {
    if (!state.annotations) return;
    const id = `${prefix}-web-block:${page}:${performance.now()}`;
    state.annotations.createAnnotation(page - 1, {
      id,
      pageIndex: page - 1,
      type: PdfAnnotationSubtype.SQUARE,
      rect: bboxToRect([x1 - 3, y1 - 3, x2 + 3, y2 + 3]),
      strokeColor: "#c27c0e",
      strokeWidth: 2,
      strokeStyle: PdfAnnotationBorderStyle.SOLID,
      strokeDashArray: [],
      color: "transparent",
      opacity: 1,
      flags: [],
    });
    setTimeout(() => state.annotations?.purgeAnnotation(page - 1, id), ms);
  }

  return {
    start, stop, loaded, select, place, outline,
    sync: syncCommentAnnotations,
    commentOf: (uid) => state.annotationCommentById.get(uid),
    clearPlace: clearGotoHighlight,
    ready: () => state.annotationsReady && Boolean(state.annotations),
  };
}
