// Where a comment is: its place as its card names it (anchorLabel), the words its card quotes
// (commentQuote), and that place shown from the comment list, as the source's cursor on its words
// or the Preview at its box.
//
// createCommentPlaces takes state, the comment panel's (comments); workspace's view() and
// show(view); editor, the source editor's open(file, line), showPlace(row, column) and focus();
// and pdf's zoomTo(page, bbox), selectComment(id, page) and showLine(file, line). It gives
// show(id), for a comment shown from the list, and enter(comment), for K, which takes the keys
// to its words in the source.

import { $ } from "../shared/dom.js";

// The words a comment is on, which its card quotes once opened: those selected on the PDF or in
// the source, or a section's title. A note on the paper or a box on it quotes nothing.
export function commentQuote(comment) {
  const { anchor } = comment;
  return anchor.kind === "text_selection" ? anchor.quote
    : anchor.kind === "source_range" ? comment.source_selector?.exact ?? ""
    : anchor.kind === "section" ? anchor.title : "";
}

export function anchorLabel(anchor) {
  switch (anchor.kind) {
    case "paper": return "paper";
    case "section": return `§ ${anchor.title}`;
    case "source_range": return `${anchor.file}:${anchor.line_start}-${anchor.line_end}`;
    case "text_selection": return `p${anchor.selection.page} text`;
    case "area": return `p${anchor.page} area`;
    default: throw new Error(`unknown anchor kind: ${anchor.kind}`);
  }
}

export function createCommentPlaces({ state, workspace, editor, pdf }) {

  // Where a comment is, shown from the comments: the source moves its cursor there, and the
  // keys stay with the comments, where the reader clicked or pressed Enter.
  async function jumpToComment(commentId) {
    const comment = state.comments.find((item) => item.id === commentId);
    if (!comment) return;
    if (comment.anchor.kind === "source_range" || comment.suggested) {
      if (workspace.view() === "pdf") await workspace.show("source");
      const source = comment.resolved_source ?? comment.anchor;
      if (await editor.open(source.file)) {
        editor.showPlace(source.line_start - 1, comment.stale ? 0 : source.column_start ?? 0);
      }
    } else if (comment.anchor.kind === "text_selection" || comment.anchor.kind === "area") {
      const selection = comment.anchor.kind === "text_selection"
        ? comment.anchor.selection
        : { page: comment.anchor.page, bbox: comment.anchor.bbox };
      pdf.zoomTo(selection.page, selection.bbox);
      pdf.selectComment(comment.id, selection.page);
    } else if (comment.resolved_source) {
      const { file, line_start: line } = comment.resolved_source;
      if (workspace.view() !== "pdf" && await editor.open(file)) editor.showPlace(line - 1, 0);
      if (workspace.view() !== "source") await pdf.showLine(file, line);
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
  async function enterSource(comment) {
    const place = comment.anchor.kind === "source_range" ? comment.resolved_source ?? comment.anchor : comment.resolved_source;
    if (workspace.view() === "pdf") await workspace.show("source");
    if (place && await editor.open(place.file)) {
      editor.showPlace(place.line_start - 1, comment.stale ? 0 : place.column_start ?? 0);
    }
    editor.focus();
  }

  return { show: jumpToComment, enter: enterSource };
}
