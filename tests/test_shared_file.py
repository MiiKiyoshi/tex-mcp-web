"""Files one process replaces while another reads them, as Windows treats them: it refuses
to replace a file another process has open, and to open one at the instant it is replaced."""

import json
import os
import pathlib
import subprocess
import sys
from pathlib import Path

import pytest

from tex_mcp_web import mcp_server, overleaf, shared_file
from tex_mcp_web.comments import CommentStore, PaperAnchor

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def windows(monkeypatch):
    """Tries made as on Windows, without the pause between them."""
    monkeypatch.setattr(shared_file, "WINDOWS", True)
    monkeypatch.setattr(shared_file, "PAUSE", 0)


def refuse_replacing(monkeypatch, name: str, times: int) -> list[str]:
    """os.replace refuses to put a file in place of one called *name*, *times* times, as Windows
    does while a reader has it open. The list given back holds each try's target."""
    real, tries = os.replace, []

    def replace(source, target):
        if Path(target).name == name:
            tries.append(name)
            if len(tries) <= times:
                raise PermissionError(5, "Access is denied", str(target))
        real(source, target)

    monkeypatch.setattr(os, "replace", replace)
    return tries


def refuse_reading(monkeypatch, name: str, times: int) -> list[str]:
    """Reading a file called *name* is refused *times* times, as Windows refuses it at the
    instant the file is replaced."""
    real, tries = pathlib.Path.read_text, []

    def read_text(self, *args, **kwargs):
        if self.name == name:
            tries.append(name)
            if len(tries) <= times:
                raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    return tries


def test_on_windows_a_refused_replace_is_tried_again(tmp_path, monkeypatch, windows):
    (tmp_path / "new").write_text("new", encoding="utf-8")
    (tmp_path / "file").write_text("old", encoding="utf-8")
    tries = refuse_replacing(monkeypatch, "file", 2)

    shared_file.replace(tmp_path / "new", tmp_path / "file")

    assert len(tries) == 3 and (tmp_path / "file").read_text(encoding="utf-8") == "new"


def test_on_windows_a_refused_read_is_tried_again(tmp_path, monkeypatch, windows):
    (tmp_path / "file").write_text("text", encoding="utf-8")
    tries = refuse_reading(monkeypatch, "file", 2)

    assert shared_file.read_text(tmp_path / "file") == "text" and len(tries) == 3


def test_on_windows_a_refusal_that_lasts_is_raised(tmp_path, monkeypatch, windows):
    (tmp_path / "new").write_text("new", encoding="utf-8")
    (tmp_path / "file").write_text("old", encoding="utf-8")
    replaced = refuse_replacing(monkeypatch, "file", 10_000)
    read = refuse_reading(monkeypatch, "file", 10_000)

    with pytest.raises(PermissionError):
        shared_file.replace(tmp_path / "new", tmp_path / "file")
    with pytest.raises(PermissionError):
        shared_file.read_text(tmp_path / "file")
    assert len(replaced) == len(read) == shared_file.RETRIES + 1


def test_elsewhere_a_permission_error_is_raised_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_file, "WINDOWS", False)
    (tmp_path / "new").write_text("new", encoding="utf-8")
    (tmp_path / "file").write_text("old", encoding="utf-8")
    replaced = refuse_replacing(monkeypatch, "file", 1)
    read = refuse_reading(monkeypatch, "file", 1)

    with pytest.raises(PermissionError):
        shared_file.replace(tmp_path / "new", tmp_path / "file")
    with pytest.raises(PermissionError):
        shared_file.read_text(tmp_path / "file")
    assert len(replaced) == len(read) == 1


def test_on_windows_the_comment_store_waits_out_a_reader_and_a_writer(tmp_path, monkeypatch, windows):
    """The page lists comments while the agent's process writes one: the write waits for the
    reader to let go of the file, and the read for the new file to be in place."""
    path = tmp_path / "comments.json"
    writer, reader = CommentStore(path), CommentStore(path)
    replaced = refuse_replacing(monkeypatch, "comments.json", 2)

    writer.add(PaperAnchor(), "Written while the page reads.")
    read = refuse_reading(monkeypatch, "comments.json", 2)

    assert [c.text for c in reader.list()] == ["Written while the page reads."]
    assert len(replaced) == 3 and len(read) == 3


def test_the_cursor_and_the_overleaf_ledger_are_put_in_place_whole(tmp_path, monkeypatch):
    """The agent's processes read the ledger the server writes, and the server reads the
    cursor an agent writes. Each is written beside its place and then moved into it, so a
    reader finds the earlier one or the new one, never a file half written."""
    from tex_mcp_web.server import TexMcpWebServer
    from tex_mcp_web.config import Config

    (tmp_path / "paper.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    server = TexMcpWebServer(Config(main="paper.tex", config_path=tmp_path / ".tex-mcp-web.yaml"))
    mcp_server.write_cursor(tmp_path, "2026-11-18T09:00:00+00:00")
    server._save_overleaf_ledger({"thread": {"comment": "c1"}})
    monkeypatch.setattr(shared_file, "WINDOWS", False)
    refuse_replacing(monkeypatch, "agent-cursor.json", 1)
    refuse_replacing(monkeypatch, "overleaf-imported.json", 1)

    with pytest.raises(PermissionError):
        mcp_server.write_cursor(tmp_path, "2026-11-18T10:00:00+00:00")
    with pytest.raises(PermissionError):
        server._save_overleaf_ledger({"other": {"comment": "c2"}})

    assert mcp_server.read_cursor(tmp_path) == "2026-11-18T09:00:00+00:00"
    assert overleaf.ledger(tmp_path) == {"thread": {"comment": "c1"}}


# One writer adds comments and two readers list them for the seconds given: one reader
# keeps its store, as the page's server does, and one opens the store afresh for each
# read, as each of the agent's tool calls does. Each prints what it met as JSON.
_HAMMER = """
import json, sys, time
from pathlib import Path
from tex_mcp_web.comments import CommentStore, PaperAnchor

role, path, until = sys.argv[1], Path(sys.argv[2]), time.time() + float(sys.argv[3])
errors, count, fell, rounds = [], 0, False, 0
store = CommentStore(path)
while time.time() < until:
    rounds += 1
    try:
        if role == "writer":
            store.add(PaperAnchor(), f"Comment {rounds}.")
            continue
        found = len((store if role == "keeps" else CommentStore(path)).list())
        fell |= found < max(count, 1)
        count = max(count, found)
    except Exception as error:
        errors.append(f"{type(error).__name__}: {error}")
print(json.dumps({"role": role, "rounds": rounds, "errors": errors[:5], "error_count": len(errors), "fell": fell}))
"""


def test_a_writer_and_two_readers_in_their_own_processes_meet_no_error(tmp_path):
    """No read fails, finds the file half written, or finds fewer comments than an earlier
    read did. On Windows a store that does not wait out the others fails hundreds of times."""
    path = tmp_path / "comments.json"
    CommentStore(path).add(PaperAnchor(), "The first comment.")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))}
    processes = [subprocess.Popen([sys.executable, "-c", _HAMMER, role, str(path), "3"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=tmp_path)
                 for role in ("writer", "keeps", "opens")]
    reports = []
    for process in processes:
        out, err = process.communicate(timeout=60)
        assert process.returncode == 0, err.decode("utf-8", "replace")
        reports.append(json.loads(out))

    assert [(r["role"], r["error_count"], r["errors"], r["fell"]) for r in reports] == [
        ("writer", 0, [], False), ("keeps", 0, [], False), ("opens", 0, [], False)]
    assert all(r["rounds"] > 0 for r in reports)
    assert len(CommentStore(path).list()) == 1 + reports[0]["rounds"]
