#!/usr/bin/env python3
"""Claude Code hooks that keep an agent from writing a paper before it has read, in the
context it is working in, the terms the paper's reference papers use.

The list is ``.tex-mcp-web/terms.md`` beside the project's ``.tex-mcp-web.yaml``, kept by the
tex-mcp server when ``references`` is set. While a project has one, an edit of the paper's
sources and every tex-mcp tool but setup_info() is refused until a Read in this context has
returned the whole list as it is now. The record is cleared whenever a context starts again (a
new session, a resumed one, a cleared one, or the summary a compaction leaves), and a rebuilt
list is read again. A subagent has a record of its own.

  session-start  SessionStart: clear the record, and in a project with a list say what to do.
  file-read      PostToolUse on Read: record a list read whole.
  before-tool    PreToolUse on tex-mcp's tools and on Edit, Write and MultiEdit: refuse while
                 the list has not been read.

The script needs only the standard library, since it runs outside the server's environment.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sys
from pathlib import Path

CONFIG = ".tex-mcp-web.yaml"
TERMS = Path(".tex-mcp-web") / "terms.md"
RECORDS = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tex-mcp-web" / "terms-read"
OPEN_TOOLS = ("setup_info",)
EDITS = ("Edit", "Write", "MultiEdit")

REMINDER = ("tex-mcp: this paper lists the terms its reference papers use in {path}. Read it in full, "
            "with Read and without offset or limit, before editing the paper or using tex-mcp tools. "
            "Both are blocked until then, again after each compaction.")
UNREAD = ("Read {path} in full first, with Read and without offset or limit. It lists the terms the "
          "paper's reference papers use. Editing the paper and tex-mcp tools are blocked in this "
          "context until then.")


def project_of(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / CONFIG).is_file():
            return folder
    return None


def sources_of(project: Path) -> set[str]:
    """The suffixes of the paper's sources: .tex, and the main file's own when it is another.
    Read with a pattern rather than a YAML parser, since the file is written by init."""
    suffixes = {".tex"}
    found = re.search(r"^main:\s*['\"]?([^'\"\s#]+)", (project / CONFIG).read_text(encoding="utf-8"), re.MULTILINE)
    if found:
        suffixes.add(Path(found.group(1)).suffix.lower())
    return suffixes


def record_of(event: dict) -> Path:
    agent = event.get("agent_id")
    return RECORDS / (event["session_id"] + (f"-{agent}" if agent else ""))


def read_lists(event: dict) -> dict[str, int]:
    """The lists this context has read whole, each with the modification time it had then."""
    try:
        record = json.loads(record_of(event).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return record if isinstance(record, dict) else {}


@contextlib.contextmanager
def held(event: dict):
    """This context's record, held against the other hooks of the same context: the Reads
    of one turn run their hooks at once."""
    record = record_of(event)
    record.parent.mkdir(parents=True, exist_ok=True)
    with open(record.with_name(record.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def keep(event: dict, lists: dict[str, int]) -> None:
    record = record_of(event)
    written = record.with_name(f"{record.name}.{os.getpid()}")
    written.write_text(json.dumps(lists), encoding="utf-8")
    os.replace(written, record)


def session_start(event: dict) -> None:
    record_of(event).unlink(missing_ok=True)
    project = project_of(Path(event["cwd"]))
    if project is not None and (project / TERMS).is_file():
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": REMINDER.format(path=project / TERMS)}}))


def file_read(event: dict) -> None:
    """A Read counts when it returned the whole list: from its first line, every line, and
    every character, so a list cut at a line or a size limit does not."""
    returned = (event.get("tool_response") or {}).get("file") or {}
    path = Path(os.path.realpath(returned.get("filePath") or (event.get("tool_input") or {}).get("file_path", "")))
    if path.name != TERMS.name or path.parent.name != TERMS.parent.name or not (path.parent.parent / CONFIG).is_file():
        return
    try:
        whole = path.read_text(encoding="utf-8")
        modified = path.stat().st_mtime_ns
    except (OSError, UnicodeDecodeError):
        return
    if not (returned.get("startLine") == 1 and returned.get("numLines") == returned.get("totalLines")
            and len(returned.get("content") or "") >= len(whole.rstrip("\n"))):
        return
    with held(event):
        lists = read_lists(event)
        lists[str(path)] = modified
        keep(event, lists)


def before_tool(event: dict) -> None:
    tool = event["tool_name"]
    if tool in EDITS:
        target = Path((event.get("tool_input") or {}).get("file_path", ""))
        project = project_of(target.parent) if target.is_absolute() else None
        if project is None or target.suffix.lower() not in sources_of(project):
            return
    else:
        if not tool.startswith("mcp__tex-mcp__") or tool.rsplit("__", 1)[-1] in OPEN_TOOLS:
            return
        project = project_of(Path(event["cwd"]))
        if project is None:
            return
    terms = project / TERMS
    try:
        modified = terms.stat().st_mtime_ns
    except FileNotFoundError:
        return
    if read_lists(event).get(os.path.realpath(terms)) == modified:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": UNREAD.format(path=terms)}}))


if __name__ == "__main__":
    hooks = {"session-start": session_start, "file-read": file_read, "before-tool": before_tool}
    hooks[sys.argv[1]](json.load(sys.stdin))
