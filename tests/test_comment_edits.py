"""An agent rewrites its own thread entries, named by stable ids, all or none."""

import json
import multiprocessing
import re
from pathlib import Path

import pytest

from tex_mcp_web.comments import CommentStore, PaperAnchor



@pytest.fixture
def store(tmp_path):
    return CommentStore(tmp_path / ".tex-mcp-web" / "comments.json")


def add(store, text="질문"):
    return store.add(PaperAnchor(), text)


def test_entries_written_before_ids_get_them_once(tmp_path):
    path = tmp_path / "comments.json"
    path.write_text(json.dumps({"version": 5, "comments": [{
        "id": "c-00000001", "anchor": {"kind": "paper"}, "status": "open",
        "created": "2026-01-01T00:00:00+00:00", "updated": "2026-01-01T00:00:00+00:00",         "thread": [{"author": "human", "at": "2026-01-01T00:00:00+00:00", "text": "q"},
                   {"author": "agent", "at": "2026-01-01T00:01:00+00:00", "text": "a", "edits": ["x.tex:1"]}],
    }]}), encoding="utf-8")
    ids = [entry.id for entry in CommentStore(path).get("c-00000001").thread]
    assert all(re.fullmatch(r"e-[0-9a-f]{8}", value) for value in ids) and len(set(ids)) == 2
    assert [entry.id for entry in CommentStore(path).get("c-00000001").thread] == ids   # persisted
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["comments"][0]["thread"][1] == {
        "id": ids[1], "author": "agent", "at": "2026-01-01T00:01:00+00:00", "text": "a", "edits": ["x.tex:1"]}


def test_agent_rewrites_its_own_entry_and_nothing_else(store):
    comment = add(store)
    replied = store.reply(comment.id, "first answer", "agent", edits=["a.tex:1"])
    human_entry, agent_entry = replied.thread
    before = replied.updated

    changed = store.edit_agent_entries([(agent_entry.id, "better answer")], {comment.id: before})
    assert [c.id for c in changed] == [comment.id]
    entry = store.get(comment.id).thread[1]
    assert entry.text == "better answer" and entry.id == agent_entry.id
    assert entry.author == "agent" and entry.at == agent_entry.at and entry.edits == ["a.tex:1"]
    assert entry.updated_at is not None and store.get(comment.id).updated != before

    for bad, expected, error in (
        ([(human_entry.id, "rewritten by agent")], {comment.id: store.get(comment.id).updated}, "written by human"),
        ([("e-deadbeef", "x")], {comment.id: store.get(comment.id).updated}, "not found"),
        ([(agent_entry.id, "x")], {comment.id: before}, "stale"),
        ([(agent_entry.id, "x")], {}, "no updated stamp"),
        ([(agent_entry.id, "  ")], {comment.id: store.get(comment.id).updated}, "empty"),
    ):
        snapshot = store.path.read_bytes()
        with pytest.raises((ValueError, KeyError), match=error):
            store.edit_agent_entries(bad, expected)
        assert store.path.read_bytes() == snapshot


def test_a_batch_with_one_bad_edit_writes_nothing(store):
    one, two = add(store), add(store, "another")
    a = store.reply(one.id, "answer one", "agent").thread[1]
    b = store.reply(two.id, "answer two", "agent").thread[1]
    stamps = {one.id: store.get(one.id).updated, two.id: store.get(two.id).updated}
    snapshot = store.path.read_bytes()
    with pytest.raises(ValueError, match="stale"):
        store.edit_agent_entries([(a.id, "new one"), (b.id, "new two")], {**stamps, two.id: "wrong"})
    assert store.path.read_bytes() == snapshot
    changed = store.edit_agent_entries([(a.id, "new one"), (b.id, "new two")], stamps)
    assert {c.id for c in changed} == {one.id, two.id}
    assert store.get(one.id).thread[1].text == "new one" and store.get(two.id).thread[1].text == "new two"


def _edit(path, entry_id, stamp_by_comment, ready, start, results):
    store = CommentStore(Path(path))
    ready.put(True)
    start.wait(5)
    try:
        store.edit_agent_entries([(entry_id, "mine")], stamp_by_comment)
        results.put("ok")
    except ValueError as error:
        results.put(str(error))


def test_concurrent_edits_of_one_entry_let_exactly_one_through(store):
    comment = add(store)
    entry = store.reply(comment.id, "answer", "agent").thread[1]
    stamps = {comment.id: store.get(comment.id).updated}
    ctx = multiprocessing.get_context("fork")
    ready, results, start = ctx.Queue(), ctx.Queue(), ctx.Event()
    processes = [ctx.Process(target=_edit, args=(str(store.path), entry.id, stamps, ready, start, results))
                 for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            ready.get(timeout=5)
        start.set()
        outcomes = [results.get(timeout=5) for _ in processes]
        assert outcomes.count("ok") == 1 and sum("stale" in value for value in outcomes) == 1
    finally:
        for process in processes:
            process.join(5)
    assert store.get(comment.id).thread[1].text == "mine"


def _fill(path, replies=(), edits=()):
    body = Path(path).read_text(encoding="utf-8")
    replies = iter(replies)
    body = re.sub(r"(<!-- reply:[^\n]+ -->\n)\n(<!-- /reply:)",
                  lambda m: m[1] + next(replies, "") + "\n" + m[2], body)
    for entry_id, text in edits:
        body = re.sub(rf"(<!-- edit:[0-9a-f]+:{entry_id} -->\n).*?(\n<!-- /edit:)",
                      lambda m: m[1] + text + m[2], body, flags=re.DOTALL)
    Path(path).write_text(body, encoding="utf-8")


def test_a_draft_carries_edit_blocks_for_agent_entries(store):
    comment = add(store)
    entry = store.reply(comment.id, "first\nanswer", "agent").thread[1]
    result = store.export_comments([comment.id])
    draft = Path(result["path"]).read_text(encoding="utf-8")
    assert f"### Edit {entry.id}" in draft and "first\nanswer\n<!-- /edit:" in draft
    assert draft.count("### Edit") == 1                     # the human's entry has none

    # Left alone, the Edit block changes nothing and an empty Reply adds nothing.
    with pytest.raises(ValueError, match="no reply and no changed Edit"):
        store.reply_file(result["path"])

    _fill(result["path"], replies=["follow-up"], edits=[(entry.id, "revised\nanswer")])
    updated = store.reply_file(result["path"], edits=["b.tex:2"])
    thread = store.get(comment.id).thread
    assert [e.text for e in thread] == ["질문", "revised\nanswer", "follow-up"]
    assert thread[1].updated_at is not None and thread[2].edits == ["b.tex:2"]
    assert updated[0].id == comment.id
    with pytest.raises(ValueError, match="stale"):                 # a draft is spent by its import
        store.reply_file(result["path"])

    again = store.export_comments([comment.id])
    _fill(again["path"], edits=[(entry.id, "")])
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="must not be emptied"):
        store.reply_file(again["path"])
    assert store.path.read_bytes() == before
    text = Path(again["path"]).read_text(encoding="utf-8").replace("<!-- /edit:", "<!-- /edit-")
    Path(again["path"]).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        store.reply_file(again["path"])
    assert store.path.read_bytes() == before
