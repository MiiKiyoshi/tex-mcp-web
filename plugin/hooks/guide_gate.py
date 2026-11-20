#!/usr/bin/env python3
"""Claude Code hooks that keep an agent from working on a paper through tex-mcp before it has
read, in the context it is working in, the files guide() lists.

guide() names the files to read in full: the paper's guideline and the ones it extends, when
the paper sets guideline, and the terms the paper's reference papers use, when the paper sets
references. A context that calls it records those files as unread, a Read that
returned a whole file, or found it unchanged since doing so, takes it off, and every tex-mcp
tool but guide(), setup_info() and image() is refused while guide() has not answered in this
context or a listed file has not been read in full. image() only draws a page, which looking at
needs none of them. The record is cleared whenever a context starts again (a new session, a resumed one, a
cleared one, or the summary a compaction leaves), since what was read before is no longer in
it. A subagent starts without its parent's context and has a record of its own. What is
checked is that the Read calls returned the whole files, not that they were understood.

  session-start  SessionStart: clear the record, and in a project say what to do.
  guide-read     PostToolUse on guide(): record the files its answer lists to read.
  file-read      PostToolUse on Read: take a file read whole off the record.
  before-tool    PreToolUse on tex-mcp's tools: refuse one other than guide(), setup_info() and
                 image()
                 while this context has not read the files.
  before-edit    PreToolUse on Edit, Write, MultiEdit and NotebookEdit: refuse a change to a
                 .tex or .bib file of a project while this context has not read the files
                 guide() would list, so an agent that edits the paper itself, without calling
                 guide(), still reads them. Its record is kept apart from guide()'s, so it
                 does not open tex-mcp's tools.

A project is a folder holding .tex-mcp-web.yaml, found from the working folder upward. The
script needs only the standard library, since it runs outside the server's environment.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

CONFIG = ".tex-mcp-web.yaml"
RECORDS = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tex-mcp-web" / "guide-read"
# The tools that come before the files: guide() names them, and setup_info() serves setting
# a project up, before there is a guide() to call. image() only draws a page, so a context
# that looks at the paper without working on it need not read the files.
OPEN_TOOLS = ("guide", "setup_info", "image")

REMINDER = ("tex-mcp: before using tex-mcp tools in this context, call guide() and read every "
            "file in its read list in full. Use Read without offset or limit. tex-mcp tools other "
            "than guide(), setup_info() and image() are blocked until guide() and the required Read calls "
            "are recorded. A [review] notification from the tex-mcp monitor means the user pressed "
            "Call agent on the review page. It is the user's request to handle the review now, so "
            "handle it without asking first.")
NO_GUIDE = ("guide() has not succeeded in this context. Call it, then use Read without offset or "
            "limit for every file in its read list. Read each file in full before retrying this call.")
MISSING_TERMS = ("The term list {terms} has not been built. Run `tex-mcp-web terms` in {project}, "
                 "or call guide(), then read it in full before retrying this edit.")
MISSING_GUIDES = "These writing guidelines named in {config} are not there: {files}. Fix the path first."
SOURCES = (".tex", ".bib")
EDIT = "edit:"
UNREAD = ("The required Read calls have not been recorded for these files in this context: "
          "{files}. Use Read without offset or limit for each file, and read its full contents "
          "before retrying this call.")


def project_of(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / CONFIG).is_file():
            return folder
    return None


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


def record_of(event: dict) -> Path:
    agent = event.get("agent_id")
    return RECORDS / (event["session_id"] + (f"-{agent}" if agent else ""))


def unread(event: dict) -> dict[str, list[str]]:
    """The files still to read, by project, for this context."""
    try:
        record = json.loads(record_of(event).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return record if isinstance(record, dict) else {}


@contextlib.contextmanager
def held(event: dict):
    """This context's record, held against the other hooks of the same context while one
    rewrites it: the Reads of one turn run their hooks at once."""
    record = record_of(event)
    record.parent.mkdir(parents=True, exist_ok=True)
    with open(record.with_name(record.name + ".lock"), "w") as lock:
        if os.name == "nt":
            import msvcrt

            # Waits for the lock, a second at a time, and gives up after ten.
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def keep(event: dict, projects: dict[str, list[str]]) -> None:
    """Written whole and then put in place, so a hook reading without the lock never sees it
    half written."""
    record = record_of(event)
    written = record.with_name(f"{record.name}.{os.getpid()}")
    written.write_text(json.dumps(projects), encoding="utf-8")
    os.replace(written, record)


def response_text(event: dict) -> str:
    response = event.get("tool_response")
    blocks = response if isinstance(response, list) else [response]
    return "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in blocks)


def session_start(event: dict) -> None:
    record_of(event).unlink(missing_ok=True)
    if project_of(Path(event["cwd"])) is not None:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": REMINDER}}))


def guide_read(event: dict) -> None:
    try:
        answer = json.loads(response_text(event))
    except ValueError:
        return
    if not isinstance(answer, dict) or not isinstance(answer.get("read"), list):
        return
    cwd = Path(event["cwd"])
    project = project_of(cwd) or cwd
    with held(event):
        projects = unread(event)
        projects[str(project)] = sorted(os.path.realpath(path) for path in answer["read"])
        keep(event, projects)


def file_read(event: dict) -> None:
    """A Read counts when it returned the whole file: from its first line, every line, and
    every character, so a file cut at a line or a size limit does not.

    A Read without offset or limit counts too when Claude Code answers that the file is
    unchanged. It answers so, without the contents, when the same Read already returned the
    file in this context, as when guide() is called again and lists files read under its first
    answer. Counting only returned contents left those files unread for good."""
    response = event.get("tool_response") or {}
    returned = response.get("file") or {}
    request = event.get("tool_input") or {}
    path = os.path.realpath(returned.get("filePath") or request.get("file_path", ""))
    if response.get("type") == "file_unchanged":
        if request.get("offset") is not None or request.get("limit") is not None:
            return
    else:
        try:
            whole = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        if not (returned.get("startLine") == 1 and returned.get("numLines") == returned.get("totalLines")
                and len(returned.get("content") or "") >= len(whole.rstrip("\n"))):
            return
    with held(event):
        projects = unread(event)
        if any(path in files for files in projects.values()):
            keep(event, {project: [file for file in files if file != path] for project, files in projects.items()})


def before_tool(event: dict) -> None:
    """One tex-mcp server serves one project, so what this context has read through guide() is
    what its tools need."""
    if event["tool_name"].rsplit("__", 1)[-1] in OPEN_TOOLS:
        return
    projects = {project: files for project, files in unread(event).items() if not project.startswith(EDIT)}
    files = sorted({file for listed in projects.values() for file in listed})
    if not projects:
        reason = NO_GUIDE
    elif files:
        reason = UNREAD.format(files=", ".join(files))
    else:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def before_edit(event: dict) -> None:
    """The project of a file is the one found from its folder upward, or else the one found
    from the working folder, whose dir may point at a paper kept elsewhere; either way the file
    has to lie in the folder that project watches."""
    request = event.get("tool_input") or {}
    target = request.get("file_path") or request.get("notebook_path") or ""
    if not target.endswith(SOURCES):
        return
    path = Path(os.path.realpath(Path(event["cwd"]) / target))
    for project in dict.fromkeys(p for p in (project_of(path.parent), project_of(Path(event["cwd"]))) if p):
        watched, terms, guides = required(project)
        if watched == path or watched in path.parents:
            break
    else:
        return
    missing = [guide for guide in guides if not guide.is_file()]
    reasons = [MISSING_GUIDES.format(config=project / CONFIG, files=", ".join(map(str, missing)))] if missing else []
    if terms and not terms[0].is_file():
        reasons.insert(0, MISSING_TERMS.format(terms=terms[0], project=project))
    key = EDIT + str(project)
    with held(event):
        projects = unread(event)
        if key not in projects:
            projects[key] = sorted(os.path.realpath(file) for file in terms + guides)
            keep(event, projects)
    files = [file for file in projects[key] if Path(file).is_file()]
    if files:
        reasons.append(UNREAD.format(files=", ".join(files)).replace("this call", "this edit"))
    if reasons:
        deny(" ".join(reasons))


if __name__ == "__main__":
    hooks = {"session-start": session_start, "guide-read": guide_read, "file-read": file_read,
             "before-tool": before_tool, "before-edit": before_edit}
    hooks[sys.argv[1]](json.load(sys.stdin))
