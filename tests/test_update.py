"""The page's Update (shared/update.py): which installs update themselves, by which commands,
and what is newer, for a tool on PyPI and for one installed from a git clone."""

import asyncio
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tex_mcp_web import __version__
from tex_mcp_web.shared.tool import Tool
from tex_mcp_web.shared.update import Update, UpdateError
from tex_mcp_web.tool import TOOL

# The real ones, which the no_update fixture replaces for every other test.
NEWS = Update.news
RUN = Update._run
CLONED = Tool(name="tex-mcp-web", package="tex_mcp_web", server="review server", event="[event]", extras="[mcp]")


def commit(folder) -> str:
    """A git clone in *folder* with one commit, made on this machine: its short hash."""
    git = ("git", "-C", str(folder), "-c", "user.name=Ann", "-c", "user.email=ann@example.com")
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "first"], check=True)
    return subprocess.run([*git, "rev-parse", "--short", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_what_runs_and_what_is_on_disk_are_read_apart(tmp_path, monkeypatch):
    """An update rewrites the package's files, and the running process keeps the version it
    loaded. A tool without a PyPI project tells them apart by the clone's commit too, since
    its version stays the same from one commit to the next."""
    update = Update(TOOL, __version__, lambda: True)
    assert update.version == update.on_disk() == __version__
    (tmp_path / "__init__.py").write_text('"""A package."""\n\n__version__ = "9.9.9"\n', encoding="utf-8", newline="")
    update.package = tmp_path
    assert update.on_disk() == "9.9.9" and update.version == __version__
    # A clone of a tool not on PyPI: the version with the commit, which a pull changes.
    clone = tmp_path / "clone"
    (clone / "pkg").mkdir(parents=True)
    first = commit(clone)
    (clone / "pkg" / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
    monkeypatch.setattr(Update, "_git", lambda self: "git")
    update = Update(CLONED, "0.1.0", lambda: True)
    update.root, update.package = clone, clone / "pkg"
    assert update.on_disk() == f"0.1.0+{first}"
    second = commit(clone)
    assert update.on_disk() == f"0.1.0+{second}" != f"0.1.0+{first}"
    # A commit git cannot read at the moment is taken as the last one read, so a passing
    # failure of git does not read as an update; without git from the start, the version alone.
    monkeypatch.setattr(Update, "_git", lambda self: None)
    assert update.on_disk() == f"0.1.0+{second}"
    assert Update(CLONED, "0.1.0", lambda: True).version == "0.1.0"


def test_only_an_install_with_its_own_environment_updates_itself(tmp_path, monkeypatch):
    """A git clone with its own virtual environment updates by git pull, the package from
    PyPI in a virtual environment by pip, and an install with neither says to ask the agent.
    A tool not on PyPI has no package to upgrade."""
    update = Update(TOOL, __version__, lambda: True)
    update.root = tmp_path
    monkeypatch.setattr(sys, "base_prefix", sys.prefix + "-base")
    assert update.way() == "package"
    cloned = Update(CLONED, "0.1.0", lambda: True)
    cloned.root = tmp_path
    assert cloned.way() is None
    (tmp_path / ".git").mkdir()
    assert update.way() == cloned.way() == "checkout"
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert update.way() is None
    # Outside a virtual environment nothing is installed for the reviewer.
    monkeypatch.undo()
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    assert update.way() is None


async def test_an_update_runs_its_commands_apart_from_the_agents_connection(tmp_path, monkeypatch):
    """A clone is pulled, fast-forward only, and installed again with the tool's extras for a
    changed dependency, and the package is upgraded by pip. A command's output is read, never
    left on this process's own stdout, which is the agent's connection, and a failed one is
    the error."""
    ran = []

    async def note(self, *command, timeout=None):
        ran.append(command)
        return ""

    monkeypatch.setattr(Update, "_run", note)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/git")
    pip = (sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check")
    for tool, installed in ((TOOL, str(tmp_path)), (CLONED, f"{tmp_path}[mcp]")):
        update = Update(tool, __version__, lambda: True)
        update.root = tmp_path
        monkeypatch.setattr(update, "way", lambda: "checkout")
        assert await update.run() == update.on_disk()
        assert ran == [("/usr/bin/git", "-C", str(tmp_path), "pull", "-q", "--ff-only"), (*pip, "-e", installed)]
        ran.clear()
    update = Update(TOOL, __version__, lambda: True)
    monkeypatch.setattr(update, "way", lambda: "package")
    await update.run()
    assert ran == [(*pip, "-U", "tex-mcp-web")]
    monkeypatch.setattr(update, "way", lambda: None)
    with pytest.raises(UpdateError, match="Ask your agent to update tex-mcp-web"):
        await update.run()
    # The command itself: its words come back in the error, and nothing reaches stdout.
    monkeypatch.setattr(Update, "_run", RUN)
    with pytest.raises(UpdateError, match="no such thing"):
        await update._run(sys.executable, "-c", "import sys; print('no such thing'); sys.exit(3)")
    assert await update._run(sys.executable, "-c", "print('quiet')") == "quiet"


async def test_what_is_newer_is_asked_once_a_day_and_again_after_a_failure(tmp_path, monkeypatch):
    """A tool on PyPI asks it for the newest release. A tool not on PyPI fetches its clone's
    upstream and counts the commits the clone lacks; a folder that is no clone has none to
    count. An answer stands for a day, and a failure is asked again an hour later, keeping
    what was known."""
    monkeypatch.setattr(Update, "news", NEWS)
    asked = []

    async def release(self):
        asked.append("pypi")
        return "99.0.0"

    monkeypatch.setattr(Update, "_latest", release)
    update = Update(TOOL, __version__, lambda: True)
    assert await update.news() == ("99.0.0", None) and await update.news() == ("99.0.0", None)
    assert asked == ["pypi"]

    ran = []

    async def git(self, *command, timeout=None):
        ran.append(command[3:])
        if ran[-1][0] == "fetch" and len(ran) > 2:
            raise UpdateError("fatal: unable to access the upstream")
        return "warning: a word from git first\n3" if ran[-1][0] == "rev-list" else ""

    monkeypatch.setattr(Update, "_run", git)
    monkeypatch.setattr(Update, "_git", lambda self: "git" if (self.root / ".git").exists() else None)
    cloned = Update(CLONED, "0.1.0", lambda: True)
    cloned.root = tmp_path
    assert await cloned.news() == (None, None) and ran == []
    cloned._ask_again = 0.0
    (tmp_path / ".git").mkdir()
    assert await cloned.news() == (None, 3)
    assert ran == [("fetch", "-q"), ("rev-list", "--count", "HEAD..@{upstream}")]
    assert await cloned.news() == (None, 3) and len(ran) == 2
    # A day later the fetch fails: what was known stands, and it is asked again in an hour.
    cloned._ask_again = 0.0
    assert await cloned.news() == (None, 3) and len(ran) == 3
    assert Update.RETRY - 60 < cloned._ask_again - time.monotonic() <= Update.RETRY
    assert await cloned.news() == (None, 3) and len(ran) == 3


def test_on_windows_an_update_is_refused_while_the_installs_own_program_runs(tmp_path, monkeypatch):
    """Windows locks a running program's file, and an update replaces tex-mcp.exe. A server
    started from it is told so before anything is changed. One started as a Python module,
    as INSTALL.md registers it, updates."""
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    running = [SimpleNamespace(info={"exe": str(scripts / "tex-mcp.exe")}),
               SimpleNamespace(info={"exe": str(scripts / "tex-web.exe")}),
               SimpleNamespace(info={"exe": str(tmp_path / "elsewhere" / "tex-mcp.exe")}),
               SimpleNamespace(info={"exe": str(scripts / "python.exe")}),
               SimpleNamespace(info={"exe": None})]
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr("psutil.process_iter", lambda attrs: running)
    update = Update(TOOL, __version__, lambda: True)
    # Every command the package installs counts, whatever its name.
    assert update.running_launchers() == ["tex-mcp.exe", "tex-web.exe"]

    ran = []

    async def record(self, *command, timeout=None):
        ran.append(command)
        return ""

    monkeypatch.setattr(update, "way", lambda: "checkout")
    monkeypatch.setattr(Update, "_run", record)
    monkeypatch.setattr(update, "WINDOWS", True)
    with pytest.raises(UpdateError, match="tex-mcp.exe and tex-web.exe are running"):
        asyncio.run(update.run())
    assert ran == []
    del running[:2]
    asyncio.run(update.run())
    assert len(ran) == 2
