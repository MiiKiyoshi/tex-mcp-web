#!/usr/bin/env python3
"""Claude Code hooks that keep an agent from working on a paper through tex-mcp before it has
read, in the context it is working in, the files guide() lists.

guide() names the files to read in full: the paper's guideline and the ones it extends, when
the paper sets guideline, and the terms the paper's reference papers use, when the paper sets
references. Every tex-mcp tool but guide(), setup_info() and image() is refused while guide()
has not answered in this context or a listed file has not been read in full. image() only draws
a page, which looking at needs none of them. guide_record.py beside this file keeps what each
context read, says when a Read counts, and starts again with the context.

  session-start  SessionStart: clear the record, and in a project say what to do.
  guide-read     PostToolUse on guide(): record the files its answer lists to read.
  file-read      PostToolUse on Read: take a file read whole off the record.
  before-tool    PreToolUse on tex-mcp's tools: refuse one other than guide(), setup_info() and
                 image() while this context has not read the files.
  before-edit    PreToolUse on Edit, Write, MultiEdit and NotebookEdit: refuse a change to a
                 .tex or .bib file of a project while this context has not read the files
                 guide() would list, so an agent that edits the paper itself, without calling
                 guide(), still reads them. Its record is kept apart from guide()'s, so it
                 does not open tex-mcp's tools.

A project is a folder holding .tex-mcp-web.yaml, found from the working folder upward. The
script needs only the standard library, since it runs outside the server's environment.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_record import UNREAD, Gate  # noqa: E402

CONFIG = ".tex-mcp-web.yaml"
REMINDER = ("tex-mcp: before using tex-mcp tools in this context, call guide() and read every "
            "file in its read list in full. Use Read without offset or limit. tex-mcp tools other "
            "than guide(), setup_info() and image() are blocked until guide() and the required Read calls "
            "are recorded. A [review] notification from the tex-mcp monitor means the user pressed "
            "Call agent on the review page. It is the user's request to handle the review now, so "
            "handle it without asking first.")
NO_GUIDE = ("guide() has not succeeded in this context. Call it, then use Read without offset or "
            "limit for every file in its read list. Read each file in full before retrying this call.")
# The tools that come before the files: guide() names them, and setup_info() serves setting
# a project up, before there is a guide() to call. image() only draws a page, so a context
# that looks at the paper without working on it need not read the files.
GATE = Gate("tex-mcp-web", CONFIG, REMINDER, NO_GUIDE, open_tools=("guide", "setup_info", "image"))
MISSING_TERMS = ("The term list {terms} has not been built. Run `tex-mcp-web terms` in {project}, "
                 "or call guide(), then read it in full before retrying this edit.")
MISSING_GUIDES = "These writing guidelines named in {config} are not there: {files}. Fix the path first."
SOURCES = (".tex", ".bib")


def settings(project: Path) -> dict[str, object]:
    """dir, references and guideline from a configuration, read with the standard library: a
    top-level key holds one value or the list of items under it."""
    found: dict[str, object] = {}
    key = None
    for line in (project / CONFIG).read_text(encoding="utf-8").splitlines():
        text = line.split(" #", 1)[0].rstrip()
        if not text.strip() or text.lstrip().startswith("#"):
            continue
        if text.lstrip().startswith("- ") and key is not None:
            found.setdefault(key, [])
            if isinstance(found[key], list):
                found[key].append(text.lstrip()[2:].strip().strip("'\""))
        elif not text[0].isspace() and ":" in text:
            key, value = (part.strip() for part in text.split(":", 1))
            if value:
                found[key] = value.strip("'\"")
    return found


def guideline_files(name: str) -> list[Path]:
    """The guideline a project names and every one it extends, base first, as guide() lists
    them: a folder under the user's guidelines folder, whose GUIDELINE.md may open with front
    matter holding extends: <name>."""
    root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "tex-mcp-web" / "guidelines"
    files: list[Path] = []
    while name and len(files) < 20:
        path = (root / name / "GUIDELINE.md").resolve()
        if path in files:
            break
        files.insert(0, path)
        name = ""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            break
        if text.startswith("---\n") and (end := text.find("\n---\n", 3)) >= 0:
            for line in text[4:end].splitlines():
                if line.startswith("extends:"):
                    name = line.split(":", 1)[1].strip().strip("'\"")
    return files


def required(project: Path) -> tuple[Path, list[Path], list[Path]]:
    """The folder the project watches, and the files guide() would list for it: its term list
    when it sets references, and its writing guideline with those it extends."""
    found = settings(project)
    watched = (project / str(found.get("dir") or ".")).resolve()
    terms = [project.resolve() / ".tex-mcp-web" / "terms.md"] if found.get("references") else []
    name = found.get("guideline")
    return watched, terms, guideline_files(name) if isinstance(name, str) else []


def guide_read(event: dict) -> None:
    """One tex-mcp server serves one project: the one found from the working folder."""
    try:
        answer = json.loads(GATE.response_text(event))
    except ValueError:
        return
    if not isinstance(answer, dict) or not isinstance(answer.get("read"), list):
        return
    cwd = Path(event["cwd"])
    GATE.listed(event, answer["read"], str(GATE.project_of(cwd) or cwd))


def before_edit(event: dict) -> None:
    """The project of a file is the one found from its folder upward, or else the one found
    from the working folder, whose dir may point at a paper kept elsewhere; either way the file
    has to lie in the folder that project watches."""
    request = event.get("tool_input") or {}
    target = request.get("file_path") or request.get("notebook_path") or ""
    if not target.endswith(SOURCES):
        return
    path = Path(os.path.realpath(Path(event["cwd"]) / target))
    for project in dict.fromkeys(p for p in (GATE.project_of(path.parent), GATE.project_of(Path(event["cwd"]))) if p):
        watched, terms, guides = required(project)
        if watched == path or watched in path.parents:
            break
    else:
        return
    missing = [guide for guide in guides if not guide.is_file()]
    reasons = [MISSING_GUIDES.format(config=project / CONFIG, files=", ".join(map(str, missing)))] if missing else []
    if terms and not terms[0].is_file():
        reasons.insert(0, MISSING_TERMS.format(terms=terms[0], project=project))
    files = GATE.edit_unread(event, str(project), terms + guides)
    if files:
        reasons.append(UNREAD.format(files=", ".join(files)).replace("this call", "this edit"))
    if reasons:
        GATE.deny(" ".join(reasons))


if __name__ == "__main__":
    GATE.run({"guide-read": guide_read, "before-edit": before_edit})
