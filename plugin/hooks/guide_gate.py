#!/usr/bin/env python3
"""Claude Code hooks that keep an agent from working on a paper through tex-mcp before it has
read, in the context it is working in, the files guide() lists.

guide() names the files to read in full: the terms the paper's reference papers use, when
the paper sets references. A context that calls it records those files as unread, a Read that
returned a whole file takes it off, and every tex-mcp tool but guide() and setup_info() is
refused while guide() has not answered in this context or a listed file has not been read in
full. The record is cleared whenever a context starts again (a new session, a resumed one, a
cleared one, or the summary a compaction leaves), since what was read before is no longer in
it. A subagent starts without its parent's context and has a record of its own. What is
checked is that the Read calls returned the whole files, not that they were understood.

  session-start  SessionStart: clear the record, and in a project say what to do.
  guide-read     PostToolUse on guide(): record the files its answer lists to read.
  file-read      PostToolUse on Read: take a file read whole off the record.
  before-tool    PreToolUse on tex-mcp's tools: refuse one other than guide() and setup_info()
                 while this context has not read the files.

A project is a folder holding .tex-mcp-web.yaml, found from the working folder upward. The
script needs only the standard library, since it runs outside the server's environment.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
from pathlib import Path

CONFIG = ".tex-mcp-web.yaml"
RECORDS = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tex-mcp-web" / "guide-read"
# The tools that come before the files: guide() names them, and setup_info() serves setting
# a project up, before there is a guide() to call.
OPEN_TOOLS = ("guide", "setup_info")

REMINDER = ("tex-mcp: before using tex-mcp tools in this context, call guide() and read every "
            "file in its read list in full. Use Read without offset or limit. tex-mcp tools other "
            "than guide() and setup_info() are blocked until guide() and the required Read calls "
            "are recorded.")
NO_GUIDE = ("guide() has not succeeded in this context. Call it, then use Read without offset or "
            "limit for every file in its read list. Read each file in full before retrying this call.")
UNREAD = ("The required Read calls have not been recorded for these files in this context: "
          "{files}. Use Read without offset or limit for each file, and read its full contents "
          "before retrying this call.")


def project_of(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / CONFIG).is_file():
            return folder
    return None


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
    every character, so a file cut at a line or a size limit does not."""
    returned = (event.get("tool_response") or {}).get("file") or {}
    path = os.path.realpath(returned.get("filePath") or (event.get("tool_input") or {}).get("file_path", ""))
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
    projects = unread(event)
    files = sorted({file for listed in projects.values() for file in listed})
    if not projects:
        reason = NO_GUIDE
    elif files:
        reason = UNREAD.format(files=", ".join(files))
    else:
        return
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    hooks = {"session-start": session_start, "guide-read": guide_read, "file-read": file_read,
             "before-tool": before_tool}
    hooks[sys.argv[1]](json.load(sys.stdin))
