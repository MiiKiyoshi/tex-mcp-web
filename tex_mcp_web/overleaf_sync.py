"""The paper's files kept the same as its Overleaf project, both ways, as edits: live
while a page is open (ProjectSync), or when the reviewer presses Get or Send (HandSync).

Overleaf's editor sends each change as a small edit naming its version, and the service
moves it past the edits made meanwhile; this does the same for each file. Overleaf's
edits are applied to the file here, and what changed in the file here is sent as the
smallest edit that makes it, so a comment on words that were not changed keeps them.

Nothing is overwritten on a guess. Each file keeps, beside the paper, the text both sides
last agreed on and its version, so a file edited while no page was open, or while the
connection was down, is brought together with what changed on Overleaf meanwhile, and a
file that already holds Overleaf's text agrees with it. When
that cannot be done for certain, when an edit would delete every word of an open comment,
or when Overleaf turns an edit away, the file stops and the reviewer chooses which text
stands. The text that gives way is kept in ``.tex-mcp-web/overleaf-sync/kept/`` first.

Only documents of Overleaf's older kind (``sharejs-text-ot``) are followed. A file only
here is not sent there, and files added, renamed or deleted on Overleaf are not created,
moved or deleted here beyond taking up a document's text.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from . import overleaf, overleaf_ot as ot

logger = logging.getLogger(__name__)

KIND = "sharejs-text-ot"
# Seconds between looks at the files, besides the watcher's word.
DISK_POLL = 1.0
# Seconds after which a file's text is read again from Overleaf to make sure both agree.
CHECK_AFTER = 30.0


class Session(Protocol):
    public_id: str

    async def call(self, name: str, args: list, timeout: float = 30) -> list: ...

    async def emit(self, name: str, args: list) -> None: ...


def _decode(text: str) -> str:
    """A joinDoc line: UTF-8 bytes sent one per character."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def text_hash(text: str) -> str:
    """The hash Overleaf checks an edit's result against, git's blob hash."""
    return hashlib.sha1(f"blob {ot.js_len(text)}\0".encode() + text.encode("utf-8")).hexdigest()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Doc:
    """One file and its Overleaf document."""

    def __init__(self, doc_id: str, path: str) -> None:
        self.doc_id = doc_id
        self.path = path
        self.status = "joining"     # joining, synced, sending, held, differs, stopped, unsupported
        self.message = ""
        self.version = -1
        self.server = ""            # Overleaf's text at version
        self.local = ""             # the file's text as last read or written here
        self.local_before = ""      # hash of the file before the last write here
        self.inflight: ot.Op | None = None
        self.inflight_ids: list[str] = []
        self.pending: ot.Op = []
        self.comments: list[dict] = []
        self.threads: list[str] = []   # open threads an edit held back would delete
        self.mark: tuple[int, int] | None = None
        self.last_check = 0.0
        self.busy_since_check = False

    @property
    def known(self) -> bool:
        return self.version >= 0

    def record(self) -> dict:
        return {"path": self.path, "version": self.version, "server": self.server, "local": self.local,
                "local_before": self.local_before, "inflight": self.inflight,
                "inflight_ids": self.inflight_ids, "pending": self.pending}

    def restore(self, record: dict) -> None:
        self.version, self.server, self.local = record["version"], record["server"], record["local"]
        self.local_before = record.get("local_before", "")
        self.inflight, self.inflight_ids = record.get("inflight"), list(record.get("inflight_ids") or [])
        self.pending = list(record.get("pending") or [])

    def view(self) -> dict:
        return {"path": self.path, "status": self.status, "message": self.message, "threads": self.threads}


class _Records:
    """The paper's files that are documents on Overleaf, and for each the record of the text
    both sides last agreed on and its version, kept in ``.tex-mcp-web/overleaf-sync/``. The
    files following Overleaf live and the files brought together by hand keep the same
    record, so either picks up where the other left off."""

    def __init__(self, root: Path, written: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.root = root.resolve()
        self.state_dir = self.root / ".tex-mcp-web" / "overleaf-sync"
        self._written = written

    # ----- The files here -----

    def _file(self, doc: Doc) -> Path:
        file = (self.root / doc.path).resolve()
        if self.root not in file.parents or (self.root / ".tex-mcp-web") in file.parents:
            raise ValueError(f"{doc.path} is not a file of this paper")
        return file

    def _read(self, doc: Doc) -> str | None:
        file = self._file(doc)
        try:
            info = file.stat()
        except FileNotFoundError:
            return None
        data = file.read_bytes()
        doc.mark = (info.st_mtime_ns, info.st_size)
        return data.decode("utf-8")

    def _write(self, doc: Doc, text: str) -> None:
        """Write the file, unless it changed since it was read: then the caller reads it
        again. The record goes first, naming what the file held, so a stop between the
        two is told from an edit."""
        file = self._file(doc)
        try:
            info = file.stat()
            now = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            now = None
        if now is not None and now != doc.mark:
            raise _ChangedMeanwhile()
        doc.local_before, doc.local = _sha(doc.local), text
        self._save(doc)
        file.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IMODE(file.stat().st_mode) if file.exists() else 0o644
        with tempfile.NamedTemporaryFile("wb", dir=file.parent, prefix=f".{file.name}-", suffix=".tmp",
                                         delete=False) as handle:
            handle.write(text.encode("utf-8"))
            staging = Path(handle.name)
        os.chmod(staging, mode)
        os.replace(staging, file)
        info = file.stat()
        doc.mark = (info.st_mtime_ns, info.st_size)
        if self._written is not None:
            asyncio.ensure_future(self._written(doc.path))

    def _keep(self, doc: Doc, text: str, which: str) -> Path:
        """Keep a text that is about to give way."""
        return self._keep_bytes(doc.path, text.encode("utf-8"), which)

    def _keep_bytes(self, path: str, data: bytes, which: str) -> Path:
        kept = self.state_dir / "kept" / f"{path}.{which}.{time.strftime('%Y%m%d-%H%M%S')}"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_bytes(data)
        return kept

    # ----- The record -----

    def _record_path(self, doc_id: str) -> Path:
        return self.state_dir / f"{doc_id}.json"

    def _save(self, doc: Doc) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._record_path(doc.doc_id)
        staging = path.with_suffix(".tmp")
        staging.write_text(json.dumps(doc.record()), encoding="utf-8")
        os.replace(staging, path)

    def _load(self, doc: Doc) -> bool:
        try:
            record = json.loads(self._record_path(doc.doc_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if record.get("path") != doc.path:   # renamed: begin again
            return False
        doc.restore(record)
        return True


class ProjectSync(_Records):
    """Every document of the project that is a file here, kept the same both ways."""

    def __init__(self, root: Path, changed: Callable[[], Any],
                 written: Callable[[str], Awaitable[None]] | None = None) -> None:
        super().__init__(root, written)
        self._changed = changed
        self.docs: dict[str, Doc] = {}
        # The project's uploaded files, kept the same both ways too, in a folder that is no git clone.
        self.uploads: Uploads | None = None
        self._uploads_quiet_until = 0.0
        self.resolved_threads: set[str] = set()
        self._session: Session | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._poll: asyncio.Task | None = None

    # ----- What the presence and the server tell it -----

    def session_opened(self, session: Session, docs: dict[str, str]) -> None:
        self._queue.put_nowait(("open", session, dict(docs)))

    def session_closed(self) -> None:
        self._queue.put_nowait(("closed",))

    def remote(self, update: dict) -> None:
        self._queue.put_nowait(("remote", update))

    def failed(self, doc_id: str, error: str) -> None:
        self._queue.put_nowait(("failed", doc_id, error))

    def poke(self) -> None:
        self._queue.put_nowait(("disk",))

    def forget_thread(self, thread: str) -> None:
        self._queue.put_nowait(("forget", thread))

    async def decide(self, path: str, choice: str) -> str | None:
        """The reviewer's choice for a stopped file; an error, or None."""
        done: asyncio.Future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(("decide", path, choice, done))
        return await done

    async def comment(self, path: str, words: str, line: int, thread: str,
                      opened: Callable[[], Awaitable[None]]) -> str | None:
        """Put a new thread on *words* in *path*, the occurrence nearest *line*, in turn with
        this file's edits; see LivePresence.comment. An error, or None."""
        done: asyncio.Future = asyncio.get_running_loop().create_future()
        self._queue.put_nowait(("comment", path, words, line, thread, opened, done))
        return await done

    def snapshot(self) -> list[dict]:
        views = [doc.view() for doc in sorted(self.docs.values(), key=lambda doc: doc.path)]
        if self.uploads is not None:
            views += [upload.view() for upload in sorted(self.uploads.files.values(), key=lambda upload: upload.path)
                      if upload.status != "same"]
        return views

    def owns(self, path: str) -> bool:
        return any(doc.path == path and doc.status not in ("unsupported",) for doc in self.docs.values())

    # ----- Running -----

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            self._poll = asyncio.create_task(self._tick())

    async def stop(self) -> None:
        for task in (self._poll, self._task):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._task = self._poll = None
        self._session = None

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(DISK_POLL)
            self._queue.put_nowait(("disk",))
            self._queue.put_nowait(("check",))

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # one event's trouble must not end the rest
                logger.exception("Overleaf sync: %s failed", event[0])

    async def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "open":
            self._session = event[1]
            await self._open_all(event[2])
            await self._settle_uploads()
        elif kind == "closed":
            self._session = None
            for doc in self.docs.values():
                if doc.status in ("synced", "sending"):
                    doc.status = "joining"
            self._notify()
        elif kind == "remote":
            await self._remote(event[1])
        elif kind == "disk":
            for doc in list(self.docs.values()):
                if doc.status in ("synced", "sending", "held"):
                    self._absorb(doc)
                    await self._send(doc)
            await self._settle_uploads()
        elif kind == "check":
            await self._check_some()
        elif kind == "forget":
            for doc in self.docs.values():
                doc.comments = [comment for comment in doc.comments if comment["t"] != event[1]]
        elif kind == "decide":
            _, path, choice, done = event
            try:
                done.set_result(await self._decide(path, choice))
            except Exception as error:  # the page is told
                done.set_result(str(error))
        elif kind == "comment":
            _, path, words, line, thread, opened, done = event
            try:
                done.set_result(await self._comment(path, words, line, thread, opened))
            except Exception as error:  # the page is told
                done.set_exception(error)
        elif kind == "failed":
            doc = self.docs.get(event[1])
            if doc is not None:
                doc.inflight, doc.inflight_ids = None, []
                self._save(doc)
                self._set(doc, "stopped", f"Overleaf turned away an edit to this file ({event[2]}). "
                                          "Choose which text stands.")
        elif kind == "rejoin":
            doc = self.docs.get(event[1])
            if doc is not None and self._session is not None:
                await self._join(doc)

    def _notify(self) -> None:
        try:
            self._changed()
        except Exception:
            logger.exception("Overleaf sync listener failed")

    def _set(self, doc: Doc, status: str, message: str = "", threads: list[str] | None = None) -> None:
        if (doc.status, doc.message, doc.threads) != (status, message, threads or []):
            doc.status, doc.message, doc.threads = status, message, threads or []
            self._notify()

    def _absorb(self, doc: Doc) -> None:
        """Take up what changed in the file here as an edit still to send."""
        text = self._read(doc)
        if text is None or text == doc.local:
            return
        doc.pending = doc.pending + ot.diff(doc.local, text, self._comments_here(doc))
        doc.local = text
        self._save(doc)

    def _comments_here(self, doc: Doc) -> list[dict]:
        """The open comments on the file's text as this side last followed it."""
        try:
            moved = ot.move_comments(doc.comments, (doc.inflight or []) + doc.pending)
        except (KeyError, TypeError):
            return []
        return [c for c in moved if c["t"] not in self.resolved_threads]

    # ----- Joining -----

    async def _open_all(self, docs: dict[str, str]) -> None:
        for doc_id, path in docs.items():
            doc = self.docs.get(doc_id)
            if doc is None or doc.path != path:
                doc = self.docs[doc_id] = Doc(doc_id, path)
                try:
                    self._file(doc)
                except ValueError:
                    del self.docs[doc_id]
                    continue
            if self._session is None:
                return
            await self._join(doc)
        for doc_id in [key for key in self.docs if key not in docs]:
            del self.docs[doc_id]
        self._notify()

    async def _join(self, doc: Doc) -> None:
        session = self._session
        if session is None:
            return
        if not doc.known:
            self._load(doc)
        waiting = doc.known and doc.status in ("differs", "stopped")
        since = doc.version if doc.known and not waiting else -1
        reply = await session.call("joinDoc", [doc.doc_id, since, {"encodeRanges": True, "supportsHistoryOT": True}])
        if reply and reply[0] is not None and since >= 0:
            # The edits since then are gone: read the text whole.
            reply = await session.call("joinDoc", [doc.doc_id, -1, {"encodeRanges": True, "supportsHistoryOT": True}])
            since = -2
        if not reply or reply[0] is not None:
            self._set(doc, "stopped", f"Overleaf would not open this file: {reply[0] if reply else 'no answer'}")
            return
        lines, version, ops = reply[1], reply[2], reply[3] if len(reply) > 3 else []
        ranges, kind = (reply[4] if len(reply) > 4 else None) or {}, reply[5] if len(reply) > 5 else KIND
        if kind != KIND:
            self._set(doc, "unsupported", f"Overleaf keeps this file as {kind}, which is not followed here.")
            return
        theirs = "\n".join(_decode(str(line)) for line in lines)
        comments = [{"t": str((c.get("op") or {}).get("t")), "p": int((c.get("op") or {}).get("p", 0)),
                     "n": ot.js_len(_decode(str((c.get("op") or {}).get("c", ""))))}
                    for c in ranges.get("comments") or [] if isinstance(c, dict)]
        disk = self._read(doc)
        if waiting:     # Overleaf's text kept up to date; the reviewer's choice still waits
            doc.version, doc.server, doc.comments = version, theirs, comments
            return
        if since >= 0:
            await self._catch_up(doc, ops, theirs, version, comments, disk)
        elif since == -2:
            await self._merge_whole(doc, theirs, version, comments, disk)
        else:
            await self._first(doc, theirs, version, comments, disk)

    async def _first(self, doc: Doc, theirs: str, version: int, comments: list, disk: str | None) -> None:
        doc.version, doc.server, doc.comments = version, theirs, comments
        doc.inflight, doc.pending, doc.inflight_ids = None, [], []
        if disk is None or disk == theirs:
            doc.local = disk if disk is not None else ""
            if disk is None:
                self._write(doc, theirs)
            self._save(doc)
            self._set(doc, "synced")
        else:
            doc.local = disk
            self._set(doc, "differs", "This file and Overleaf's differ, and nothing says which changed. "
                                      "Choose which stands.")

    async def _catch_up(self, doc: Doc, ops: list, theirs: str, version: int, comments: list,
                        disk: str | None) -> None:
        """Edits made on Overleaf since both last agreed, and edits made here."""
        # A file that already holds Overleaf's text agrees with it, whatever brought the
        # text there (a git pull does): taken as an edit here, Overleaf's edit would be made
        # a second time on both sides.
        if disk is not None and disk == theirs:
            self._set_first(doc, theirs, version, comments)
            doc.local = disk
            self._save(doc)
            self._set(doc, "synced")
            return
        # A file holding what it held before the last write here stopped between the
        # record and the write: that is not an edit, and the write is made below.
        if disk is not None and disk != doc.local and _sha(disk) != doc.local_before:
            if ops and _overlap(doc.server, theirs, disk):
                doc.local = disk
                self._set_first(doc, theirs, version, comments)
                self._set(doc, "differs", "This file and Overleaf changed the same place while apart. "
                                          "Choose which stands.")
                return
            doc.pending = doc.pending + ot.diff(doc.local, disk)
            doc.local = disk
        for update in ops:
            if doc.inflight is not None and (update.get("meta") or {}).get("source") in doc.inflight_ids:
                self._acknowledged(doc)
            else:
                self._take(doc, update.get("op") or [], write=False)
        doc.comments = comments
        if doc.version != version or doc.server != theirs:
            self._set(doc, "differs", "Overleaf's text is not what this file followed. Choose which stands.")
            return
        if disk is None or doc.local != disk:
            try:
                self._write(doc, doc.local)
            except _ChangedMeanwhile:
                self._absorb(doc)
        self._save(doc)
        self._set(doc, "synced")
        await self._send(doc, again=True)

    async def _merge_whole(self, doc: Doc, theirs: str, version: int, comments: list, disk: str | None) -> None:
        """The edits since both last agreed are gone: bring the texts together whole when
        each side changed other places, else ask."""
        base = doc.server
        mine = disk if disk is not None else doc.local
        if doc.inflight is not None:
            doc.local = mine
            self._set_first(doc, theirs, version, comments)
            self._set(doc, "differs", "An edit sent before the connection dropped may or may not have reached "
                                      "Overleaf. Choose which text stands.")
            return
        together = merge(base, theirs, mine)
        self._set_first(doc, theirs, version, comments)
        doc.local = mine
        if together is None:
            self._set(doc, "differs", "This file and Overleaf changed the same place while apart. "
                                      "Choose which stands.")
            return
        merged, doc.pending = together
        if merged != mine:
            try:
                self._write(doc, merged)
            except _ChangedMeanwhile:
                self._set(doc, "differs", "The file changed while it was being brought together. Choose which stands.")
                return
        self._save(doc)
        self._set(doc, "synced")
        await self._send(doc)

    def _set_first(self, doc: Doc, theirs: str, version: int, comments: list) -> None:
        doc.version, doc.server, doc.comments = version, theirs, comments
        doc.inflight, doc.inflight_ids, doc.pending = None, [], []

    # ----- Edits from Overleaf -----

    async def _remote(self, update: dict) -> None:
        doc = self.docs.get(str(update.get("doc")))
        if doc is None or not doc.known or doc.status in ("joining", "unsupported"):
            return
        version = update.get("v")
        if not isinstance(version, int):
            return
        if "op" not in update:     # our own edit, applied
            if doc.inflight is not None and version == doc.version:
                self._acknowledged(doc)
                self._save(doc)
                if doc.status == "sending":
                    self._set(doc, "synced")
                await self._send(doc)
            return
        if version < doc.version:
            return
        if version > doc.version:  # some were missed: read them
            self._queue.put_nowait(("rejoin", doc.doc_id))
            return
        try:
            op = update.get("op") or []
            if doc.status in ("differs", "stopped"):
                doc.server = ot.apply(doc.server, ot.text_only(op))
                doc.comments = ot.move_comments(doc.comments, op)
                doc.version += 1
                return
            self._absorb(doc)
            self._take(doc, op, write=True)
            doc.busy_since_check = True
        except ot.Mismatch as error:
            self._set(doc, "stopped", f"An edit from Overleaf did not fit this file's copy ({error}). "
                                      "Choose which text stands.")

    def _take(self, doc: Doc, op: ot.Op, *, write: bool) -> None:
        text_op = ot.text_only(op)
        moved = text_op
        if doc.inflight is not None:
            doc.inflight, moved = ot.transform_x(doc.inflight, moved)
        if doc.pending:
            doc.pending, moved = ot.transform_x(doc.pending, moved)
        doc.server = ot.apply(doc.server, text_op)
        doc.comments = ot.move_comments(doc.comments, op)
        doc.version += 1
        local = ot.apply(doc.local, moved)
        if not write:
            doc.local = local
            return
        if local != doc.local:
            for _ in range(3):
                try:
                    self._write(doc, local)
                    break
                except _ChangedMeanwhile:  # take the edit here first, then this one past it
                    text = self._read(doc) or ""
                    extra, moved = ot.transform_x(ot.diff(doc.local, text), moved)
                    doc.pending, doc.local = doc.pending + extra, text
                    local = ot.apply(text, moved)
            else:
                raise ot.Mismatch("the file kept changing while an edit from Overleaf was written")
        self._save(doc)

    def _acknowledged(self, doc: Doc) -> None:
        doc.server = ot.apply(doc.server, doc.inflight or [])
        doc.comments = ot.move_comments(doc.comments, doc.inflight or [])
        doc.version += 1
        doc.inflight, doc.inflight_ids = None, []

    # ----- Edits from here -----

    async def _send(self, doc: Doc, *, again: bool = False, anyway: bool = False) -> None:
        session = self._session
        if session is None or doc.status not in ("synced", "sending", "held"):
            return
        if doc.inflight is not None:
            if not again:
                return
            op, ids = doc.inflight, doc.inflight_ids
        else:
            if not doc.pending:
                if doc.status == "held":
                    self._set(doc, "synced")
                return
            if not anyway:
                open_comments = [c for c in ot.move_comments(doc.comments, doc.inflight or [])
                                 if c["t"] not in self.resolved_threads]
                lost = ot.comments_removed(open_comments, doc.pending)
                if lost:
                    self._set(doc, "held", "This edit deletes every word of an open Overleaf comment, so it was "
                                           "not sent. Send it anyway, or put Overleaf's text back.", lost)
                    return
            op, ids = doc.pending, []
            doc.inflight, doc.pending = op, []
        if session.public_id not in ids:
            ids = ids + [session.public_id]
        doc.inflight_ids = ids
        self._save(doc)
        update: dict[str, Any] = {"doc": doc.doc_id, "op": op, "v": doc.version,
                                  "hash": text_hash(ot.apply(doc.server, op))}
        if len(ids) > 1:
            update["dupIfSource"] = ids[:-1]
        self._set(doc, "sending")
        reply = await session.call("applyOtUpdate", [doc.doc_id, update])
        if reply and reply[0] is not None:
            error = reply[0].get("message") if isinstance(reply[0], dict) else reply[0]
            doc.inflight, doc.inflight_ids = None, []
            self._save(doc)
            self._set(doc, "stopped", f"Overleaf did not take an edit to this file ({error}). Choose which text stands.")

    async def _comment(self, path: str, words: str, line: int, thread: str,
                       opened: Callable[[], Awaitable[None]]) -> str | None:
        doc = next((doc for doc in self.docs.values() if doc.path == path), None)
        if doc is None or not doc.known:
            return f"{path} is not followed on Overleaf."
        if self._session is None or doc.status != "synced" or doc.inflight is not None or doc.pending:
            return f"{path} is still being brought in step with Overleaf. Send again in a moment."
        at = ot.nearest(doc.server, words, line)
        if at is None:
            return f"The lines this thread is on are not in {path} on Overleaf yet."
        await opened()
        doc.pending = [{"c": words, "p": at, "t": thread}]
        await self._send(doc)
        if doc.status == "stopped":
            return doc.message
        return None

    async def _check_some(self) -> None:
        """Read again a file whose edits crossed others', to make sure both texts agree."""
        session = self._session
        now = time.monotonic()
        for doc in self.docs.values():
            if (session is None or not doc.busy_since_check or doc.status != "synced" or doc.inflight
                    or doc.pending or now - doc.last_check < CHECK_AFTER):
                continue
            doc.last_check, doc.busy_since_check = now, False
            reply = await session.call("joinDoc", [doc.doc_id, -1, {"encodeRanges": True, "supportsHistoryOT": True}])
            if not reply or reply[0] is not None or reply[2] != doc.version:
                continue
            if "\n".join(_decode(str(line)) for line in reply[1]) != doc.server:
                self._set(doc, "differs", "A check found Overleaf's text is not what this file followed. "
                                          "Choose which stands.")

    # ----- The reviewer's choice -----

    async def _settle_uploads(self) -> None:
        """Uploaded files replaced on one side only go to the other, whole. One that fails waits
        a minute before the next try, so a file Overleaf will not take is not sent on every tick."""
        if self.uploads is None or self._session is None or time.monotonic() < self._uploads_quiet_until:
            return
        before = [upload.view() for upload in self.uploads.files.values()]
        try:
            self.uploads.recount()
            await self.uploads.get()
            await self.uploads.send()
        except (NotConnected, overleaf.OverleafError, OSError) as error:
            logger.info("Overleaf uploads: %s", error)
            self._uploads_quiet_until = time.monotonic() + 60
        if [upload.view() for upload in self.uploads.files.values()] != before:
            self._notify()

    async def _decide(self, path: str, choice: str) -> str | None:
        if self.uploads is not None and path in self.uploads.files:
            return await self.uploads.decide(path, choice, lambda name, data: self._keep_bytes(name, data, "here"))
        doc = next((doc for doc in self.docs.values() if doc.path == path), None)
        if doc is None or not doc.known:
            return f"{path} is not followed on Overleaf"
        if doc.status not in ("differs", "stopped", "held"):
            return f"{path} needs no choice"
        if self._session is None:
            return "not connected to Overleaf"
        disk = self._read(doc)
        if choice == "overleaf":
            # Overleaf's text stands, with any edit of ours it has not taken undone.
            if disk is not None and disk != doc.server:
                self._keep(doc, disk, "here")
            doc.local = disk if disk is not None else doc.local
            doc.inflight, doc.inflight_ids, doc.pending = None, [], []
            if disk is None or disk != doc.server:
                try:
                    self._write(doc, doc.server)
                except _ChangedMeanwhile:
                    return "the file changed meanwhile; choose again"
            self._save(doc)
            self._set(doc, "synced")
            return None
        if choice in ("here", "anyway"):
            if choice == "here":
                mine = disk if disk is not None else doc.local
                self._keep(doc, doc.server, "overleaf")
                doc.inflight, doc.inflight_ids = None, []
                doc.pending, doc.local = ot.diff(doc.server, mine), mine
            else:
                self._absorb(doc)
            self._save(doc)
            self._set(doc, "synced")
            await self._send(doc, anyway=True)
            return None
        return f"no such choice {choice!r}"


class NotConnected(Exception):
    """The page is not in the Overleaf project right now."""


def blob_sha(data: bytes) -> str:
    """The hash Overleaf gives an uploaded file: git's object hash of its bytes."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class Upload:
    """One uploaded file of the project, and where it stands against the file here."""

    def __init__(self, path: str) -> None:
        self.path, self.status, self.message, self.threads = path, "same", "", []

    def view(self) -> dict:
        return {"path": self.path, "status": self.status, "message": self.message, "threads": []}


class Uploads:
    """The project's uploaded files, figures and the like, which Overleaf keeps whole and names
    by the hash of their bytes, brought together with the files here whole. Each keeps the
    hash both sides last agreed on, in ``.tex-mcp-web/overleaf-sync/uploads.json``, and
    stands as same, get (replaced or added on Overleaf), send (replaced here), differs (both
    replaced, or the two differ with nothing to say which changed), or gone (deleted here).
    Downloads and uploads go through Overleaf's own routes, with the reviewer's cookie."""

    def __init__(self, root: Path, tree: Callable[[], tuple[dict[str, dict[str, str]], dict[str, str]]],
                 link: Callable[[], str | None], cookie: Callable[[], tuple[str, str] | None],
                 written: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.root = root.resolve()
        self.record_path = self.root / ".tex-mcp-web" / "overleaf-sync" / "uploads.json"
        self._tree, self._link, self._cookie, self._written = tree, link, cookie, written
        self.files: dict[str, Upload] = {}
        self._refs: dict[str, dict[str, str]] = {}
        self._hashes: dict[str, tuple[tuple[int, int], str]] = {}   # path: (mtime, size), hash
        # Files just uploaded, as Overleaf answered, with the hash the session's tree named
        # before: the answer stands for the file until the tree, joined again after the
        # change, names other bytes than that.
        self._sent: dict[str, tuple[dict[str, str], str]] = {}

    # ----- Records and files -----

    def _records(self) -> dict[str, str]:
        try:
            return dict(json.loads(self.record_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return {}

    def _agree(self, path: str, sha: str) -> None:
        records = self._records()
        records[path] = sha
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.record_path.with_suffix(".tmp")
        staging.write_text(json.dumps(records, indent=1), encoding="utf-8")
        os.replace(staging, self.record_path)

    def _file(self, path: str) -> Path:
        file = (self.root / path).resolve()
        if self.root not in file.parents or (self.root / ".tex-mcp-web") in file.parents:
            raise ValueError(f"{path} is not a file of this paper")
        return file

    def _hash_here(self, path: str) -> str | None:
        """The hash of the file here, worked out again only when its time or size changed."""
        try:
            info = self._file(path).stat()
        except (FileNotFoundError, ValueError):
            return None
        mark = (info.st_mtime_ns, info.st_size)
        known = self._hashes.get(path)
        if known is None or known[0] != mark:
            known = (mark, blob_sha(self._file(path).read_bytes()))
            self._hashes[path] = known
        return known[1]

    def _write(self, path: str, data: bytes) -> None:
        file = self._file(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=file.parent, prefix=f".{file.name}-", suffix=".tmp",
                                         delete=False) as handle:
            handle.write(data)
            staging = Path(handle.name)
        os.chmod(staging, 0o644)
        os.replace(staging, file)
        if self._written is not None:
            asyncio.ensure_future(self._written(path))

    def _access(self) -> tuple[str, tuple[str, str]]:
        link, cookie = self._link(), self._cookie()
        if not link or cookie is None:
            raise NotConnected("The page is not signed in to an Overleaf project. Save the cookie first.")
        return link, cookie

    # ----- Where each stands -----

    def recount(self) -> None:
        refs, _ = self._tree()
        for path, (ref, before) in list(self._sent.items()):
            if refs.get(path, {}).get("hash") == before:
                refs[path] = ref
            else:
                del self._sent[path]
        self._refs = refs
        records = self._records()
        for path in [path for path in self.files if path not in refs]:
            del self.files[path]
        for path, ref in refs.items():
            try:
                self._file(path)
            except ValueError:
                continue
            upload = self.files.setdefault(path, Upload(path))
            here, there, agreed = self._hash_here(path), ref["hash"], records.get(path)
            if here is None:
                upload.status, upload.message = ("gone", "Deleted here, and still on Overleaf.") if agreed else ("get", "")
            elif here == there:
                upload.status, upload.message = "same", ""
                if agreed != there:
                    self._agree(path, there)
            elif agreed is None:
                upload.status, upload.message = "differs", ("This file and Overleaf's differ, and nothing says "
                                                            "which changed. Choose which stands.")
            elif there != agreed and here != agreed:
                upload.status, upload.message = "differs", ("This file and Overleaf's were both replaced. "
                                                            "Choose which stands.")
            else:
                upload.status, upload.message = ("get" if there != agreed else "send"), ""

    def paths(self, *kinds: str) -> list[str]:
        return sorted(upload.path for upload in self.files.values() if upload.status in kinds)

    # ----- Moving them -----

    async def get(self, only: str | None = None) -> None:
        link, cookie = self._access()
        for upload in [upload for upload in self.files.values()
                       if (upload.status == "get" and only is None) or upload.path == only]:
            ref = self._refs[upload.path]
            data = await overleaf.download_file(link, cookie, ref["id"])
            if blob_sha(data) != ref["hash"]:
                upload.status, upload.message = "differs", "Overleaf sent other bytes than it names. Choose again."
                continue
            self._write(upload.path, data)
            self._agree(upload.path, ref["hash"])
            upload.status, upload.message = "same", ""

    async def send(self, only: str | None = None) -> None:
        link, cookie = self._access()
        _, folders = self._tree()
        for upload in [upload for upload in self.files.values()
                       if (upload.status == "send" and only is None) or upload.path == only]:
            folder, _, name = upload.path.rpartition("/")
            data = self._file(upload.path).read_bytes()
            answer = await overleaf.upload_file(link, cookie, folders[folder], name, data)
            if answer.get("hash") != blob_sha(data):
                upload.status, upload.message = "differs", "Overleaf holds other bytes than were sent. Choose which stands."
                continue
            self._agree(upload.path, answer["hash"])
            self._sent[upload.path] = ({"id": str(answer.get("entity_id", "")), "hash": answer["hash"]},
                                       self._refs.get(upload.path, {}).get("hash", ""))
            upload.status, upload.message = "same", ""

    async def decide(self, path: str, choice: str, keep: Callable[[str, bytes], None]) -> str | None:
        upload = self.files.get(path)
        if upload is None or upload.status != "differs":
            return f"{path} needs no choice"
        if choice == "overleaf":
            if (self.root / path).exists():
                keep(path, self._file(path).read_bytes())
            await self.get(only=path)
        elif choice == "here":
            await self.send(only=path)
        else:
            return f"no such choice {choice!r}"
        return None


_JOIN = {"encodeRanges": True, "supportsHistoryOT": True}
# Seconds a request from the page waits for its session in the project, just opened.
JOIN_WAIT = 10


class HandSync(_Records):
    """The paper's files brought together with its Overleaf project's documents only when the
    reviewer asks: Get takes Overleaf's edits into the files, and Send makes the files' edits
    on Overleaf, each as the smallest edit, as the files following Overleaf live do. Where
    things stand is read by opening each document and letting it go again, through the
    session the page keeps in the project.

    Each file stands, against the record of the text both sides last agreed on, as one of:
    same; get, changed on Overleaf; send, changed here; both, each side changed other places,
    which Get brings together; differs, both changed one place, or the two differ with no
    record to say which changed, and the reviewer chooses which stands; held, its edit would
    delete every word of an open comment, and the reviewer sends it anyway or not; gone,
    deleted here; unsupported, a document of Overleaf's newer kind."""

    def __init__(self, root: Path, changed: Callable[[], Any],
                 editor: Callable[[], tuple[Session | None, dict[str, str], str]],
                 written: Callable[[str], Awaitable[None]] | None = None) -> None:
        super().__init__(root, written)
        self._changed = changed
        self._editor = editor
        self._turn = asyncio.Lock()
        self.docs: dict[str, Doc] = {}
        self.resolved_threads: set[str] = set()
        self._theirs: dict[str, tuple[str, int, list[dict]]] = {}   # Overleaf's text, version, comments
        self.read_once = False
        # The project's uploaded files, moved whole by Get and Send too.
        self.uploads: Uploads | None = None

    # ----- What the page shows -----

    def snapshot(self) -> list[dict]:
        views = [doc.view() for doc in self.docs.values() if doc.status not in ("same", "get", "send", "both")]
        if self.uploads is not None:
            views += [upload.view() for upload in self.uploads.files.values()
                      if upload.status not in ("same", "get", "send")]
        return sorted(views, key=lambda view: view["path"])

    def state(self) -> dict:
        """The files Get and Send would move, and those waiting for the reviewer."""
        def paths(*kinds: str) -> list[str]:
            found = [doc.path for doc in self.docs.values() if doc.status in kinds]
            uploads = self.uploads.paths(*kinds) if self.uploads is not None else []
            return sorted(found + uploads)
        return {"read": self.read_once, "get": paths("get", "both"), "send": paths("send"),
                "choose": paths("differs", "held", "stopped"), "gone": paths("gone")}

    def owns(self, path: str) -> bool:
        return any(doc.path == path for doc in self.docs.values())

    # ----- Asked by the page -----

    async def read(self) -> dict:
        async with self._turn:
            await self._read_all()
        return self.state()

    async def get(self) -> dict:
        """Write Overleaf's edits into the files: its text where only Overleaf changed a file,
        and both sides' edits where each changed other places."""
        async with self._turn:
            await self._read_all()
            for doc in [doc for doc in self.docs.values() if doc.status in ("get", "both")]:
                theirs, version, _ = self._theirs[doc.doc_id]
                disk = self._read(doc)
                together = merge(doc.server, theirs, disk) if doc.status == "both" else (theirs, [])
                if together is None:
                    continue
                doc.version, doc.server = version, theirs
                try:
                    if disk != together[0]:
                        self._write(doc, together[0])
                    else:
                        doc.local = disk
                        self._save(doc)
                except _ChangedMeanwhile:
                    continue
            if self.uploads is not None:
                await self.uploads.get()
            self._recount()
        return self.state()

    async def send(self, path: str | None = None) -> dict:
        """Make the files' edits on Overleaf, each file that changed only here, or with *path*
        that one file, sent though it deletes every word of an open comment."""
        async with self._turn:
            session = await self._session()
            await self._read_all()
            chosen = [doc for doc in self.docs.values()
                      if (doc.status == "send" and path is None) or (doc.path == path and doc.status == "held")]
            for doc in chosen:
                await self._send(session, doc)
            if self.uploads is not None and path is None:
                await self.uploads.send()
            self._recount()
        return self.state()

    async def decide(self, path: str, choice: str) -> str | None:
        """The reviewer's choice for a file that differs: Overleaf's text stands, or this
        file's, which Send then makes on Overleaf. The text that gives way is kept first."""
        if choice == "anyway":
            await self.send(path)
            return None
        if self.uploads is not None and path in self.uploads.files:
            async with self._turn:
                answer = await self.uploads.decide(path, choice, lambda name, data: self._keep_bytes(name, data, "here"))
                self._recount()
            return answer
        async with self._turn:
            doc = next((doc for doc in self.docs.values() if doc.path == path), None)
            if doc is None or doc.doc_id not in self._theirs:
                return f"{path} is not a document of the Overleaf project"
            if doc.status not in ("differs", "stopped"):
                return f"{path} needs no choice"
            theirs, version, _ = self._theirs[doc.doc_id]
            disk = self._read(doc)
            doc.version, doc.server = version, theirs
            if choice == "overleaf":
                if disk is not None and disk != theirs:
                    self._keep(doc, disk, "here")
                try:
                    self._write(doc, theirs)
                except _ChangedMeanwhile:
                    return "the file changed meanwhile; choose again"
            elif choice == "here":
                self._keep(doc, theirs, "overleaf")
                doc.local = theirs
                self._save(doc)
            else:
                return f"no such choice {choice!r}"
            self._recount()
        return None

    async def text_of(self, path: str) -> str | None:
        """Overleaf's text of the file at *path*, read now, or None when it is no document."""
        async with self._turn:
            session, docs, main = self._editor()
            doc_id = next((key for key, name in docs.items() if name == path), None)
            if session is None or doc_id is None:
                return None
            read = await self._open(session, doc_id, main)
            return None if read is None else read[0]

    def local_changed(self) -> None:
        """A file changed here: what Send would move is counted again from what Overleaf
        held when last read. A Get or a Send under way counts it when done."""
        if self.read_once and not self._turn.locked():
            self._recount()

    # ----- Reading Overleaf -----

    async def _session(self) -> Session:
        """The page's session in the project, waited for a little while it is joining."""
        for _ in range(int(JOIN_WAIT / 0.1)):
            session, _, _ = self._editor()
            if session is not None:
                return session
            await asyncio.sleep(0.1)
        raise NotConnected("The page is not in the Overleaf project right now. Try again in a moment.")

    async def _open(self, session: Session, doc_id: str, main: str) -> tuple[str, int, list[dict]] | str | None:
        """A document's text, version and comments, or why it cannot be followed, opened and
        let go again unless it is the main file, which the session keeps open."""
        reply = await session.call("joinDoc", [doc_id, -1, _JOIN])
        if doc_id != main:
            try:
                await session.call("leaveDoc", [doc_id], timeout=5)
            except (ConnectionError, asyncio.TimeoutError):
                pass
        if not reply or reply[0] is not None:
            return f"Overleaf would not open this file: {reply[0] if reply else 'no answer'}"
        kind = reply[5] if len(reply) > 5 else KIND
        if kind != KIND:
            return f"Overleaf keeps this file as {kind}, which is not brought together here."
        ranges = (reply[4] if len(reply) > 4 else None) or {}
        comments = [{"t": str((c.get("op") or {}).get("t")), "p": int((c.get("op") or {}).get("p", 0)),
                     "n": ot.js_len(_decode(str((c.get("op") or {}).get("c", ""))))}
                    for c in ranges.get("comments") or [] if isinstance(c, dict)]
        return "\n".join(_decode(str(line)) for line in reply[1]), int(reply[2]), comments

    async def _read_all(self) -> None:
        session = await self._session()
        _, docs, main = self._editor()
        for doc_id, path in docs.items():
            doc = self.docs.get(doc_id)
            if doc is None or doc.path != path:
                doc = Doc(doc_id, path)
                try:
                    self._file(doc)
                except ValueError:
                    continue
                self._load(doc)
                self.docs[doc_id] = doc
            read = await self._open(session, doc_id, main)
            if isinstance(read, tuple):
                self._theirs[doc_id] = read
                if doc.status in ("unsupported", "stopped"):
                    doc.status = "joining"
            else:
                self._theirs.pop(doc_id, None)
                self._set(doc, "unsupported" if "kind" in (read or "") or "keeps" in (read or "") else "stopped",
                          read or "")
        for doc_id in [key for key in self.docs if key not in docs]:
            del self.docs[doc_id]
            self._theirs.pop(doc_id, None)
        self.read_once = True
        self._recount()

    def _recount(self) -> None:
        if self.uploads is not None:
            before = [upload.view() for upload in self.uploads.files.values()]
            self.uploads.recount()
            if [upload.view() for upload in self.uploads.files.values()] != before:
                self._changed()
        for doc in self.docs.values():
            if doc.doc_id not in self._theirs:
                continue
            theirs, version, comments = self._theirs[doc.doc_id]
            disk = self._read(doc)
            if not doc.known:
                if disk is None:
                    self._set(doc, "get")
                elif disk == theirs:      # the same text: both agree on it from here on
                    doc.version, doc.server, doc.local = version, theirs, theirs
                    self._save(doc)
                    self._set(doc, "same")
                else:
                    self._set(doc, "differs", "This file and Overleaf's differ, and nothing says which "
                                              "changed. Choose which stands.")
                continue
            base = doc.server
            there, here = theirs != base, disk is not None and disk != base
            if disk is None:
                self._set(doc, "gone", "Deleted here, and still on Overleaf.")
            elif there and here:
                if merge(base, theirs, disk) is None:
                    self._set(doc, "differs", "This file and Overleaf changed the same place. Choose which stands.")
                else:
                    self._set(doc, "both")
            elif there:
                self._set(doc, "get")
            elif here:
                open_comments = [c for c in comments if c["t"] not in self.resolved_threads]
                lost = ot.comments_removed(open_comments, ot.diff(base, disk, open_comments))
                if lost:
                    self._set(doc, "held", "This edit deletes every word of an open Overleaf comment, so Send "
                                           "leaves it. Send it anyway, or put Overleaf's text back.", lost)
                else:
                    self._set(doc, "send")
            else:
                self._set(doc, "same")

    # ----- Sending -----

    async def _send(self, session: Session, doc: Doc) -> None:
        theirs, version, comments = self._theirs[doc.doc_id]
        disk = self._read(doc)
        if disk is None or theirs != doc.server:
            return
        open_comments = [c for c in comments if c["t"] not in self.resolved_threads]
        op = ot.diff(doc.server, disk, open_comments)
        reply = await session.call("applyOtUpdate", [doc.doc_id, {"doc": doc.doc_id, "op": op, "v": version,
                                                                  "hash": text_hash(disk)}])
        if reply and reply[0] is not None:
            error = reply[0].get("message") if isinstance(reply[0], dict) else reply[0]
            self._set(doc, "stopped", f"Overleaf did not take the edit to this file ({error}). Nothing was "
                                      "changed there. Send again, or choose which text stands.")
            return
        # Overleaf took it: read it back, so the record says only what Overleaf holds.
        _, _, main = self._editor()
        read = await self._open(session, doc.doc_id, main)
        if not isinstance(read, tuple):
            self._set(doc, "stopped", read or "Overleaf could not be read after the edit.")
            return
        self._theirs[doc.doc_id] = read
        if read[0] == disk:
            doc.version, doc.server, doc.local = read[1], disk, disk
            self._save(doc)

    def _set(self, doc: Doc, status: str, message: str = "", threads: list[str] | None = None) -> None:
        if (doc.status, doc.message, doc.threads) != (status, message, threads or []):
            doc.status, doc.message, doc.threads = status, message, threads or []
            try:
                self._changed()
            except Exception:  # the page's trouble is not the files'
                logger.exception("Overleaf files listener failed")


class _ChangedMeanwhile(Exception):
    """The file changed after it was last read."""


def merge(base: str, theirs: str, mine: str) -> tuple[str, ot.Op] | None:
    """Two texts changed from *base* brought together: the text holding both sides' edits,
    and my edit moved past theirs, which makes that text from *theirs*. None when both
    changed the same place."""
    their_edit, my_edit = ot.diff(base, theirs), ot.diff(base, mine)
    if my_edit and their_edit and _overlap(base, theirs, mine):
        return None
    moved_mine, moved_theirs = ot.transform_x(my_edit, their_edit)
    return ot.apply(mine, moved_theirs), moved_mine


def _span(base: str, text: str) -> tuple[int, int]:
    """Where in *base* the change to *text* lies, from the first changed character to the last."""
    start, limit = 0, min(len(base), len(text))
    while start < limit and base[start] == text[start]:
        start += 1
    end = 0
    while end < limit - start and base[len(base) - 1 - end] == text[len(text) - 1 - end]:
        end += 1
    return start, len(base) - end


def _overlap(base: str, first: str, second: str) -> bool:
    """Whether two texts changed from *base* changed the same stretch of it, counting
    everything between a text's first and last change, and two changes that touch."""
    (a, b), (c, d) = _span(base, first), _span(base, second)
    return a <= d and c <= b
