# Set up a folder for tex review

The user said "do tex init", or `listen` found no config. Setting up writes
`.tex-mcp-web.yaml` in the folder this agent session started in. Inspect first, show one
summary, write only after the user agrees.

## 1. Inspect (write nothing)

- A config here or in a parent folder (`.tex-mcp-web.yaml`): if one exists, show the user
  its values and stop. A change is an edit to that file, after the user agrees.
- Main file: `grep -l '\\documentclass' *.tex`. One hit is the proposal; several are asked.
  No hit here: ask where the paper is; that folder becomes `dir`.
- Port: the first free one from 8765 (`ss -ltn`, or `lsof -iTCP -sTCP:LISTEN -P -n` on macOS), also skipping the port in a
  `.html-mcp-web.yaml` in this folder; both tools default to 8765.
- Compiler: `command -v latexmk`. Missing: tell the user; do not install a TeX distribution.

## 2. Confirm

Show one summary, in the user's language, like:

    main     : main.tex  (the only file with \documentclass)
    port     : 8765
    watch    : *.tex *.bib -> 9 files (main.tex, sections/*.tex, refs.bib)
    ignore   : *_backup.tex
    compiler : auto (latexmk)

Add `dir` when the paper lives in another folder. Ask once; change only what the user
corrects.

## 3. Write

In the session's folder, with the command that `listen()`'s missing-config error names:

    tex-mcp-web --port <port> init --main <file>
    tex-mcp-web config <dir|compiler|watch|ignore> <value>   # only for changed fields

## 4. Tell the user

The folder is set up; saying "do tex listen" starts the review page.
