// A place of the paper shown in the views: the Preview, the Source, and Split, which shows the two
// side by side. Each kind of place has its rule for which view brings it into sight, and SyncTeX
// finds words selected in one of the two in the other, and a source line's page. A comment's
// place, shown from the comment list, is one of them.
//
// createPlaces takes digest(), the digest of the PDF shown; workspace (view(), show(view),
// shows(pane)); pdf (loaded(), scrollTo(place), jumpToPage(page), zoomTo(page, bbox), marksReady(),
// markPlace(page, bbox, rects, look), selectComment(id, page)); and source (init(), open(file,
// line), selectQuote(quote, first, last), showPlace(row, column), focus()). It gives
// showSelectionInSource(selection), for words selected in the Preview, and
// showSelectionInPreview(selection), for words selected in the editor, each of which says itself
// when it cannot; showLayoutLine(at), for a
// line of the layout Problems lists; showTarget(target), for the agent's goto; showInSource(file,
// line), which brings the Source into sight; showLine(file, line), which shows a source line in
// each view shown; showComment(comment), for a comment shown from the list; and
// enterComment(comment), for K, which takes the keys to its words in the source.

import { $ } from "../shared/dom.js";
import { responseError } from "../shared/comment-api.js";

export function createPlaces({ digest, workspace, pdf, source }) {
  // The Source brought into sight beside the Preview: from the Preview, Split, and the Source view
  // where Split's translation stands in for Source, as the translation sits under the editor there.
  async function sourceBesidePreview() {
    if (workspace.view() === "pdf") await workspace.show("split");
    if (!workspace.shows("source")) await workspace.show("source");
  }

  // The Preview brought into sight beside the Source shown alone: Split.
  async function previewBesideSource() {
    if (workspace.view() !== "source") return;
    await workspace.show("split");
    // The Preview takes its new width before it is scrolled.
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  }

  // A box on a printed page, at the zoom the Preview is read at, in the middle. At once: a smooth
  // scroll in Safari came late, or stopped short, when one place followed another.
  function centerPreviewOn(page, [x1, y1, x2, y2]) {
    pdf.scrollTo({
      pageNumber: page, pageCoordinates: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 },
      behavior: "instant", alignX: 50, alignY: 50,
    });
  }

  // The PDF page a source line compiles to.
  async function showPageOf(file, line) {
    const params = new URLSearchParams({ file, line: String(line) });
    const response = await fetch(`/synctex/source-to-pdf?${params}`);
    if (!response.ok) throw new Error(await responseError(response));
    const data = await response.json();
    pdf.jumpToPage(data.page);
  }

  // A file at a line in the editor, with the cursor and the keys there, the Source brought beside
  // the Preview: from the translation, and from someone on Overleaf. It says whether the file
  // opened.
  async function showInSource(file, line) {
    await sourceBesidePreview();
    await source.init();
    return source.open(file, line);
  }

  // Words selected on a page of the Preview ({ page, rects, quote }), shown in the editor beside
  // it where SyncTeX says their lines come from, and selected there.
  async function showSelectionInSource(selection) {
    await sourceOf(selection).catch((error) => alert(`Could not open the source: ${error.message}`));
  }

  async function sourceOf({ page, rects, quote }) {
    const response = await fetch("/synctex/pdf-to-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page, rects, pdf_digest: digest() }),
    });
    if (!response.ok) throw new Error(await responseError(response));
    const place = await response.json();
    if (await showInSource(place.file, place.line_start)) source.selectQuote(quote, place.line_start, place.line_end);
  }

  // The words selected in the editor ({ file, line_start, line_end, text }), shown where the
  // Preview prints them, beside the editor in Split, at the zoom the Preview is read at: SyncTeX
  // names the boxes of their lines, and the server finds the printed words in them.
  async function showSelectionInPreview(selection) {
    await printedOf(selection).catch((error) => alert(`Could not show it in the Preview: ${error.message}`));
  }

  async function printedOf({ file, line_start, line_end, text }) {
    if (!pdf.loaded()) return;
    const response = await fetch("/synctex/source-to-pdf-words", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file, line_start, line_end, text }),
    });
    if (!response.ok) throw new Error(await responseError(response));
    const place = await response.json();
    await previewBesideSource();
    centerPreviewOn(place.page, place.bbox);
    // The editor's selection blue, apart from the comments' yellow.
    pdf.markPlace(place.page, place.bbox, place.rects, { opacity: 0.6, color: "#b5d5ff" });
  }

  // A line of the layout ({ page, bbox, source }) is a place on a printed page: it is pointed at
  // in the Preview, in the middle and marked, beside the Source when the Source shows alone, and
  // the editor's cursor moves to its source ("file:line") only where the editor shows beside it.
  async function showLayoutLine(at) {
    await previewBesideSource();
    centerPreviewOn(at.page, at.bbox);
    if (pdf.marksReady()) pdf.markPlace(at.page, at.bbox, [at.bbox], { opacity: 0.55, color: "#fbdc00" });
    if (workspace.view() === "split" && at.source) {
      const [, file, line] = /^(.*):(\d+)$/.exec(at.source);
      await source.open(file, Number(line));
    }
  }

  // The agent's goto ({ page, bbox, rects, quote }), a place on a printed page, which the Preview
  // zooms to and marks.
  function showTarget(target) {
    if (!target.page) return;
    pdf.zoomTo(target.page, target.bbox);
    if (!target.bbox || !pdf.marksReady()) return;
    pdf.markPlace(target.page, target.bbox, target.rects ?? [target.bbox],
      { opacity: 0.55, color: "#fbdc00", contents: target.quote ?? "" });
  }

  // A source line shown in each view that shows: in the editor, with the keys there, unless the
  // Preview shows alone, and on its page unless the Source does.
  async function showLine(file, line) {
    if (workspace.view() !== "pdf") {
      await source.init();
      await source.open(file, line);
    }
    if (workspace.view() !== "source") await showPageOf(file, line);
  }

  // A comment's words in the source, with the Source shown in place of the Preview shown alone.
  async function showCommentSource(comment, place) {
    if (workspace.view() === "pdf") await workspace.show("source");
    if (place && await source.open(place.file)) {
      source.showPlace(place.line_start - 1, comment.stale ? 0 : place.column_start ?? 0);
    }
  }

  // Where a comment is, shown from the comments: the source moves its cursor there, and the
  // keys stay with the comments, where the reader clicked or pressed Enter.
  async function showComment(comment) {
    if (comment.anchor.kind === "source_range" || comment.suggested) {
      await showCommentSource(comment, comment.resolved_source ?? comment.anchor);
    } else if (comment.anchor.kind === "text_selection" || comment.anchor.kind === "area") {
      const selection = comment.anchor.kind === "text_selection"
        ? comment.anchor.selection
        : { page: comment.anchor.page, bbox: comment.anchor.bbox };
      pdf.zoomTo(selection.page, selection.bbox);
      pdf.selectComment(comment.id, selection.page);
    } else if (comment.resolved_source) {
      const { file, line_start: line } = comment.resolved_source;
      if (workspace.view() !== "pdf" && await source.open(file)) source.showPlace(line - 1, 0);
      if (workspace.view() !== "source") await showPageOf(file, line);
    }
    // The keys come back to the list, unless a box in it took them while the jump went on, as r
    // pressed right after Space opens the reply box.
    if (!document.activeElement?.matches?.("#comments-list textarea")) {
      $("#comments-list").focus({ preventScroll: true });
    }
  }

  // The keys taken into the source at the ringed comment's words, as K in the editor takes them
  // from a comment's words to the comment. A comment with no place in the source, as a paper note,
  // leaves the editor's cursor where it is.
  async function enterComment(comment) {
    await showCommentSource(comment,
      comment.anchor.kind === "source_range" ? comment.resolved_source ?? comment.anchor : comment.resolved_source);
    source.focus();
  }

  return {
    showSelectionInSource, showSelectionInPreview, showLayoutLine, showTarget, showInSource, showLine, showComment,
    enterComment,
  };
}
