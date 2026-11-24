"""Hand the waiter to a tool's plugin monitor, which Claude Code runs for the whole session and
whose lines reach the agent as notifications, with no expiry to start it again.

The monitor (monitor.py, started by the tool's plugin/monitors/review.sh) and the tool's server
are both started by one Claude Code process, so that process's pid names the session to both:
the server writes the command to run under it in the tool's monitors folder, and the monitor,
which records its own pid there, runs the newest command it finds. A server with no Claude
Code process above it, or a session without the plugin, has no monitor, and the agent starts
the waiter itself. So has a Codex started from a Claude Code shell: its servers run under
that session, but answer to the Codex.
"""
from __future__ import annotations

import os
import time

import psutil

from . import shared_file
from .tool import Tool


def _parent_and_name(pid: int) -> tuple[int, str] | None:
    """*pid*'s parent, 0 when it has none, and its name. Windows keeps the pid of a process's
    creator after the creator exits, and a younger process can take that pid, even one below:
    a parent is taken only when it started before its child."""
    try:
        process = psutil.Process(pid)
        parent = process.parent()
        # A program's name without the .exe Windows gives it.
        return parent.pid if parent else 0, process.name().casefold().removesuffix(".exe")
    except psutil.Error:
        return None


def claude_pid() -> int | None:
    """The nearest process above this one named claude, unless a codex comes first."""
    # From this process, not its parent: on Windows an environment's Python runs the real
    # one under it, and the walk passes over both.
    return session_of(os.getpid())


def session_of(pid: int) -> int | None:
    """The nearest process at or above *pid* named claude, unless a codex comes first. Two
    processes started within one tick of the clock look equally old, so a taken pid can still
    lead back down, and the walk ends at a pid it has passed."""
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
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


def _is_monitor(tool: Tool, pid: int) -> bool:
    """Whether *pid* runs the tool's monitor. A monitor killed outright leaves its record, and
    the pid in it may since have gone to some other process."""
    try:
        return f"{tool.package}.monitor" in psutil.Process(pid).cmdline()
    except psutil.Error:
        return False


def monitored_session(tool: Tool) -> int | None:
    """This session's Claude Code pid when the tool's plugin monitor runs for it, else None."""
    session = claude_pid()
    if session is None:
        return None
    try:
        monitor = int((tool.monitors / f"{session}.monitor").read_text().strip())
    except (OSError, ValueError):
        return None
    return session if _is_monitor(tool, monitor) else None


def hand_over(tool: Tool, command: str) -> bool:
    """Have this session's monitor of the tool run command in place of what it ran before.
    True when a monitor is running to take it."""
    session = monitored_session(tool)
    if session is None:
        return False
    # The first line differs on every call, so a listen() call restarts a waiter that ended.
    staging = tool.monitors / f"{session}.command.new"
    staging.write_text(f"{time.time_ns()}\n{command}\n", encoding="utf-8")
    shared_file.replace(staging, tool.monitors / f"{session}.command")
    return True
