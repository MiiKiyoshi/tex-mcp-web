"""Wait for Call agent presses on the review page and print one line for each.

Run as ``python -m tex_mcp_web.waiter <port> <listener> [--codex <thread>]`` with the
listener id ``listen()`` gave, or ``<port> - --agent gemini --once`` for Gemini, which waits
in a lane of its own, names no listener, and ends after the first press it prints. The server keeps the press count and what was taken up, so
the waiter carries no state: a press made before it started answers it at once, and a line
is acknowledged only after it is printed, so a press is offered until a waiter says it
landed. A server that goes away is waited for: one [gone] line, then [back] when it answers
again. Presses go to one waiter: when a newer one takes over, one [moved] line ends this one.
The waiter ends with whoever started it, and when no one reads what it prints.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import psutil

from . import page_token


def _deliver(line: str, thread: str | None) -> None:
    """Hand a line to the agent: printed, or queued to a Codex thread until it takes it."""
    if thread is None:
        print(line, flush=True)
        return
    while subprocess.run(["codex", "queue", "--thread", thread, "--message", f"[TeX review event]\n{line}"],
                         stdin=subprocess.DEVNULL).returncode != 0:
        print("Queue delivery failed; retrying in 5 seconds", file=sys.stderr, flush=True)
        time.sleep(5)


def wait(port: int, listener: str, thread: str | None = None, agent: str | None = None,
         once: bool = False) -> None:
    base = f"http://127.0.0.1:{port}"
    lane = f"&agent={agent}" if agent else ""
    parent = psutil.Process().parent()
    gone, delay = False, 2
    # The page answers only with the reviewer's token when it asks for one. It is read from
    # its file at each call, and never put on a command line others on the machine can read.
    while parent is None or parent.is_running():
        request = urllib.request.Request(
            f"{base}/wait-review?listener={listener}&pid={os.getpid()}{lane}", headers=page_token.headers())
        try:
            with urllib.request.urlopen(request, timeout=70) as response:
                said, moved, press = response.read().decode("utf-8"), response.headers.get("X-Moved"), response.headers.get("X-Press")
        except TimeoutError:
            said, moved, press = "", None, None
        except (urllib.error.URLError, OSError) as error:
            if not gone:
                _deliver(f"[gone] review server unreachable ({getattr(error, 'reason', error)}); waiting for it", thread)
                gone = True
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if gone:
            _deliver("[back] review server reachable again", thread)
            gone, delay = False, 2
        if not said:
            continue
        _deliver(said, thread)
        if moved:
            return
        if press:
            acknowledge = urllib.request.Request(
                f"{base}/wait-review/ack?upto={press}{lane}", method="POST", headers=page_token.headers())
            try:
                urllib.request.urlopen(acknowledge, timeout=10).close()
            except (urllib.error.URLError, OSError):
                pass
            if once:
                return


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    usage = "usage: python -m tex_mcp_web.waiter <port> <listener> [--codex <thread>] [--agent gemini] [--once]"
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
    if options.get("--agent") not in (None, "gemini"):
        raise SystemExit(usage)
    try:
        wait(int(args[0]), args[1], options.get("--codex"), options.get("--agent"), bool(options.get("--once")))
    except (OSError, KeyboardInterrupt):
        # No one reads the lines any more, which Windows reports as an invalid argument and
        # others as a broken pipe: a press not printed is not acknowledged.
        os._exit(0)


if __name__ == "__main__":
    main()
