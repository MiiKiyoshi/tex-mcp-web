"""Hand the review waiter to the tex-mcp plugin's monitor, which Claude Code runs for the whole
session and whose lines reach the agent as notifications, with no expiry to start it again.

The monitor (plugin/monitors/review.sh) and this server are both started by one Claude Code
process, so that process's pid names the session to both: the server writes the command to
run under it, and the monitor, which records its own pid there, runs the newest command it
finds. A server with no Claude Code process above it, or a session without the plugin, has no
monitor, and the agent starts the waiter itself. So has a Codex started from a Claude Code
shell: its servers run under that session, but answer to the Codex.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

DIRECTORY = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "tex-mcp-web" / "monitor"


def _parent_and_name(pid: int) -> tuple[int, str] | None:
    result = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                            capture_output=True, text=True, check=False)
    fields = result.stdout.split(None, 1)
    if len(fields) != 2:
        return None
    return int(fields[0]), fields[1].strip()


def claude_pid() -> int | None:
    """The nearest process above this one named claude, unless a codex comes first."""
    pid = os.getppid()
    while pid > 1:
        found = _parent_and_name(pid)
        if found is None:
            return None
        parent, name = found
        if os.path.basename(name) == "codex":
            return None
        if os.path.basename(name) == "claude":
            return pid
        pid = parent
    return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def monitored_session() -> int | None:
    """This session's Claude Code pid when the plugin's monitor runs for it, else None."""
    session = claude_pid()
    if session is None:
        return None
    try:
        monitor = int((DIRECTORY / f"{session}.monitor").read_text().strip())
    except (OSError, ValueError):
        return None
    return session if _alive(monitor) else None


def hand_over(command: str) -> bool:
    """Have this session's monitor run command in place of what it ran before. True when a
    monitor is running to take it."""
    session = monitored_session()
    if session is None:
        return False
    # The first line differs on every call, so a listen() call restarts a waiter that ended.
    staging = DIRECTORY / f"{session}.command.new"
    staging.write_text(f"{time.time_ns()}\n{command}\n", encoding="utf-8")
    staging.replace(DIRECTORY / f"{session}.command")
    return True
