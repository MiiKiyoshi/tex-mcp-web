# Configuration

`do tex init` writes `.tex-mcp-web.yaml` at the paper's root. A saved change applies at once,
except `port` and `dir`, which apply when Claude Code or Codex restarts.

```yaml
main: main.tex
watch:
- '*.tex'
- '*.bib'
ignore:
- '*_backup.tex'
compiler: auto
port: 8765
guideline: my-field
translation:
  language: Korean
  instructions: Keep cell names in English.
overleaf: https://www.overleaf.com/project/0123456789abcdef01234567
```

| Field | Effect |
|---|---|
| `main` | Top-level source file compiled into the PDF. |
| `dir` | Folder holding the paper, relative to this file or absolute. Unset means this file's folder. |
| `watch` | Source files the Source tab offers. |
| `ignore` | Patterns excluded even when they match `watch`. |
| `compiler` | `auto` (latexmk for LaTeX, pandoc for Markdown or text), `latexmk`, `pdflatex`, `xelatex`, `lualatex`, or `pandoc`. |
| `port` | This paper's review page port. Any free port works. Init picks the first free one from 8765, so each paper can have its own. |
| `guideline` | The writing guideline the agent reads before editing: the name of a folder of your own under `~/.config/tex-mcp-web/guidelines/<name>/` holding a `GUIDELINE.md`. A guideline whose file opens with front matter `extends: <name>` is read after the one it names. Unset means none. |
| `references` | Folder holding only the papers yours builds on, converted to Markdown, relative to this file or absolute. Unset means no list of their terms. |
| `translation` | `language` puts a button for that language beside **+ Note** that shows each paragraph of the source translated by `claude -p`, with those on the current page marked. `instructions` adds rules for the translator, one per line. A paragraph is translated once per text, and of a figure or table only the caption is. Unset means no translation. |
| `overleaf` | The paper's Overleaf project, by its link, which an open page follows and the sync button reads. Unset means none. |
| `overleaf_add` | Patterns for the files only here that go to the Overleaf project, by **Send** or under **Live** by themselves, in a folder that is no git clone. Unset means the paper's sources and figures: `*.tex`, `*.bib`, `*.sty`, `*.cls`, `*.bst`, `*.png`, `*.jpg`, `*.jpeg`, `*.pdf`, `*.eps` and `*.svg`. A file of a kind the project already holds is added too, and the paper's compiled PDF never is. |
| `overleaf_sync` | `true` is **Live** in Settings, which keeps the paper's files and the project's the same both ways while a page is open, and brings new threads in by themselves. Unset is **By hand**: files change only by git pull and commit & push in a git clone, or by Get and Send otherwise. |
