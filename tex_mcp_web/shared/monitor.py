"""A tool's review monitor. Claude Code starts it with the session, from the tool's
plugin/monitors/review.sh, and turns each line it prints into a notification. It prints nothing
of its own: it runs the newest waiter command the tool's server hands this session (see
plugin_monitor), at the session's start or on listen(), in place of the one it ran before, and
passes on what the waiter prints. It ends with the session's Claude Code process, and at once
where none is above it, since nothing can then hand it a waiter.

Each tool runs it as ``python -m <package>.monitor [session pid | --from pid]``.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time

import psutil

from . import plugin_monitor, shared_file
from .tool import Tool


def _end(child: subprocess.Popen | None) -> None:
    """End the waiter and all it started: a request it waits on would outlive it."""
    if child is None:
        return
    try:
        tree = [psutil.Process(child.pid), *psutil.Process(child.pid).children(recursive=True)]
    except psutil.Error:
        return
    for process in tree:
        try:
            process.terminate()
        except psutil.Error:
            pass
    for process in psutil.wait_procs(tree, timeout=3)[1]:
        try:
            process.kill()
        except psutil.Error:
            pass


def main(tool: Tool, argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["--from"]:
        # The process to look up from, where this one's own parents do not lead there.
        session = plugin_monitor.session_of(int(args[1]))
    else:
        session = int(args[0]) if args else plugin_monitor.claude_pid()
    if session is None:
        return
    directory = tool.monitors
    record, command = directory / f"{session}.monitor", directory / f"{session}.command"
    # A command left by a monitor killed outright, for a session whose pid this one now has,
    # is not this session's. It goes before the record, after which this session hands over its own.
    directory.mkdir(parents=True, exist_ok=True)
    command.unlink(missing_ok=True)
    record.write_text(str(os.getpid()), encoding="utf-8")

    def stop(*_):
        raise SystemExit(0)

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), stop)
    child, current = None, None
    try:
        while psutil.pid_exists(session):
            try:
                handed = shared_file.read_text(command)
            except OSError:
                handed = None
            if handed and handed != current:
                _end(child)
                current = handed
                # The first line only makes each handing over differ from the last.
                child = subprocess.Popen(shlex.split(handed.splitlines()[1]), stdin=subprocess.DEVNULL)
            time.sleep(1)
    finally:
        _end(child)
        record.unlink(missing_ok=True)
        command.unlink(missing_ok=True)
