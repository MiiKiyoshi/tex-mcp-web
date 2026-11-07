"""The paper's files kept the same as its Overleaf project, both ways, as edits.

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

from . import overleaf_ot as ot

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


class ProjectSync:
    """Every document of the project that is a file here, kept the same both ways."""

    def __init__(self, root: Path, changed: Callable[[], Any],
                 written: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.root = root.resolve()
        self.state_dir = self.root / ".tex-mcp-web" / "overleaf-sync"
        self._changed = changed
        self._written = written
        self.docs: dict[str, Doc] = {}
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

    def snapshot(self) -> list[dict]:
        return [doc.view() for doc in sorted(self.docs.values(), key=lambda doc: doc.path)]

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
        kept = self.state_dir / "kept" / f"{doc.path}.{which}.{time.strftime('%Y%m%d-%H%M%S')}"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(text, encoding="utf-8")
        return kept

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
        their_edit, my_edit = ot.diff(base, theirs), ot.diff(base, mine)
        self._set_first(doc, theirs, version, comments)
        doc.local = mine
        if my_edit and their_edit and _overlap(base, theirs, mine):
            self._set(doc, "differs", "This file and Overleaf changed the same place while apart. "
                                      "Choose which stands.")
            return
        moved_mine, moved_theirs = ot.transform_x(my_edit, their_edit)
        doc.pending = moved_mine
        merged = ot.apply(mine, moved_theirs)
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

    async def _decide(self, path: str, choice: str) -> str | None:
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


class _ChangedMeanwhile(Exception):
    """The file changed after it was last read."""


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
