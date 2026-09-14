"""Only the directories the paper's sources live in are watched, and a start that
times out or fails cleans up after itself."""

import asyncio
import fcntl
import socket
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from tex_mcp_web.compiler import source_dependencies
from tex_mcp_web.config import load_config
from tex_mcp_web.mcp_client import SharedProjectServer
from tex_mcp_web.server import TexMcpWebServer
from tex_mcp_web.watcher import Watcher

FDB = '''# Fdb version 4
["bibtex main"] 1789376452.68888 "main.aux" "main.bbl" "main" 1789376453.80116 0
  "./bib/refs.bib" 1789376450.55846 60 c2f2e72936d05cf3ccd26b1ec791ec54 ""
  "/home/user/tools/texlive/2025/texmf-dist/bibtex/bst/base/plain.bst" 1 6 d ""
  "main.aux" 1789376453.27445 96 4242e664078ca8d11680798c00eab95c "pdflatex"
  (generated)
  "main.bbl"
  "main.blg"
["pdflatex"] 1789376452.82005 "main.tex" "main.pdf" "main" 1789376453.80149 0
  "/home/user/tools/texlive/2025/texmf-dist/tex/latex/base/article.cls" 1 2 a ""
  "fig/a.png" 1789376450.62646 69 3acca26d4f9d111694d7dbda2d1e6a40 ""
  "fig/with \\"quote\\" and space.png" 1789376450.62646 69 3acca26d4f9d111694d7dbda2d1e6a40 ""
  "main.aux" 1789376453.27445 96 4242e664078ca8d11680798c00eab95c "pdflatex"
  "main.bbl" 1789376452.81045 104 1bb5e841e4feab91efc1da556ffd2a0a "bibtex main"
  "main.tex" 1789376450.55846 177 56c168c2dc380d864951e09fa884a867 ""
  "sec/intro.tex" 1789376450.55846 15 3e96b1ee6c7d92a3a45fa8e956a61f2f ""
  "../outside/shared.tex" 1789376450.55846 15 3e96b1ee6c7d92a3a45fa8e956a61f2f ""
  (generated)
  "main.aux"
  "main.pdf"
'''


def test_fdb_sources_are_project_local_and_not_generated(tmp_path: Path) -> None:
    (tmp_path / "main.fdb_latexmk").write_text(FDB, encoding="utf-8")
    found = source_dependencies(tmp_path / "main.tex", tmp_path)
    assert [path.relative_to(tmp_path).as_posix() for path in found] == [
        "bib/refs.bib", "fig/a.png", 'fig/with "quote" and space.png', "main.tex", "sec/intro.tex",
    ]
    assert source_dependencies(tmp_path / "other.tex", tmp_path) == []


def _watches(watcher: Watcher) -> set[tuple[str, bool]]:
    return {(watch.path, watch.is_recursive) for watch in watcher._observer._watches}


def test_only_source_roots_are_watched_and_the_rest_is_never_walked(tmp_path: Path) -> None:
    (tmp_path / "tex").mkdir()
    (tmp_path / "fig").mkdir()
    for number in range(300):
        (tmp_path / "private" / f"run{number}" / "out").mkdir(parents=True)
    watcher = Watcher(tmp_path, ["*.tex"], [], AsyncMock(),
                      roots=[tmp_path, tmp_path / "tex", tmp_path / "fig", tmp_path / "fig",
                             tmp_path.parent / "elsewhere"])
    assert watcher.roots == [tmp_path / "fig", tmp_path / "tex"]  # the project itself is flat only
    loop = asyncio.new_event_loop()
    watcher.start(loop)
    try:
        assert _watches(watcher) == {(str(tmp_path), False), (str(tmp_path / "tex"), True),
                                     (str(tmp_path / "fig"), True)}
        # A successful compile that reads a new directory and no longer reads fig.
        (tmp_path / "bib").mkdir()
        added, dropped = watcher.set_roots([tmp_path / "tex", tmp_path / "bib", tmp_path / "tex" / "nested"])
        assert (added, dropped) == ([tmp_path / "bib"], [tmp_path / "fig"])
        assert _watches(watcher) == {(str(tmp_path), False), (str(tmp_path / "tex"), True),
                                     (str(tmp_path / "bib"), True)}
        assert watcher.set_roots([tmp_path / "tex", tmp_path / "bib"]) == ([], [])
    finally:
        watcher.stop()
        loop.close()


def _project(tmp_path: Path, main: str = "main.tex") -> Path:
    (tmp_path / "tex").mkdir(exist_ok=True)
    (tmp_path / "fig").mkdir(exist_ok=True)
    (tmp_path / "private" / "dump").mkdir(parents=True, exist_ok=True)
    (tmp_path / main).write_text("\\documentclass{article}\\begin{document}\\input{tex/body}\\end{document}",
                                 encoding="utf-8")
    (tmp_path / "tex" / "body.tex").write_text("Body.", encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
    path = tmp_path / ".tex-mcp-web.yaml"
    path.write_text(yaml.safe_dump({"main": main, "port": port, "auto_compile": False}), encoding="utf-8")
    return path


def test_server_roots_come_from_reachable_tex_and_the_last_run_record(tmp_path: Path) -> None:
    config = load_config(_project(tmp_path))
    server = TexMcpWebServer(config)
    # Before any run: the main file's directory is the project (flat), body.tex adds tex/.
    assert Watcher(tmp_path, [], [], AsyncMock(), roots=server.source_roots()).roots == [tmp_path / "tex"]
    (tmp_path / "main.fdb_latexmk").write_text(
        '["pdflatex"] 1 "main.tex" "main.pdf" "main" 1 0\n'
        '  "fig/a.png" 1 1 a ""\n  "main.tex" 1 1 a ""\n  "tex/body.tex" 1 1 a ""\n', encoding="utf-8")
    assert Watcher(tmp_path, [], [], AsyncMock(), roots=server.source_roots()).roots == [
        tmp_path / "fig", tmp_path / "tex"]


def _server_threads() -> int:
    return sum(1 for thread in threading.enumerate() if thread.name == "tex-mcp-web")


def _lock_is_free(watch_dir: Path) -> bool:
    with (watch_dir / ".tex-mcp-web" / "server.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True


def _wait(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_a_slow_start_is_cancelled_after_setup_returns_and_cleans_up(tmp_path: Path, monkeypatch) -> None:
    """setup() runs synchronously on the loop, so the cancel cannot land before it
    returns; when it does, the thread cleans the server up and frees the lock, and a
    retry made meanwhile adds no thread."""
    config = load_config(_project(tmp_path))
    shared = SharedProjectServer(config)
    monkeypatch.setattr(SharedProjectServer, "START_TIMEOUT", 0.3)
    original = TexMcpWebServer.setup
    servers: list[TexMcpWebServer] = []

    async def slow_setup(self, port):
        servers.append(self)
        time.sleep(0.9)  # a cold walk of a big tree, on the loop thread
        await original(self, port)

    monkeypatch.setattr(TexMcpWebServer, "setup", slow_setup)
    before = _server_threads()
    try:
        with pytest.raises(RuntimeError, match="did not start within"):
            shared.ensure()
        assert _server_threads() == before + 1 and not shared.ready.is_set()
        with pytest.raises(RuntimeError, match="still starting"):
            shared.ensure()
        assert _server_threads() == before + 1
        _wait(lambda: shared.thread is not None and not shared.thread.is_alive())
        assert len(servers) == 1 and servers[0].watcher is None
        assert _lock_is_free(tmp_path) and _server_threads() == before

        monkeypatch.setattr(TexMcpWebServer, "setup", original)
        monkeypatch.setattr(SharedProjectServer, "START_TIMEOUT", 10)
        shared.ensure()
        assert shared._remote_identity() == str(tmp_path.resolve())
    finally:
        shared.stop()
    assert _server_threads() == before and _lock_is_free(tmp_path)


def test_a_failed_start_names_its_cause_and_frees_the_lock(tmp_path: Path, monkeypatch) -> None:
    config = load_config(_project(tmp_path))
    shared = SharedProjectServer(config)

    async def broken_setup(self, port):
        raise RuntimeError("the inotify watch limit is used up")

    monkeypatch.setattr(TexMcpWebServer, "setup", broken_setup)
    before = _server_threads()
    with pytest.raises(RuntimeError, match="failed to start: the inotify watch limit is used up"):
        shared.ensure()
    assert _server_threads() == before and _lock_is_free(tmp_path)
    assert shared._remote_identity() is None
