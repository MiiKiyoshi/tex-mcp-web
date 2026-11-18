"""The page's Update: which installs update themselves, and by which commands."""

import sys

import pytest

from tex_mcp_web import __version__, update


def test_versions_compare_as_numbers_and_the_one_on_disk_is_read_from_the_file(tmp_path, monkeypatch):
    assert update.numbers("0.10.0") > update.numbers("0.9.1") > update.numbers("0.9")
    assert update.on_disk() == __version__
    # An update rewrites the file, and the running process keeps the version it loaded.
    (tmp_path / "__init__.py").write_text('"""A package."""\n\n__version__ = "9.9.9"\n', encoding="utf-8", newline="")
    monkeypatch.setattr(update, "PACKAGE", tmp_path)
    assert update.on_disk() == "9.9.9" and __version__ != "9.9.9"


def test_only_an_install_with_its_own_environment_updates_itself(tmp_path, monkeypatch):
    """A git clone with its own virtual environment updates by git pull, the package in a
    virtual environment by pip, and an install with neither says to ask the agent."""
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "base_prefix", sys.prefix + "-base")
    assert update.way() == "package"
    (tmp_path / ".git").mkdir()
    assert update.way() == "checkout"
    monkeypatch.setattr(update.shutil, "which", lambda name: None)
    assert update.way() is None
    # Outside a virtual environment nothing is installed for the reviewer.
    monkeypatch.undo()
    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    assert update.way() is None


async def test_an_update_runs_its_commands_apart_from_the_agents_connection(tmp_path, monkeypatch):
    """A clone is pulled, fast-forward only, and installed again for a changed dependency,
    and the package is upgraded by pip. A command's output is read, never left on this
    process's own stdout, which is the agent's connection, and a failed one is the error."""
    ran = []

    async def note(*command):
        ran.append(command)

    monkeypatch.setattr(update, "ROOT", tmp_path)
    monkeypatch.setattr(update, "_run", note)
    monkeypatch.setattr(update, "way", lambda: "checkout")
    monkeypatch.setattr(update.shutil, "which", lambda name: "/usr/bin/git")
    pip = (sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check")
    assert await update.run() == __version__
    assert ran == [("/usr/bin/git", "-C", str(tmp_path), "pull", "-q", "--ff-only"), (*pip, "-e", str(tmp_path))]
    ran.clear()
    monkeypatch.setattr(update, "way", lambda: "package")
    await update.run()
    assert ran == [(*pip, "-U", "tex-mcp-web")]
    monkeypatch.setattr(update, "way", lambda: None)
    with pytest.raises(update.UpdateError, match="Ask your agent to update tex-mcp-web"):
        await update.run()
    monkeypatch.undo()
    # The command itself: its words come back in the error, and nothing reaches stdout.
    with pytest.raises(update.UpdateError, match="no such thing"):
        await update._run(sys.executable, "-c", "import sys; print('no such thing'); sys.exit(3)")
    await update._run(sys.executable, "-c", "print('quiet')")


def test_on_windows_an_update_is_refused_while_the_installs_own_program_runs(tmp_path, monkeypatch):
    """Windows locks a running program's file, and an update replaces tex-mcp.exe. A server
    started from it is told so before anything is changed. One started as a Python module,
    as INSTALL.md registers it, updates."""
    import asyncio
    from types import SimpleNamespace

    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    running = [SimpleNamespace(info={"exe": str(scripts / "tex-mcp.exe")}),
               SimpleNamespace(info={"exe": str(tmp_path / "elsewhere" / "tex-mcp.exe")}),
               SimpleNamespace(info={"exe": str(scripts / "python.exe")}),
               SimpleNamespace(info={"exe": None})]
    monkeypatch.setattr(update.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(update.psutil, "process_iter", lambda attrs: running)
    assert update.running_launchers() == ["tex-mcp.exe"]

    ran = []

    async def record(*command):
        ran.append(command)

    monkeypatch.setattr(update, "way", lambda: "checkout")
    monkeypatch.setattr(update, "_run", record)
    monkeypatch.setattr(update.sys, "platform", "win32")
    with pytest.raises(update.UpdateError, match="tex-mcp.exe is running"):
        asyncio.run(update.run())
    assert ran == []
    del running[0]
    asyncio.run(update.run())
    assert len(ran) == 2
