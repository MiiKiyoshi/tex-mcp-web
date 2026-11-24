"""What a context has read of the files a review tool's guide() lists, for the tool's Claude
Code hooks (guide_gate.py beside this file).

A context that calls guide() records the files its answer lists as unread, and a Read that
returned a whole file, or found it unchanged since doing so, takes it off. The tool's tools but
the open ones are refused while guide() has not answered in this context or a listed file has
not been read in full. An edit the tool's hook waits for files too keeps them under a key of
its own, edit: and what the edit is to, which does not open the tool's tools. The record is
cleared whenever a context starts again (a new session, a resumed one, a cleared one, or the
summary a compaction leaves), since what was read before is no longer in it. A subagent starts
without its parent's context and has a record of its own. What is checked is that the Read
calls returned the whole files, not that they were understood.

The record is a JSON object in ~/.cache/<tool>/guide-read/<session>, or <session>-<agent> for
a subagent: each key a project folder, or an edit:<...> key, and each value the files it still
waits for.

Shared by the review tools' plugins. It is written in tex-mcp-web, and scripts/share_page.py
copies it to the others. A copy is never edited by hand. It needs only the standard library,
since the hooks run outside the tools' environments.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

EDIT = "edit:"
UNREAD = ("The required Read calls have not been recorded for these files in this context: "
          "{files}. Use Read without offset or limit for each file, and read its full contents "
          "before retrying this call.")



def patiently(action, *args, **kwargs):
    """Do *action*, again while Windows refuses it because another hook of this context has
    the record open for a moment, for up to two seconds."""
    for _ in range(40):
        try:
            return action(*args, **kwargs)
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(0.05)
    return action(*args, **kwargs)

class Gate:
    """The record of one tool, *name* as tex-mcp-web, whose projects are folders holding
    *config*, as .tex-mcp-web.yaml. *reminder* is what a context that starts in a project is
    told, *no_guide* why a tool is refused before guide() has answered, and *open_tools* the
    tools that need nothing read."""

    def __init__(self, name: str, config: str, reminder: str, no_guide: str, open_tools: tuple[str, ...]) -> None:
        self.config, self.reminder, self.no_guide, self.open_tools = config, reminder, no_guide, open_tools
        self.records = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / name / "guide-read"

    def project_of(self, start: Path) -> Path | None:
        for folder in (start, *start.parents):
            if (folder / self.config).is_file():
                return folder
        return None

    def record_of(self, event: dict) -> Path:
        agent = event.get("agent_id")
        return self.records / (event["session_id"] + (f"-{agent}" if agent else ""))

    def unread(self, event: dict) -> dict[str, list[str]]:
        """The files still to read, by key, for this context. A record that is not an object
        counts as none."""
        try:
            record = json.loads(patiently(self.record_of(event).read_text, encoding="utf-8"))
        except FileNotFoundError:
            return {}
        return record if isinstance(record, dict) else {}

    @contextlib.contextmanager
    def held(self, event: dict):
        """This context's record, held against the other hooks of the same context while one
        rewrites it. The Reads of one turn run their hooks at once: one hook read the record
        while another had just emptied it to write and failed, and others wrote back files
        already taken off, so a file read whole stayed unread."""
        record = self.record_of(event)
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

    def keep(self, event: dict, projects: dict[str, list[str]]) -> None:
        """Written whole and then put in place, so a hook reading without the lock never sees
        it half written."""
        record = self.record_of(event)
        written = record.with_name(f"{record.name}.{os.getpid()}")
        written.write_text(json.dumps(projects), encoding="utf-8")
        patiently(os.replace, written, record)

    @staticmethod
    def response_text(event: dict) -> str:
        response = event.get("tool_response")
        blocks = response if isinstance(response, list) else [response]
        return "\n".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in blocks)

    @staticmethod
    def deny(reason: str) -> None:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))

    def listed(self, event: dict, files: list[str], *keys: str) -> None:
        """Record *files*, which guide() listed to read, as unread under each of *keys*."""
        with self.held(event):
            projects = self.unread(event)
            for key in keys:
                projects[key] = sorted(os.path.realpath(path) for path in files)
            self.keep(event, projects)

    def edit_unread(self, event: dict, key: str, files: list[Path]) -> list[str]:
        """The files an edit under edit:*key* still waits for: *files*, recorded the first
        time this context makes such an edit, less those read in full since, of those there."""
        key = EDIT + key
        with self.held(event):
            projects = self.unread(event)
            if key not in projects:
                projects[key] = sorted(os.path.realpath(file) for file in files)
                self.keep(event, projects)
        return [file for file in projects[key] if Path(file).is_file()]

    def session_start(self, event: dict) -> None:
        self.record_of(event).unlink(missing_ok=True)
        if self.project_of(Path(event["cwd"])) is not None:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": self.reminder}}))

    def file_read(self, event: dict) -> None:
        """A Read counts when it returned the whole file: from its first line, every line,
        and every character, so a file cut at a line or a size limit does not.

        A Read without offset or limit counts too when Claude Code answers that the file is
        unchanged. It answers so, without the contents, when the same Read already returned
        the file in this context, as when guide() is called again and lists files read under
        its first answer. Counting only returned contents left those files unread for good."""
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
        with self.held(event):
            projects = self.unread(event)
            if any(path in files for files in projects.values()):
                self.keep(event, {key: [file for file in files if file != path] for key, files in projects.items()})

    def before_tool(self, event: dict) -> None:
        """One server serves one project, so what this context has read through guide(), for
        any project, is what the tool's tools need."""
        if event["tool_name"].rsplit("__", 1)[-1] in self.open_tools:
            return
        projects = {project: files for project, files in self.unread(event).items() if not project.startswith(EDIT)}
        files = sorted({file for listed in projects.values() for file in listed})
        if not projects:
            self.deny(self.no_guide)
        elif files:
            self.deny(UNREAD.format(files=", ".join(files)))

    def run(self, own: dict[str, Callable[[dict], None]]) -> None:
        """Run the hook the command line names on the event Claude Code gives: one of these,
        or one of *own*, the tool's."""
        hooks = {"session-start": self.session_start, "file-read": self.file_read, "before-tool": self.before_tool, **own}
        # The event is UTF-8 whatever the system's code page, which Windows would read it in.
        hooks[sys.argv[1]](json.loads(sys.stdin.buffer.read().decode("utf-8")))
