---
name: tex-mcp-web
description: Use when reviewing or editing a TeX paper through its live PDF and comment queue
---

# tex-mcp-web

`paper()` returns compact open comments alongside paper orientation. A text
comment contains the human comment, selected PDF quote, page, and exact bbox.
The quote is the source-search key: locate a distinctive fragment with `rg`,
then read only the surrounding source lines.

`image(comment_id=...)` is used only when rendering adds information, such as
figure, table, equation, line-wrap, or spacing problems. The stored bbox is
exact. Its default `margin=12` adds render-time context in PDF points;
`margin=0` renders the exact bbox and a larger value shows more surroundings.

Text-comment creation does not depend on SyncTeX. SyncTeX remains useful for
source-to-PDF navigation and source-range rendering, but its reverse mapping is
not a source annotation.

After editing, `compile()` asks the running daemon to build, refresh anchors,
and notify the viewer. Visual changes receive an additional `image()` check
before the comment is resolved.

`goto(target)` accepts an exact rendered quote as well as a section, label,
page, or source line. Exact quotes are highlighted temporarily in the viewer.
