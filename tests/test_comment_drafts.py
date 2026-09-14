import hashlib
import json
import multiprocessing
from pathlib import Path
import re

import pytest

from tex_mcp_web.comments import CommentStore, PaperAnchor


@pytest.fixture
def store(tmp_path):
    return CommentStore(tmp_path / ".tex-mcp-web" / "comments.json")


def add(store, text="질문\n두 번째 줄"):
    return store.add(PaperAnchor(), text)


def fill(path, texts):
    path = Path(path)
    body = path.read_text(encoding="utf-8")
    replies = iter(texts)
    body = re.sub(r"(<!-- reply:[^\n]+ -->\n)\n(<!-- /reply:)",
                  lambda m: m[1] + next(replies) + "\n" + m[2], body)
    path.write_text(body, encoding="utf-8")


def test_round_trip(store):
    comments = [add(store), add(store, "다른 질문")]
    result = store.export_comments([c.id for c in comments])
    assert set(result) == {"path", "sha256", "comment_ids"}
    assert result["sha256"] == hashlib.sha256(Path(result["path"]).read_bytes()).hexdigest()
    text = "답변: αβ\n\n```python\nx = 1\n```\n# Heading\n<!-- ordinary comment -->"
    # Existing research prose is copied locally into the server-owned Reply block.
    prose = store.path.parent / "research.md"
    prose.write_text(text, encoding="utf-8")
    fill(result["path"], [prose.read_text(encoding="utf-8"), "두 번째 답변"])
    updated = store.reply_file(result["path"], edits=["source:1-3"])
    assert [c.id for c in updated] == result["comment_ids"]
    assert updated[0].thread[-1].text == text
    assert updated[0].thread[-1].edits == ["source:1-3"]
    assert all(c.status == "open" and c.thread[-1].author == "agent" for c in updated)
    assert all(c.updated != old.updated for c, old in zip(updated, comments))
    with pytest.raises(ValueError, match="stale"):
        store.reply_file(result["path"])


@pytest.mark.parametrize("mutation", ["reply", "delete", "edit", "resolve"])
def test_stale_batch_is_all_or_nothing(store, mutation):
    a, b = add(store), add(store)
    result = store.export_comments([a.id, b.id])
    fill(result["path"], ["first", "second"])
    if mutation == "delete":
        store.delete(b.id)
    elif mutation == "edit":
        store.edit_entry(b.id, 0, "human edit", "human")
    elif mutation == "resolve":
        store.resolve(b.id, "done", "human")
    else:
        store.reply(b.id, "human reply", "human")
    before = store.path.read_bytes()
    with pytest.raises((ValueError, KeyError)):
        store.reply_file(result["path"])
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("damage", ["blank", "header", "timestamp", "truncated", "utf8"])
def test_malformed_draft_does_not_write(store, damage):
    comment = add(store)
    result = store.export_comments([comment.id])
    path = Path(result["path"])
    if damage != "blank":
        fill(path, ["reply"])
    if damage == "utf8":
        path.write_bytes(b"\xff")
    elif damage != "blank":
        text = path.read_text(encoding="utf-8")
        text = {"header": "wrong" + text, "timestamp": text.replace(comment.updated, "changed"),
                "truncated": text[:-10]}[damage]
        path.write_text(text, encoding="utf-8")
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.reply_file(str(path))
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("attack", ["outside", "traversal", "unregistered", "file_link", "snapshot_link", "directory_link", "hardlink"])
def test_path_confinement(store, tmp_path, attack):
    result = store.export_comments([add(store).id])
    path = Path(result["path"])
    fill(path, ["reply"])
    outside = tmp_path / "outside.md"
    outside.write_bytes(path.read_bytes())
    if attack == "outside":
        path = outside
    elif attack == "traversal":
        path = path.parent / ".." / path.parent.name / path.name
    elif attack == "unregistered":
        path = path.parent / ("0" * 32 + ".md")
        path.write_bytes(outside.read_bytes())
    elif attack in {"file_link", "hardlink", "snapshot_link"}:
        target = path.with_suffix(".snapshot") if attack == "snapshot_link" else path
        target.unlink()
        if attack == "hardlink":
            target.hardlink_to(outside)
        else:
            target.symlink_to(outside)
    else:
        moved = path.parent.with_name("moved")
        path.parent.rename(moved)
        path.parent.symlink_to(moved, target_is_directory=True)
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.reply_file(str(path))
    assert store.path.read_bytes() == before


def _import(path, draft, ready, start, results):
    store = CommentStore(Path(path))
    ready.put(True)
    start.wait(5)
    try:
        store.reply_file(draft)
        results.put("ok")
    except ValueError as error:
        results.put(str(error))


def test_concurrent_import_checks_snapshot_under_lock(store):
    comments = [add(store), add(store)]
    result = store.export_comments([c.id for c in comments])
    fill(result["path"], ["one", "two"])
    ctx = multiprocessing.get_context("fork")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = [ctx.Process(target=_import, args=(str(store.path), result["path"], ready, start, results)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            ready.get(timeout=5)
        start.set()
        outcomes = [results.get(timeout=5) for _ in processes]
        assert outcomes.count("ok") == 1
        assert sum("stale" in value for value in outcomes) == 1
        assert [len(c.thread) for c in store.list()] == [2, 2]
    finally:
        for process in processes:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join()
        ready.close()
        results.close()


def test_version_and_selection_validation(store):
    comment = add(store)
    for ids in ([], [comment.id, comment.id], ["missing"]):
        with pytest.raises((ValueError, KeyError)):
            store.export_comments(ids)
    result = store.export_comments([comment.id])
    fill(result["path"], ["reply"])
    data = json.loads(store.path.read_text())
    data["version"] = -1
    store.path.write_text(json.dumps(data))
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="version"):
        store.reply_file(result["path"])
    assert store.path.read_bytes() == before
