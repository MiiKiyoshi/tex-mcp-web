// The Preview's own part of the PDF view the review pages share (shared/pdf-view.js). The Preview
// shows each build's PDF, named by its digest, and a comment written on it names that PDF too: the
// server places a comment only on the PDF it was made on. Source shows words selected on it where
// SyncTeX says they are written, and the translation follows the page it shows.
//
// previewHooks takes digest(), the digest of the PDF shown; showWhereWritten({ page, rects,
// quote }), which shows words selected on a page where the source writes them (places.js); and
// scheduleTranslation(delay). It gives the hooks createPdfView takes for these. pdfRefusal is the
// comment panel's hook for a comment refused because its PDF is being rebuilt or was rebuilt.

export function previewHooks({ digest, showWhereWritten, scheduleTranslation }) {
  return {
    url: () => `/pdf?v=${encodeURIComponent(digest() ?? Date.now().toString())}`,
    // Not while the page knows no PDF, as after the server started again before a build.
    words: ({ page, bbox, rects, quote, excerpt }) => (digest()
      ? [{ kind: "text_selection", quote, selection: { page, bbox, rects }, pdf_digest: digest() }, `PDF text: "${excerpt}"`]
      : null),
    box: ({ page, bbox }) => [{ kind: "area", page, bbox, pdf_digest: digest() }, `PDF area on p${page}`],
    // Source opens the editor beside the Preview, in Split, where SyncTeX says the selected lines
    // come from, and selects the words there.
    source: (selection) => { if (digest()) showWhereWritten(selection); },
    pageChanged: () => scheduleTranslation(600),
    // Asked for before the pages existed, and page 1 fires no change.
    layoutReady: () => scheduleTranslation(0),
  };
}

// The PDF the selection was made on is being rebuilt, or was rebuilt since: a comment is placed
// only on a selection made on the PDF the server has. What was written waits in the comment box
// for the next selection.
export function pdfRefusal(refusal, body) {
  if (refusal.reason !== "rebuilding" && refusal.reason !== "rebuilt") return null;
  const redo = body.anchor.kind === "area" ? "draw the box again" : "select the text again";
  return { keep: true, say: refusal.reason === "rebuilding"
    ? `The PDF is being rebuilt, so this comment can't be placed on it yet. When the new PDF appears, ${redo}. `
      + "What you wrote will still be in the comment box."
    : `The PDF was rebuilt after you made this selection, so the comment wasn't posted. Please ${redo}. `
      + "What you wrote will still be in the comment box." };
}
