// The marks the editor draws on the open file: each open comment's words, the edits suggested on
// Overleaf, and the text an open proposal would replace, each with a mark beside its line.
//
// createSourceMarks takes editor(), the Ace editor; file(), the open file's path; dirty(),
// whether the file has unsaved edits, which leave it unmarked; comments(), the open
// comments of the comment tab shown; and suggested(), the edits suggested on Overleaf. It gives
// sync(), which draws the marks again and says whether the file could be marked, clear(),
// atCursor(), the comment or edit whose words the cursor is in, and onRow(row), the comments
// marked beside a row.

import { h } from "../shared/dom.js";
import { overleafHue } from "./leaf.js";
import { suggestedItem } from "./suggested-edits.js";

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

export function createSourceMarks({ editor, file, dirty, comments, suggested }) {
  const state = {
    sourceCommentMarkers: [],
    // Each comment's marked text in the editor, as { range, comment }.
    sourceCommentSpans: [],
    // The gutter's marks, as "row class": a row takes each class once.
    sourceDecorations: new Set(),
    sourceCommentsByRow: new Map(),
  };

  // The comment or suggested edit whose words the cursor is in, the narrowest when several are.
  function markAtCursor() {
    const { row, column } = editor().getCursorPosition();
    const size = ({ range }) => (range.end.row - range.start.row) * 1e6 + range.end.column - range.start.column;
    return state.sourceCommentSpans.filter(({ range }) => range.contains(row, column))
      .sort((a, b) => size(a) - size(b))[0]?.comment;
  }

  function clearSourceCommentMarkers() {
    if (!editor()) return;
    for (const marker of state.sourceCommentMarkers) editor().session.removeMarker(marker);
    for (const mark of state.sourceDecorations) {
      const [row, name] = mark.split(" ");
      editor().session.removeGutterDecoration(Number(row), name);
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
    editor().session.addGutterDecoration(row, name);
  }

  function syncSourceCommentMarkers() {
    if (!editor()) return false;
    clearSourceCommentMarkers();
    if (!file()) return false;
    const Range = window.ace.require("ace/range").Range;
    for (const comment of comments()) {
      if (comment.status !== "open" || comment.anchor.kind !== "source_range"
          || comment.anchor.file !== file()) continue;
      if (comment.stale || dirty()) continue;
      const source = comment.resolved_source ?? comment.anchor;
      const firstRow = Math.max(0, source.line_start - 1);
      const lastRow = Math.max(firstRow, source.line_end - 1);
      const startColumn = source.column_start ?? 0;
      const endColumn = source.column_end ?? editor().session.getLine(lastRow).length;
      // A reattached comment whose text was deleted has an empty range: the line where
      // the text stood gets a faint mark instead, so the comment does not vanish.
      const deleted = firstRow === lastRow && startColumn === endColumn;
      state.sourceCommentMarkers.push(editor().session.addMarker(
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
    for (const edit of dirty() ? [] : suggested()) {
      if (edit.file !== file() || !edit.here) continue;
      const { line_start: lineStart, column_start: columnStart, line_end: lineEnd, column_end: columnEnd } = edit.here;
      const item = suggestedItem(edit);
      if (edit.inserted) {
        const range = new Range(lineStart - 1, columnStart, lineEnd - 1, columnEnd);
        state.sourceCommentMarkers.push(editor().session.addMarker(range, suggestedInsertClass(edit.user_id), "text", false));
        state.sourceCommentSpans.push({ range, comment: item });
      }
      if (edit.deleted) {
        const range = new Range(lineEnd - 1, columnEnd, lineEnd - 1, columnEnd + 1);
        state.sourceCommentMarkers.push(editor().session.addMarker(range, "source-suggested-delete", "text", false));
        if (!edit.inserted) state.sourceCommentSpans.push({ range, comment: item });
      }
      decorateRow(lineStart - 1, "source-suggested-line");
    }
    // The text an open proposal would replace, whatever its comment points at: a comment on
    // the PDF proposes in the source too, and the editor marks the same text the card's −
    // line shows. The server found each piece once in the file, so an edit made since then
    // that moves or duplicates it leaves that piece unmarked rather than marked wrongly.
    if (dirty()) return false;
    const text = editor().session.getValue();
    for (const comment of comments()) {
      const suggestion = comment.suggestion;
      if (comment.status !== "open" || !suggestion || suggestion.file !== file()) continue;
      for (const change of suggestion.changes) {
        const at = text.indexOf(change.old);
        if (at < 0 || text.indexOf(change.old, at + 1) >= 0) continue;
        const start = editor().session.doc.indexToPosition(at);
        const end = editor().session.doc.indexToPosition(at + change.old.length);
        state.sourceCommentMarkers.push(editor().session.addMarker(
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
    return true;
  }

  return {
    sync: syncSourceCommentMarkers,
    clear: clearSourceCommentMarkers,
    atCursor: markAtCursor,
    onRow: (row) => state.sourceCommentsByRow.get(row),
  };
}
