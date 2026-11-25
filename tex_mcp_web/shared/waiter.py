"""Wait for Call agent presses on a tool's page and print one line for each.

Each tool runs it as ``python -m <package>.waiter <port> <listener> [--codex <thread>]`` with
the listener id ``listen()`` gave, or ``<port> - --agent <name> --once`` for an agent the
configuration names, which waits in a lane of its own, names no listener, and ends after the
first press it prints. The server keeps the press count and what was taken up, so
the waiter carries no state: a press made before it started answers it at once, and a line
is acknowledged only after it is printed, so a press is offered until a waiter says it
landed. A server that goes away is waited for: one [gone] line, then [back] when it answers
again. Presses go to one waiter: when a newer one takes over, one [moved] line ends this one.
The waiter ends with whoever started it, and when no one reads what it prints.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

from .tool import Tool


def command(tool: Tool, port: int, listener: str) -> str:
    """The command that waits for the presses of the session listening as *listener* on the
    page at *port*, run as it is or by the plugin's monitor, with the Python running now:
    Python, so that it is the same on every system."""
    return f"{shlex.quote(Path(sys.executable).as_posix())} -m {tool.package}.waiter {port} {listener}"


def lane_command(tool: Tool, port: int, agent: str) -> str:
    """The command that waits in the lane of *agent*, an agent the configuration names, for
    one press on the page at *port*."""
    return f"{shlex.quote(Path(sys.executable).as_posix())} -m {tool.package}.waiter {port} - --agent {agent} --once"


def _codex() -> str:
    """The Codex program a line is queued with. On Windows, npm installs codex as codex.cmd,
    which hands its arguments to the real program through cmd.exe, and cmd.exe ends an argument
    at a newline: a line queued through it arrived as its first line alone, with no error. The
    real program is the codex.exe the npm package carries, found by searching the package,
    since where it sits changed between Codex versions. A codex.exe on PATH is the program
    itself. Elsewhere codex hands its arguments on whole."""
    if sys.platform != "win32":
        return "codex"
    found = shutil.which("codex")
    if found is None:
        raise FileNotFoundError("codex is not on PATH")
    if Path(found).suffix.lower() == ".exe":
        return found
    package = Path(found).parent / "node_modules" / "@openai" / "codex"
    programs = sorted(package.rglob("codex.exe"))
    if not programs:
        raise FileNotFoundError(f"no codex.exe in {package}, which {found} runs")
    return str(programs[0])


def _deliver(tool: Tool, line: str, thread: str | None) -> None:
    """Hand a line to the agent: printed, or queued to a Codex thread until it takes it."""
    if thread is None:
        print(line, flush=True)
        return
    while subprocess.run([_codex(), "queue", "--thread", thread, "--message", f"{tool.event}\n{line}"],
                         stdin=subprocess.DEVNULL).returncode != 0:
        print("Queue delivery failed; retrying in 5 seconds", file=sys.stderr, flush=True)
        time.sleep(5)


def wait(tool: Tool, port: int, listener: str, thread: str | None = None, agent: str | None = None,
         once: bool = False) -> None:
    base = f"http://127.0.0.1:{port}{tool.base}"
    lane = f"&agent={agent}" if agent else ""
    parent = psutil.Process().parent()
    gone, delay = False, 2
    # A page that asks for the reviewer's token answers only with it. It is read from its
    # file at each call, and never put on a command line others on the machine can read.
    while parent is None or parent.is_running():
        request = urllib.request.Request(
            f"{base}/wait-review?listener={listener}&pid={os.getpid()}{lane}", headers=tool.headers())
        try:
            with urllib.request.urlopen(request, timeout=70) as response:
                said, moved, press = response.read().decode("utf-8"), response.headers.get("X-Moved"), response.headers.get("X-Press")
        except TimeoutError:
            said, moved, press = "", None, None
        except (urllib.error.URLError, OSError) as error:
            if not gone:
                _deliver(tool, f"[gone] {tool.server} unreachable ({getattr(error, 'reason', error)}); waiting for it", thread)
                gone = True
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if gone:
            _deliver(tool, f"[back] {tool.server} reachable again", thread)
            gone, delay = False, 2
        if not said:
            continue
        _deliver(tool, said, thread)
        if moved:
            return
        if press:
            acknowledge = urllib.request.Request(
                f"{base}/wait-review/ack?upto={press}{lane}", method="POST", headers=tool.headers())
            try:
                urllib.request.urlopen(acknowledge, timeout=10).close()
            except (urllib.error.URLError, OSError):
                pass
            if once:
                return


def main(tool: Tool, argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    usage = f"usage: python -m {tool.package}.waiter <port> <listener> [--codex <thread>] [--agent <name>] [--once]"
    if len(args) < 2:
        raise SystemExit(usage)
    options: dict[str, str | bool] = {}
    rest = args[2:]
    while rest:
        flag = rest.pop(0)
        if flag in ("--codex", "--agent") and rest:
            options[flag] = rest.pop(0)
        elif flag == "--once":
            options[flag] = True
        else:
            raise SystemExit(usage)
    try:
        wait(tool, int(args[0]), args[1], options.get("--codex"), options.get("--agent"), bool(options.get("--once")))
    except (OSError, KeyboardInterrupt):
        # No one reads the lines any more, which Windows reports as an invalid argument and
        # others as a broken pipe: a press not printed is not acknowledged.
        os._exit(0)
