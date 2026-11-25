// Where a comment is, as its card says it: its place as the card names it (anchorLabel) and the
// words the card quotes (commentQuote). That place is shown from the comment list by places.js.

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
