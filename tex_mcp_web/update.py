"""The page's Update: whether a newer release is out, and bringing this install to it.

The review page's server runs inside the agent's MCP process, so an update changes the files
on disk and the running process keeps the code it loaded. The page says so until the agent
connects again, by comparing the version on disk with the one running.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
import re
import shutil
import sys
import time
from pathlib import Path

import psutil

from . import __version__

# Where the newest release is read, at most once in ASK_EVERY seconds by one process, and
# again after RETRY seconds when the answer did not come.
INDEX = "https://pypi.org/pypi/tex-mcp-web/json"
ASK_EVERY = 24 * 3600.0
RETRY = 3600.0
ASK_TIMEOUT = 5.0
# How long one command of an update may take.
TIMEOUT = 600.0

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent

_latest: str | None = None
_ask_again = 0.0


class UpdateError(RuntimeError):
    """An update that did not go through, in words for the page."""


def numbers(version: str) -> tuple[int, ...]:
    """A version as numbers, which compare as versions do: 0.10.0 is after 0.9.1."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def on_disk() -> str:
    """The version of the files on disk, which an update changes while the running process
    keeps the one it loaded."""
    found = re.search(r'^__version__ = "([^"]+)"', (PACKAGE / "__init__.py").read_text(encoding="utf-8"), re.M)
    return found.group(1) if found else __version__


def way() -> str | None:
    """How this install updates itself: "checkout" for a git clone with its own environment,
    as INSTALL.md makes, "package" for the package in a virtual environment that has pip, or
    None, where the tool that installed it updates it."""
    if importlib.util.find_spec("pip") is None or sys.prefix == sys.base_prefix:
        return None
    if (ROOT / ".git").exists():
        return "checkout" if shutil.which("git") else None
    return "package"


async def latest() -> str | None:
    """The newest release's version, or None while it is not known. Never raises."""
    global _latest, _ask_again
    if time.monotonic() < _ask_again:
        return _latest
    _ask_again = time.monotonic() + RETRY
    try:
        import httpx

        async with httpx.AsyncClient(timeout=ASK_TIMEOUT) as client:
            answer = await client.get(INDEX)
            answer.raise_for_status()
            _latest = str(answer.json()["info"]["version"])
            _ask_again = time.monotonic() + ASK_EVERY
    except Exception:
        pass
    return _latest


async def _run(*command: str) -> None:
    """Run one command of an update, away from this process's own input and output, which
    are the agent's connection."""
    process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # pip is Python: in UTF-8 mode it writes Windows' own error messages as UTF-8, which
        # is how what it says is read below, whatever the system's code page.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "PYTHONUTF8": "1"})
    try:
        out, _ = await asyncio.wait_for(process.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise UpdateError(f"{Path(command[0]).name} took longer than {TIMEOUT:.0f} s") from None
    if process.returncode != 0:
        said = out.decode("utf-8", errors="replace").strip().splitlines()[-6:]
        raise UpdateError("\n".join(said) or f"{Path(command[0]).name} failed")


def running_launchers() -> list[str]:
    """The programs of this install that are running: the commands the package installs
    into the environment's Scripts folder, tex-mcp.exe among them. Windows locks a running
    program's file, and an update replaces these files."""
    scripts = (Path(sys.prefix) / "Scripts").resolve()
    ours = {script.name.lower() for script in importlib.metadata.distribution("tex-mcp-web").entry_points
            if script.group == "console_scripts"}
    found = set()
    for process in psutil.process_iter(["exe"]):
        exe = Path(process.info["exe"] or "")
        if exe.stem.lower() in ours and exe.parent.resolve() == scripts:
            found.add(exe.name)
    return sorted(found)


async def run() -> str:
    """Bring this install to the newest release, and give the version then on disk."""
    how = way()
    if how is None:
        raise UpdateError("This install cannot update itself. Ask your agent to update tex-mcp-web.")
    if sys.platform == "win32" and (held := running_launchers()):
        raise UpdateError(f"{' and '.join(held)} {'is' if len(held) == 1 else 'are'} running, and Windows "
                          "does not let a running program be replaced. Ask your agent to update "
                          "tex-mcp-web: it registers the server so that later updates work from this page.")
    pip = (sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check")
    if how == "checkout":
        await _run(shutil.which("git") or "git", "-C", str(ROOT), "pull", "-q", "--ff-only")
        await _run(*pip, "-e", str(ROOT))
    else:
        await _run(*pip, "-U", "tex-mcp-web")
    return on_disk()
