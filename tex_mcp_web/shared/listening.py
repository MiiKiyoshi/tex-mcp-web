"""How a session listens for Call agent: the command its agent runs and the words that tell
the agent how to run it in its client, and listening from the session's start where the
plugin's monitor runs. Each review tool calls these with its Tool and its page's port. The
page's side of a claim is calls.py."""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import urllib.request

from . import agents, plugin_monitor, waiter
from .tool import Tool

# The id this process's session listens by. One per process, so a session asked to listen
# again restarts its waiter without that waiter hearing that another session took over.
LISTENER = secrets.token_hex(8)

# How long a session's server waits for the plugin's monitor, which Claude Code starts beside it.
MONITOR_START = 30.0


def claim(tool: Tool, port: int, session: int | None, if_free: bool) -> bool:
    """Make this session's waiter the one the page's presses go to, or with *if_free* only
    when no other waiter runs. *session* is the pid of the Claude Code process it runs under,
    or None. True when the presses go to it."""
    query = f"listener={LISTENER}" + ("&if_free=1" if if_free else "") + (
        f"&session={session}" if session is not None else "")
    request = urllib.request.Request(f"http://127.0.0.1:{port}{tool.base}/wait-review/listen?{query}",
                                     headers=tool.headers(), method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())["listening"]


def command(tool: Tool, port: int, client: str, configured: dict[str, dict[str, list[str]]]) -> tuple[str, str | None]:
    """The command the agent of *client* runs to hear presses, and the lane it waits in. An
    agent the configuration names waits in a lane of its own and takes no presses from the
    session listening; any other takes the page's presses from whoever had them."""
    lane = agents.lane_of(client, configured)
    if lane is not None:
        return waiter.lane_command(tool, port, lane), lane
    claim(tool, port, plugin_monitor.claude_pid(), if_free=False)
    return waiter.command(tool, port, LISTENER), None


def how(tool: Tool, client: str, command: str, lane: str | None) -> str:
    """How the agent of *client* runs *command*, and what the lines it prints mean, but for the
    press line, whose handling is the tool's own."""
    return _wait_method(tool, client, command, lane) + (
        f" [gone] means the {tool.server} is unreachable; the waiter keeps retrying. "
        "[back] means it is reachable again. [moved] means a newer waiter took over "
        "listening, in another session or in this one as the line says, and this waiter has ended.")


def _wait_method(tool: Tool, client: str, command: str, lane: str | None) -> str:
    name = client.casefold()
    if lane is not None:
        return (
            "Run <command> as a background shell command, then end the turn and do not poll. "
            f"It shows {lane} as waiting on the {tool.page} and ends as soon as the {tool.person} "
            f"presses Call agent for {lane}, printing the {tool.press} line, which may end with "
            f"[model=M effort=E], the model and effort the {tool.person} picked. After handling "
            "that press, start the command again; otherwise leave it stopped and tell the user "
            f"in one line. Stop a running one when the {tool.work} is finished or the user asks."
        )
    if "claude" in name:
        if plugin_monitor.hand_over(tool, command):
            return (
                f"The {tool.plugin} plugin's monitor runs the command for this session and turns each "
                "line it prints into a notification for as long as the session lasts. Do not "
                "start the command yourself, and end the turn."
            )
        return (
            f'Run the command with Monitor(command=<command>, description="{tool.plugin} {tool.work}", '
            "timeout_ms=1800000), then end the turn. When it expires, leave it stopped and tell the "
            "user in one line that listening has stopped and that they can ask again to resume it. "
            "Nothing is lost while it is stopped: new "
            "comments wait for read_comments(new=True), and a Call agent press waits for the next "
            f"waiter. Stop an active Monitor when the {tool.work} is finished or the user asks. "
            "Start another copy only after the previous process has ended."
        )
    if "codex" in name:
        return (
            'Run <command> --codex "$CODEX_THREAD_ID" with '
            'exec_command(yield_time_ms=1000, sandbox_permissions="require_escalated", '
            f'justification="Allow the {tool.plugin} waiter to deliver events to this Codex thread?"). '
            "Once it is running, end the turn and do not poll. "
            "The waiter uses codex queue to deliver events as labeled user messages, "
            "even while the thread is idle; delivery may take about 10 seconds. "
            "It requires codex queue on PATH and CODEX_THREAD_ID in the agent's shell. "
            "Run one waiter at a time. "
            f"Stop its process when the {tool.work} is finished or the user asks. "
            "Start another copy only after the previous process has ended."
        )
    return (
        "Run the command with your shell tool and read its output. If the tool returns a "
        "running session, read the later output with the tool that reads from that session. "
        "After handling an event, stop the command; listening resumes when the user asks again. "
        "Start another copy only after the previous process has ended."
    )


def listen_from_start(tool: Tool, port: int) -> None:
    """In a session the plugin's monitor serves, listen from the start, unless another session
    listens to the page already."""
    if plugin_monitor.claude_pid() is None:
        return
    deadline = time.monotonic() + MONITOR_START
    while (session := plugin_monitor.monitored_session(tool)) is None:
        if time.monotonic() > deadline:
            return
        time.sleep(1)
    if claim(tool, port, session, if_free=True):
        plugin_monitor.hand_over(tool, waiter.command(tool, port, LISTENER))


def listen_in_background(tool: Tool, port: int) -> None:
    """Listen from the start beside whatever brought this process to the page's server, which
    waiting for the monitor must not hold up. A tool's binding calls it each time the process
    comes to the server afresh: at the session's start when a config is there, at the first
    tool call that finds one written since, as init writes it, and at a tool call that starts
    the server again once the session serving it has gone, taking that session's waiter with
    it."""
    def listen() -> None:
        try:
            listen_from_start(tool, port)
        except (OSError, KeyError, ValueError) as error:
            print(f"{tool.name} did not start listening: {error}", file=sys.stderr)

    threading.Thread(target=listen, daemon=True).start()
