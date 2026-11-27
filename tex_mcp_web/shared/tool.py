"""What sets one review tool apart where it meets the modules it shares with the others."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Tool:
    """A review tool, as its waiter, its plugin's monitor, their hand-over, listening for
    Call agent and the page's Update know it.

    name: the tool's name, as in tex-mcp-web, which is also its distribution's; its folder
        under ~/.cache bears it.
    package: its Python package, which runs ``python -m <package>.waiter`` and
        ``python -m <package>.monitor``.
    server: what the waiter calls the page's server in [gone] and [back], as review server.
    event: the label a line carries into a Codex thread, as [TeX review event].
    plugin: the name of its plugin and MCP server, as tex-mcp.
    page: what the agent is told the page is called, as review page.
    person: who presses Call agent on the page, as reviewer.
    work: what the person and the agent do on the page, as review.
    press: the label of the line a press prints, as [review].
    headers: what a program on this machine sends the page, read at each request: the page
        token, for a page that asks for one.
    pypi: its project on PyPI, where its releases are, or None for a tool installed only
        from a git clone.
    extras: the extras pip installs it with, as [mcp], or "".
    base: the path the page's server keeps its call routes under, as /reader for a tool
        whose calls belong to a page mounted there, or "" for the server's root.
    monitors: the folder where the tool's servers hand a session's waiter to the plugin's
        monitor, ~/.cache/<name>/monitor.
    """

    name: str
    package: str
    server: str
    event: str
    plugin: str
    page: str
    person: str
    work: str
    press: str
    headers: Callable[[], dict[str, str]] = dict
    pypi: str | None = None
    extras: str = ""
    base: str = ""
    monitors: Path = field(init=False)

    def __post_init__(self) -> None:
        self.monitors = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / self.name / "monitor"
