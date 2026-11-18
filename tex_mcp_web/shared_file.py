"""Replace a file that other processes read, and read one that other processes replace.

The page's server, the agent's MCP process, the plugin's monitor and the CLI share files
such as the comment store, each written whole beside its place and then moved into it.
Windows refuses to move a file over one that any process has open, and refuses to open a
file at the instant another is moved over it; both raise PermissionError. Here each is
tried again for a moment on Windows. On other systems neither happens, and a
PermissionError is a real lack of permission, raised at once.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

WINDOWS = os.name == "nt"

# A reader holds a file only while it reads it, and a move takes an instant, so a refusal
# passes within milliseconds. A try and up to 50 more 10 ms apart, half a second at most,
# outlast it: on Windows, one process adding comments in a loop while two others listed
# them met no error in three runs of 10 seconds this way, and 584 in 8 seconds without it.
RETRIES = 50
PAUSE = 0.01


def _patiently(action):
    """What *action* returns, tried again while Windows refuses it with PermissionError."""
    for _ in range(RETRIES if WINDOWS else 0):
        try:
            return action()
        except PermissionError:
            time.sleep(PAUSE)
    return action()


def replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """Move *source* over *target*, as os.replace does, once no reader holds *target* open."""
    _patiently(lambda: os.replace(source, target))


def read_text(path: str | os.PathLike[str], encoding: str = "utf-8", errors: str | None = None) -> str:
    """The file's text, as Path.read_text gives it, once no move over the file is under way."""
    return _patiently(lambda: Path(path).read_text(encoding=encoding, errors=errors))
