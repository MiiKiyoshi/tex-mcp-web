"""The page's Update: whether something newer than this install is out, and bringing the
install to it.

A tool's page server runs inside the agent's MCP process, so an update changes the files on
disk and the running process keeps the code it loaded. The page says so until the agent
connects again, by comparing what is on disk with what runs.

What is newer depends on where the tool is published. A tool with a project on PyPI
(Tool.pypi) is behind when PyPI's newest release has a higher version. A tool without one is
installed from a git clone, whose version stays the same from one commit to the next: what runs
and what is on disk are its version with the clone's commit, as 0.1.0+27a1a82, and the clone is
behind when its upstream, fetched, has commits it lacks.

An install updates itself when it is a git clone with its own virtual environment, as each
tool's INSTALL.md makes it, by git pull --ff-only and pip install -e of the clone with the
tool's extras, and, for a tool on PyPI, when it is the package in a virtual environment, by
pip install -U.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import psutil
from aiohttp import web

from .tool import Tool


class UpdateError(RuntimeError):
    """An update that did not go through, in words for the page."""


class Update:
    """The Update of the install this module is part of: *tool*, which loaded *version*.
    *check()* says, each time the page asks, whether the configuration lets the server ask
    what is newer. add_routes() serves the page GET /update and POST /update."""

    # What is newer is asked at most once in ASK_EVERY seconds by one process, and again after
    # RETRY seconds when no answer came: from PyPI within ASK_TIMEOUT seconds, and by a fetch of
    # the clone's upstream within FETCH_TIMEOUT.
    ASK_EVERY = 24 * 3600.0
    RETRY = 3600.0
    ASK_TIMEOUT = 5.0
    FETCH_TIMEOUT = 30.0
    # How long one command of an update may take.
    TIMEOUT = 600.0
    WINDOWS = sys.platform == "win32"

    def __init__(self, tool: Tool, version: str, check: Callable[[], bool]) -> None:
        self.tool, self.check = tool, check
        # The package this copy of the module sits in, and the folder that holds it, which is
        # the clone in a git clone.
        self.package = Path(__file__).resolve().parent.parent
        self.root = self.package.parent
        # The clone's commit as last read, for a tool not on PyPI installed from a git clone,
        # and what runs, read as the server starts.
        self._commit = self._read_commit()
        self.version = self._label(version)
        self._news: tuple[str | None, int | None] = (None, None)
        self._ask_again = 0.0
        self._updating = asyncio.Lock()

    def add_routes(self, app: web.Application) -> None:
        app.router.add_get("/update", self._handle_state)
        app.router.add_post("/update", self._handle_update)

    def on_disk(self) -> str:
        """What is on disk, which an update changes while the running process keeps what it
        loaded: the version the package's __init__.py names, with the clone's commit for a
        tool not on PyPI."""
        found = re.search(r'^__version__ = "([^"]+)"', (self.package / "__init__.py").read_text(encoding="utf-8"), re.M)
        if found is None:
            return self.version
        if self._commit is not None:
            # A commit git cannot read at the moment is taken as the last one read: a passing
            # failure of git is no update.
            self._commit = self._read_commit() or self._commit
        return self._label(found.group(1))

    def way(self) -> str | None:
        """How this install updates itself: "checkout" for a git clone with its own
        environment, as INSTALL.md makes it, "package" for the package from PyPI in a virtual
        environment that has pip, or None, where the tool that installed it updates it."""
        if importlib.util.find_spec("pip") is None or sys.prefix == sys.base_prefix:
            return None
        if (self.root / ".git").exists():
            return "checkout" if shutil.which("git") else None
        return "package" if self.tool.pypi is not None else None

    async def news(self) -> tuple[str | None, int | None]:
        """What is newer than this install: the newest release's version, for a tool on PyPI,
        and how many commits the clone's upstream has that the clone lacks, for one that is
        not, each None while it is not known. Never raises."""
        if time.monotonic() < self._ask_again:
            return self._news
        self._ask_again = time.monotonic() + self.RETRY
        try:
            self._news = (await self._latest(), None) if self.tool.pypi is not None else (None, await self._ahead())
            self._ask_again = time.monotonic() + self.ASK_EVERY
        except Exception:
            pass
        return self._news

    async def _latest(self) -> str:
        # trust_env: through the proxy the environment names, as pip goes.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.ASK_TIMEOUT), trust_env=True) as session:
            async with session.get(f"https://pypi.org/pypi/{self.tool.pypi}/json") as answer:
                answer.raise_for_status()
                return str((await answer.json())["info"]["version"])

    async def _ahead(self) -> int | None:
        git = self._git()
        if git is None:
            return None
        await self._run(git, "-C", str(self.root), "fetch", "-q", timeout=self.FETCH_TIMEOUT)
        # The count is the last line, after any warning git wrote first.
        counted = await self._run(git, "-C", str(self.root), "rev-list", "--count", "HEAD..@{upstream}")
        return int(counted.splitlines()[-1])

    def _git(self) -> str | None:
        """git, for an install that is a git clone and a system that has it."""
        return shutil.which("git") if (self.root / ".git").exists() else None

    def _label(self, version: str) -> str:
        """*version* as the page names what runs and what is on disk: with the clone's commit,
        for a tool not on PyPI installed from a git clone."""
        return f"{version}+{self._commit}" if self._commit else version

    def _read_commit(self) -> str | None:
        """The clone's commit, for a tool not on PyPI installed from a git clone, or None."""
        git = self._git()
        if self.tool.pypi is not None or git is None:
            return None
        try:
            return subprocess.run([git, "-C", str(self.root), "rev-parse", "--short", "HEAD"],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  timeout=self.ASK_TIMEOUT, check=True).stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None

    async def _run(self, *command: str, timeout: float | None = None) -> str:
        """Run one command of an update, away from this process's own input and output, which
        are the agent's connection, and from its terminal, where git asking for a password
        would wait unseen. Its output is what it printed."""
        process = await asyncio.create_subprocess_exec(
            *command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True,
            # pip is Python: in UTF-8 mode it writes Windows' own error messages as UTF-8, which
            # is how what it says is read below, whatever the system's code page.
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", "PYTHONUTF8": "1"})
        try:
            out, _ = await asyncio.wait_for(process.communicate(), timeout or self.TIMEOUT)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise UpdateError(f"{Path(command[0]).name} took longer than {timeout or self.TIMEOUT:.0f} s") from None
        said = out.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise UpdateError("\n".join(said.splitlines()[-6:]) or f"{Path(command[0]).name} failed")
        return said

    def running_launchers(self) -> list[str]:
        """The programs of this install that are running: the commands the package installs
        into the environment's Scripts folder, the tool's MCP server among them. Windows locks
        a running program's file, and an update replaces these files."""
        scripts = (Path(sys.prefix) / "Scripts").resolve()
        ours = {script.name.lower() for script in importlib.metadata.distribution(self.tool.name).entry_points
                if script.group == "console_scripts"}
        found = set()
        for process in psutil.process_iter(["exe"]):
            exe = Path(process.info["exe"] or "")
            if exe.stem.lower() in ours and exe.parent.resolve() == scripts:
                found.add(exe.name)
        return sorted(found)

    async def run(self) -> str:
        """Bring this install to what is newer, and give what is then on disk."""
        how = self.way()
        name = self.tool.name
        if how is None:
            raise UpdateError(f"This install cannot update itself. Ask your agent to update {name}.")
        if self.WINDOWS and (held := self.running_launchers()):
            raise UpdateError(f"{' and '.join(held)} {'is' if len(held) == 1 else 'are'} running, and Windows "
                              f"does not let a running program be replaced. Ask your agent to update {name}: "
                              "it registers the server so that later updates work from this page.")
        pip = (sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check")
        if how == "checkout":
            await self._run(shutil.which("git") or "git", "-C", str(self.root), "pull", "-q", "--ff-only")
            await self._run(*pip, "-e", f"{self.root}{self.tool.extras}")
        else:
            await self._run(*pip, "-U", f"{self.tool.pypi}{self.tool.extras}")
        return self.on_disk()

    async def _handle_state(self, request: web.Request) -> web.Response:
        """What the page's menu offers: what runs, what is on disk, which differs once an
        update is installed and until the agent connects again, what is newer, unless the
        configuration says not to ask, and whether this install updates itself."""
        latest, ahead = await self.news() if self.check() else (None, None)
        return web.json_response({"version": self.version, "on_disk": self.on_disk(), "latest": latest,
                                  "ahead": ahead, "self_update": self.way() is not None})

    async def _handle_update(self, request: web.Request) -> web.Response:
        """Bring this install up to date. The running server keeps its code, so the page then
        asks the reviewer to have the agent connect again. A POST without a body needs no leave
        from the browser, so another site's page could send one: the browser marks it with that
        page's origin, and it is refused. A program on this machine sends no origin."""
        origin = request.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc != request.host:
            return web.json_response({"error": "An update starts from the review page alone."}, status=403)
        if self._updating.locked():
            return web.json_response({"error": "An update is already running."}, status=409)
        async with self._updating:
            try:
                return web.json_response({"on_disk": await self.run()})
            except UpdateError as error:
                return web.json_response({"error": str(error)}, status=500)
