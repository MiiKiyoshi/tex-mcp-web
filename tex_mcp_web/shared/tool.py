"""What sets one review tool apart where it meets the modules it shares with the others."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Tool:
    """A review tool, as its waiter, its plugin's monitor and their hand-over know it.

    name: the tool's name, as in tex-mcp-web; its folder under ~/.cache bears it.
    package: its Python package, which runs ``python -m <package>.waiter`` and
        ``python -m <package>.monitor``.
    server: what the waiter calls the page's server in [gone] and [back], as review server.
    event: the label a line carries into a Codex thread, as [TeX review event].
    headers: what a program on this machine sends the page, read at each request: the page
        token, for a page that asks for one.
    monitors: the folder where the tool's servers hand a session's waiter to the plugin's
        monitor, ~/.cache/<name>/monitor.
    """

    name: str
    package: str
    server: str
    event: str
    headers: Callable[[], dict[str, str]] = dict
    monitors: Path = field(init=False)

    def __post_init__(self) -> None:
        self.monitors = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / self.name / "monitor"
