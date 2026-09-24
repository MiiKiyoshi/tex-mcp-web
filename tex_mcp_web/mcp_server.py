"""MCP server for tex-mcp-web v0.7.0.

Exposes tools to agents via stdio:

    read_comments(...)      what is new, selected threads in full, or a listing
    write_comments(...)     add/reply/suggest/withdraw/edit/delete
    compile()               recompile, return structured errors
    image(...)              render a PDF page or exact region
    listen()                instructions for receiving review events

Source is read with the agent's own file tools; each comment names its file and
line range.

The MCP process owns the review server: the first tool call starts it in a
background thread, and a peer process bound to the same project shares that
listener. ``compile`` reaches it over HTTP so compilation, PDF refresh, anchor
reattachment, and viewer notification remain one transaction.

Requires: pip install "mcp>=1.0"  (and httpx for compile)
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

try:
    from mcp.server.fastmcp import Context, FastMCP
    from mcp.types import ImageContent, TextContent
    from pydantic import BaseModel, ConfigDict, Field, model_validator

    from .mcp_client import INIT_GUIDE, ProjectBinding, ProjectSetupError

    HAS_MCP = True
    MISSING_MCP: ImportError | None = None
except ImportError as error:
    # Kept, not just noted: mcp 2.x fails this import with a message naming the rename
    # and the pin that fixes it, which "not installed" would hide.
    HAS_MCP = False
    MISSING_MCP = error


if HAS_MCP:
    class _InputModel(BaseModel):
        model_config = ConfigDict(extra="forbid")


    class PaperAnchorInput(_InputModel):
        kind: Literal["paper"]


    class SectionAnchorInput(_InputModel):
        kind: Literal["section"]
        title: Annotated[str, Field(min_length=1)]
        label: str | None = None


    class SourceRangeAnchorInput(_InputModel):
        kind: Literal["source_range"]
        file: Annotated[str, Field(min_length=1)]
        start: Annotated[int, Field(ge=1, description="First line, counting from 1")]
        end: Annotated[int, Field(ge=1, description="Last line, included")]

        # Whole lines only. Counting characters to a column is what a model cannot do
        # reliably, and nothing needs it any more: a rewrite inside these lines is
        # proposed by quoting the text it replaces.
        @model_validator(mode="after")
        def validate_range(self):
            if self.end < self.start:
                raise ValueError("end must be at least start")
            return self


    class FragmentInput(_InputModel):
        """One piece of the comment's anchored text and what it becomes."""

        old: Annotated[str, Field(min_length=1, description="Exact text from the file the comment sits in; it must occur there once")]
        new: str


    # No area anchor: a rectangle on a page is something the reviewer drags, and an
    # agent has no way to arrive at PDF coordinates that mean anything.
    CommentAnchorInput = Annotated[
        PaperAnchorInput | SectionAnchorInput | SourceRangeAnchorInput,
        Field(discriminator="kind"),
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_deps() -> None:
    if not HAS_MCP:
        print(
            f"tex-mcp cannot import its MCP dependencies: {MISSING_MCP}\n"
            "Install tex-mcp-web[mcp] into this interpreter.",
            file=sys.stderr,
        )
        sys.exit(1)


def _load_project():
    """Resolve config + watch_dir + comment store from cwd."""
    from .comments import CommentStore
    from .config import get_watch_dir, load_config

    cfg = load_config()
    watch_dir = get_watch_dir(cfg)
    store = CommentStore(watch_dir / ".tex-mcp-web" / "comments.json")
    return cfg, watch_dir, store


def revision_of(updated: str) -> str:
    """A short token standing for a thread's ``updated`` stamp.

    An agent hands this back to say which version of a thread it read. Nothing reads
    its parts, so it is eight characters rather than a timestamp's thirty-two.
    """
    import hashlib

    return hashlib.sha256(updated.encode("utf-8")).hexdigest()[:8]


def short_time(stamp: str) -> str:
    """A stored ISO stamp as the server's own clock reads it: ``MM-DD HH:MM``.

    Nothing an agent does with a time needs the second, the microsecond or the offset;
    it reads when something was said and hands the same string back to ask for what came
    after. Eleven characters instead of thirty-two, and no arithmetic between zones.
    """
    return datetime.fromisoformat(stamp).astimezone().strftime("%m-%d %H:%M")


def parse_short_time(value: str) -> datetime:
    """Read back a ``MM-DD HH:MM`` the server itself printed.

    The year is this one, or the last one when that would put the date in the future, so
    a stamp copied out of a reply keeps meaning the moment it named.
    """
    try:
        month_day, clock = value.strip().split(" ", 1)
        month, day = (int(part) for part in month_day.split("-"))
        hour, minute = (int(part) for part in clock.split(":"))
    except ValueError:
        raise ValueError(f"a time reads as 'MM-DD HH:MM', like '09-19 00:52': {value!r}") from None
    here = datetime.now().astimezone().tzinfo
    now = datetime.now(tz=here)
    moment = datetime(now.year, month, day, hour, minute, tzinfo=here)
    return moment.replace(year=now.year - 1) if moment > now else moment


def _cursor_path(watch_dir: Path) -> Path:
    return watch_dir / ".tex-mcp-web" / "agent-cursor.json"


def read_cursor(watch_dir: Path) -> str | None:
    """The moment this project's agent was last told about, or None."""
    try:
        return str(json.loads(_cursor_path(watch_dir).read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_cursor(watch_dir: Path, at: str) -> None:
    path = _cursor_path(watch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": at}), encoding="utf-8")


def _after(entry, moment: datetime | None) -> bool:
    if moment is None:
        return True
    at = datetime.fromisoformat(entry.at)
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) > moment


def _err(message: str) -> str:
    # Non-ASCII stays as itself: an escaped message is longer and harder to read.
    return json.dumps({"error": message}, ensure_ascii=False)


# How much of a request a listing shows before it is cut.
REQUEST_PREVIEW = 120

# How many errors one compile reports; the log holds the rest.
ERROR_LIMIT = 20


def _ok(payload: Any) -> str:
    # One line rather than indented: every byte here is read by a model, and indentation
    # carries nothing a parser or a reader needs.
    return json.dumps(payload, ensure_ascii=False)


# In-process SyncTeX cache for the MCP server.  Each call to image
# / _comment_add would otherwise re-parse the .synctex.gz from disk; on a
# 50+ page paper that is tens of MB of gzipped data per call.  Keyed by
# the synctex file's mtime so a recompile transparently invalidates.
_synctex_cache: dict[Path, tuple[float, Any]] = {}


def _load_synctex_cached(main_file: Path):
    """Load and cache SyncTeX for *main_file*'s rendered PDF.

    Cache key is the SyncTeX file path; cache entry is (mtime, data).
    A rebuild that bumps mtime invalidates the entry on next call.
    """
    from .server import load_synctex_for_main
    from .synctex import find_synctex_file

    pdf_path = main_file.with_suffix(".pdf")
    if not pdf_path.exists():
        return None
    synctex_path = find_synctex_file(pdf_path)
    if synctex_path is None:
        return None
    try:
        mtime = synctex_path.stat().st_mtime
    except OSError:
        return None
    cached = _synctex_cache.get(synctex_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = load_synctex_for_main(main_file)
    if data is not None:
        _synctex_cache[synctex_path] = (mtime, data)
    return data


def _agent_entry(entry) -> dict[str, Any]:
    """One thread entry as an agent reads it, with a time it can hand back."""
    written = {key: value for key, value in entry.to_dict().items()
               # Only the agent's own entries can be rewritten, so only those carry the
               # id to name them by.
               if key != "id" or entry.author == "agent"}
    written["at"] = short_time(entry.at)
    if "updated_at" in written:
        written["updated_at"] = short_time(written["updated_at"])
    return written


def _agent_comment_to_dict(comment, watch_dir: Path) -> dict[str, Any]:
    """Return only the comment information an agent can act on."""
    from .comments import AreaAnchor, SectionAnchor, SourceRangeAnchor, TextSelectionAnchor
    from .server import read_anchored_source

    anchor = comment.anchor
    payload: dict[str, Any] = {
        "id": comment.id,
        "status": comment.status,
        "kind": anchor.kind,
        "comment": comment.text,
    }
    if isinstance(anchor, TextSelectionAnchor):
        payload.update({
            "quote": anchor.quote,
            "page": anchor.selection.page,
        })
    elif isinstance(anchor, SourceRangeAnchor) and comment.status == "open":
        # What the file holds here now: the text a suggestion quotes its fragments out
        # of. A resolved or archived thread is read for what was said, not rewritten,
        # so it does not carry the paragraph with it.
        quote = read_anchored_source(watch_dir, comment)
        if quote is not None:
            payload["quote"] = quote
    elif isinstance(anchor, AreaAnchor):
        payload["page"] = anchor.page
    elif isinstance(anchor, SectionAnchor):
        payload["section"] = anchor.title
        if anchor.label is not None:
            payload["label"] = anchor.label
    if comment.resolved_source is not None:
        # The same "file:first-last" an edits entry names a changed range by, so one
        # spelling covers both directions instead of an object here and a string there.
        source = comment.resolved_source
        payload["source"] = f"{source.file}:{source.line_start}-{source.line_end}"
    if len(comment.thread) > 1:
        # Only the agent's own entries can be rewritten, so only those carry the id to name them by.
        payload["replies"] = [_agent_entry(entry) for entry in comment.thread[1:]]
    # What a write must quote back to prove it read the thread as it stands. It moves
    # with every change, and it is nothing but a token to return.
    payload["rev"] = revision_of(comment.updated)
    if comment.suggestion is not None:
        # The pieces the thread proposes changing. A thread holds one proposal, so
        # reading it never costs the ones it used to carry.
        payload["suggestion"] = [
            {"old": old, "new": new} for old, new in comment.suggestion.changes
        ]
    if comment.stale:
        payload["stale"] = True
    return payload


def _comment_add(
    store,
    cfg,
    watch_dir: Path,
    text: str | None,
    anchor: "CommentAnchorInput | None",
) -> str:
    """Implementation of ``write_comments(action="add", ...)``.

    Source ranges and sections receive the same source selectors as browser
    comments.
    """
    from .comments import (
        ResolvedSource,
        anchor_from_dict,
        capture_source_selector,
    )
    from .config import get_main_file
    from .server import (
        resolve_section_to_source,
    )
    from .structure import parse_structure

    if not text or not anchor:
        return _err("add requires text and anchor")

    resolved: ResolvedSource | None = None
    source_selector = None
    anchor_data = anchor.model_dump(exclude_none=True)
    kind = anchor_data["kind"]

    if kind == "source_range":
        file = anchor_data["file"]
        ls = int(anchor_data.pop("start"))
        le = int(anchor_data.pop("end"))
        # The store spells a range line_start/line_end; the tool asks for it once.
        anchor_data["line_start"], anchor_data["line_end"] = ls, le
        source_selector = capture_source_selector(watch_dir / file, ls, le)
        if source_selector is None:
            return _err("source_range does not identify readable source lines")
        resolved = ResolvedSource(file=file, line_start=ls, line_end=le)
    elif kind == "section":
        resolved = resolve_section_to_source(
            parse_structure(watch_dir, get_main_file(cfg)),
            watch_dir,
            anchor_data["title"],
            anchor_data["label"] if "label" in anchor_data else None,
        )

    a = anchor_from_dict(anchor_data)

    comment = store.add(
        anchor=a,
        text=text,
        author="agent",
        resolved_source=resolved,
        source_selector=source_selector,
    )
    return _ok({"id": comment.id, "status": comment.status, "rev": revision_of(comment.updated)})


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def _wait_method(ctx: "Context") -> str:
    name = ctx.session.client_params.clientInfo.name.casefold()
    if "claude" in name:
        return (
            "Run the script with Monitor(command=<script>, persistent=true, "
            "timeout_ms=3600000), then end the turn. Keep the monitor for subsequent events."
        )
    if "codex" in name:
        return (
            'Run sh <quoted-script-path> --codex "$CODEX_THREAD_ID" with '
            'exec_command(yield_time_ms=1000, sandbox_permissions="require_escalated", '
            'justification="Allow the TeX review waiter to deliver events to this Codex thread?"). '
            "Once running, end the turn; do not poll. "
            "The script uses codex queue to deliver events as labeled user messages, "
            "including while idle. Delivery may take about 10 seconds. "
            "Requires codex queue on PATH and CODEX_THREAD_ID in the agent shell. "
            "Keep one waiter; stop its process when no longer needed."
        )
    return (
        "Run the script with your shell tool and read its output. If the tool returns a "
        "running session, retain it and use the tool that reads subsequent output. Keep "
        "the turn active while waiting unless your client explicitly supports resuming "
        "a completed turn from background output. After handling an event, resume "
        "waiting on the same process."
    )


def create_server(binding: "ProjectBinding") -> "FastMCP":
    _check_deps()
    mcp = FastMCP(
        "tex-mcp-web",
        instructions=(
            "Work from read_comments(new=True), which gives what the reviewer has "
            "written since you last asked, each thread with the rev a write quotes back; "
            "read_comments(ids=[...]) gives a thread whole when its conversation is no "
            "longer in mind. Read source with your own file tools. "
            "Within the user's editing scope, correct what a thread reports, compile() once "
            "after the batch, verify, and reply naming the ranges you changed. When the wording "
            "is the reviewer's to decide, put a suggestion on their own thread rather than "
            "opening another comment. Respect read-only or discussion-only requests, and do not "
            "repeat a thread reply in chat. Call listen() when the user asks to listen and follow "
            "how. Do not poll or duplicate it; unacknowledged presses stay queued. "
            f"Setup: read {INIT_GUIDE}."
        ),
    )

    @mcp.tool()
    async def read_comments(
        ids: list[str] | None = None,
        new: Annotated[bool, Field(description="Only what the reviewer has written since this call last answered; excludes ids.")] = False,
        status: Literal["open", "resolved", "archived", "all"] = "open",
        unanswered: Annotated[bool, Field(description="Only threads whose latest entry is the human's.")] = False,
        since: Annotated[str | None, Field(description="A time this server printed, like '09-19 00:52'; only what came after it.")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        save: bool = False,
    ) -> str:
        """Read review threads: what is new, selected threads in full, or a listing.

        ``new`` answers a Call agent in one step: on open threads, what the reviewer has
        written that this has neither carried before nor already taken up, thread by
        thread, each with the rev a write quotes back. It reports the moment it read
        ``from`` and the one it moved to, so a turn that went wrong is taken again with
        since=<from>; to see again what was already answered, ask for the thread by id.

        ``ids`` gives those threads whole, which is what to ask for when the conversation
        is no longer in mind; with ``since`` it gives only the entries after that time.

        Without either, a listing: each request cut to its opening, the newest ``limit``
        threads with the rest counted in ``older``, narrowed by status, unanswered or
        since. Times read and are written back as ``MM-DD HH:MM`` on this server's clock.
        ``save`` needs ids; edit only the draft's Reply and Edit blocks.
        """
        _, watch_dir, store = _load_project()
        try:
            moment = parse_short_time(since) if since is not None else None
        except ValueError as error:
            return _err(str(error))

        if new:
            if ids is not None or save:
                return _err("new answers on its own; it takes no ids and writes no draft")
            previous = read_cursor(watch_dir)
            # The cursor is the server's own note to itself, kept exact; only what it
            # shows an agent is cut to the minute.
            edge = moment
            if edge is None and previous is not None:
                edge = datetime.fromisoformat(previous)
                if edge.tzinfo is None:
                    edge = edge.replace(tzinfo=timezone.utc)
            fresh, latest = [], previous
            # Open threads only. A resolved or archived one is not where a new request
            # arrives; the reviewer reopens it first, and it comes back open.
            for comment in store.list(status="open"):
                # Unread and unanswered, both. The cursor says what has been carried
                # before; the last thing this agent said in the thread says what it has
                # already taken up. Asked for the first time there is no cursor, and the
                # second half alone is what keeps that call off the whole history.
                answered = max((position for position, entry in enumerate(comment.thread)
                                if entry.author == "agent"), default=-1)
                said = [entry for entry in comment.thread[answered + 1:]
                        if entry.author == "human" and _after(entry, edge)]
                if not said:
                    continue
                told = {"id": comment.id, "rev": revision_of(comment.updated),
                        "status": comment.status,
                        "entries": [_agent_entry(entry) for entry in said]}
                if comment.resolved_source is not None:
                    source = comment.resolved_source
                    told["source"] = f"{source.file}:{source.line_start}-{source.line_end}"
                from .server import read_anchored_source

                quote = read_anchored_source(watch_dir, comment)
                if quote is not None:
                    told["quote"] = quote
                fresh.append(told)
                newest = max(entry.at for entry in said)
                latest = newest if latest is None or newest > latest else latest
            if latest is None:
                # A quiet first call still fixes where the next one starts.
                latest = datetime.now().astimezone().isoformat()
            if latest != previous:
                write_cursor(watch_dir, latest)
            answer: dict[str, Any] = {"comments": fresh}
            if previous is not None:
                answer["from"] = short_time(previous)
            if latest is not None:
                answer["cursor"] = short_time(latest)
            return _ok(answer)

        if ids is not None:
            if len(set(ids)) != len(ids):
                return _err("ids must be unique")
            if save:
                try:
                    return _ok(store.export_comments(ids))
                except (OSError, KeyError, TypeError, ValueError) as error:
                    return _err(str(error))
            comments = []
            for comment_id in ids:
                comment = store.get(comment_id)
                if comment is None:
                    return _err(f"comment not found: {comment_id}")
                told = _agent_comment_to_dict(comment, watch_dir)
                if moment is not None:
                    # Only what was said after that moment; the rest is already in mind.
                    told.pop("comment", None)
                    told["replies"] = [_agent_entry(entry) for entry in comment.thread
                                       if _after(entry, moment)]
                comments.append(told)
            return _ok({"comments": comments})
        if save:
            return _err("save needs the ids of the threads to write out")
        summaries = []
        for comment in store.list(status=None if status == "all" else status):
            if unanswered and comment.thread[-1].author != "human":
                continue
            human = next((entry for entry in reversed(comment.thread) if entry.author == "human"), None)
            if moment is not None and (human is None or not _after(human, moment)):
                continue
            request = (human if human is not None else comment.thread[0]).text
            summaries.append({
                "id": comment.id,
                # The token a write quotes back. Without it here, answering a thread costs
                # a full read of it first, and a thread grows.
                "rev": revision_of(comment.updated),
                "status": comment.status,
                "kind": comment.anchor.kind,
                # A listing is for picking which threads to open, so each request is cut
                # to as much as it takes to recognise it.
                "request": request[:REQUEST_PREVIEW] + "…" if len(request) > REQUEST_PREVIEW else request,
                "thread_entries": len(comment.thread),
                "last_human_at": short_time(human.at) if human is not None else None,
            })
        # The newest threads, still in the order the paper reads in: a cap that dropped
        # them would hide exactly the requests the reviewer has just written.
        listed = {"comments": summaries[-limit:]}
        if len(summaries) > limit:
            listed["older"] = len(summaries) - limit
        return _ok(listed)

    @mcp.tool()
    async def compile() -> str:
        """Recompile. Returns whether it succeeded, which pages changed, where the
        log is, and, when it failed, where each error is.

        Call once after a batch of source edits. ``pages_changed``
        compares extracted PDF text and excludes visual-only changes. Read the log
        yourself for warnings and for an error's surroundings.
        """
        try:
            import httpx
        except ImportError:
            return _err("httpx not installed; install tex-mcp-web[mcp]")

        from .config import get_main_file

        try:
            base = binding.base_url()
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(f"{base}/compile")
        except Exception as exc:
            return _err(f"review server request failed: {exc}")
        if response.status_code != 200:
            return _err(
                f"compile failed with HTTP {response.status_code}: {response.text}"
            )
        result = response.json()
        cfg, _, _ = _load_project()
        # The log is a file on disk and the source lines around an error are readable
        # with any file tool, so neither is copied into the answer. Warnings are a
        # count: a paper writes a paragraph per line, and five lines of context around
        # each overfull box costs more than everything else this server sends.
        report: dict[str, Any] = {
            "success": result["success"],
            "pages_changed": result["pages_changed"],
            "warnings": len(result["warnings"]),
            "log": str(get_main_file(cfg).with_suffix(".log")),
        }
        if not result["success"]:
            report["errors"] = [
                {"at": f"{error['file']}:{error['line']}" if error["line"] else error["file"],
                 "message": error["message"]}
                for error in result["errors"][:ERROR_LIMIT]
            ]
            if len(result["errors"]) > ERROR_LIMIT:
                report["more_errors"] = len(result["errors"]) - ERROR_LIMIT
        return _ok(report)

    @mcp.tool()
    async def write_comments(
        action: Literal["add", "reply", "suggest", "withdraw", "edit", "delete"],
        id: str | None = None,
        text: str | None = None,
        anchor: CommentAnchorInput | None = None,
        edits: list[str] | None = None,
        changes: list[FragmentInput] | None = None,
        draft: str | None = None,
        entry: str | None = None,
        rev: str | None = None,
    ) -> str:
        """Write to a comment thread.

        add: text, anchor. reply: id, text, or a saved draft alone. suggest: id, text,
        changes, rev; each change's old is quoted from the file that comment sits in and
        must occur there once, the way an editing tool takes it, never a line or a
        column. It replaces the thread's one live proposal and adds text as a reply, so
        the conversation stays whole, and it is refused on a thread the reviewer has not
        written in: propose on theirs rather than opening your own. withdraw: id, text,
        rev. edit: id, entry, text, rev, rewriting an entry you wrote. delete: id,
        refused once the reviewer has written in the thread.

        rev is the thread's token from read_comments; a thread that moved on refuses the
        write. edits names the source ranges a reply changed. The reviewer resolves
        threads, not you. Returns id, status and rev.
        """
        cfg, watch_dir, store = _load_project()

        def receipt(comment) -> str:
            return _ok({"id": comment.id, "status": comment.status,
                        "rev": revision_of(comment.updated)})

        def stamp_for(comment_id: str) -> str:
            """The stored stamp the given rev stands for, or a refusal if it moved on."""
            target = store.get(comment_id)
            if target is None:
                raise KeyError(comment_id)
            if revision_of(target.updated) != rev:
                raise ValueError("stale thread: comment changed since it was read")
            return target.updated

        try:
            if draft is not None:
                if action != "reply" or any(value is not None for value in (id, text, anchor, changes)):
                    return _err("draft requires reply and excludes inline comment fields")
                return _ok({"written": [{"id": c.id, "status": c.status,
                                         "rev": revision_of(c.updated)}
                                        for c in store.reply_file(draft, edits=edits)]})
            if action == "add":
                return _comment_add(store, cfg, watch_dir, text, anchor)
            if action == "reply":
                if not id or not text:
                    return _err("reply requires id and text")
                return receipt(store.reply(id, text=text, author="agent", edits=edits or []))
            if action == "suggest":
                if not id or not text or not changes or not rev:
                    return _err("suggest requires id, text, changes and rev")
                from .server import derive_suggestion

                pairs = [(change.old, change.new) for change in changes]
                return receipt(store.suggest(
                    id, stamp_for(id), text,
                    lambda comment: derive_suggestion(watch_dir, comment, pairs),
                ))
            if action == "withdraw":
                if not id or not text or not rev:
                    return _err("withdraw requires id, text and rev")
                return receipt(store.withdraw_suggestion(id, stamp_for(id), text))
            if action == "edit":
                if not id or not entry or not text or not rev:
                    return _err("edit requires id, entry, text and rev")
                return receipt(store.edit_agent_entries([(id, entry, text)], {id: stamp_for(id)})[0])
            if action == "delete":
                if not id:
                    return _err("delete requires id")
                target = store.get(id)
                if target is None:
                    return _err(f"comment not found: {id}")
                if any(written.author == "human" for written in target.thread):
                    return _err(
                        "the reviewer has written in this thread; withdraw the suggestion "
                        "or reply instead of deleting what they said"
                    )
                return _ok({"deleted": id, "ok": store.delete(id)})
            return _err(f"unknown action: {action}")
        except KeyError as exc:
            return _err(f"comment not found: {exc}")
        except (ValueError, TypeError) as exc:
            return _err(str(exc))

    @mcp.tool()
    async def image(
        page: Annotated[int, Field(ge=1)] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        source: Annotated[
            str, Field(pattern=r"^.+:\d+(?:-\d+)?$")
        ] | None = None,
        comment: str | None = None,
        dpi: int = 150,
        margin: Annotated[float, Field(ge=0)] = 12.0,
    ) -> list[ImageContent | TextContent]:
        """Render one PDF target as PNG.

        Choose exactly one of page (with optional bbox),
        source="file.tex:lstart-lend", or comment. Margin expands an exact
        bbox only while rendering. A multi-page source range renders the page
        with the most SyncTeX matches. The metadata reports any grayscale or
        DPI reduction applied to fit the response size limit.
        """
        import base64

        from . import imaging
        from .config import get_main_file
        from .server import _clamp_dpi, _parse_source_range

        cfg, watch_dir, store = _load_project()
        pdf_path = get_main_file(cfg).with_suffix(".pdf")
        if not pdf_path.exists():
            return [TextContent(type="text",
                text=_err("no PDF on disk; start tex-web and wait for its initial compile"))]

        parsed_bbox: tuple[float, float, float, float] | None = None
        if bbox is not None:
            if len(bbox) != 4:
                return [TextContent(type="text",
                    text=_err("bbox must have exactly 4 values"))]
            parsed_bbox = (float(bbox[0]), float(bbox[1]),
                           float(bbox[2]), float(bbox[3]))
        parsed_source = _parse_source_range(source) if source else None

        clamped_dpi = _clamp_dpi(dpi)
        synctex = _load_synctex_cached(get_main_file(cfg))

        try:
            resolved_page, resolved_bbox = imaging.resolve_image_target(
                synctex=synctex,
                comment_lookup=store.get,
                page=page,
                bbox=parsed_bbox,
                source=parsed_source,
                comment_id=comment,
                watch_dir=watch_dir,
            )
            def render(dpi_val: int, gray_val: bool) -> bytes:
                if resolved_bbox is None:
                    return imaging.render_page(pdf_path, resolved_page, dpi_val, gray=gray_val)
                return imaging.render_region(
                    pdf_path,
                    resolved_page,
                    resolved_bbox,
                    dpi_val,
                    margin=margin,
                    gray=gray_val,
                )

            # Claude Code truncates MCP tool output around 25k tokens,
            # and base64 tokenizes at ~1.8 chars/token (measured: a 46k-
            # char payload tripped the cap) — so budget ~36k chars.
            # Grayscale first: it halves a text page's PNG at full
            # resolution; only then trade DPI (PNG size ~ dpi^2).
            MAX_B64_CHARS = 36_000
            gray = False
            png = await asyncio.to_thread(render, clamped_dpi, gray)
            b64 = base64.b64encode(png).decode("ascii")
            for _ in range(5):
                if len(b64) <= MAX_B64_CHARS:
                    break
                if not gray:
                    gray = True
                elif clamped_dpi > 30:
                    clamped_dpi = max(30, int(clamped_dpi * (MAX_B64_CHARS / len(b64)) ** 0.5 * 0.9))
                else:
                    break
                png = await asyncio.to_thread(render, clamped_dpi, gray)
                b64 = base64.b64encode(png).decode("ascii")
        except (ValueError, imaging.ImagingError) as exc:
            return [TextContent(type="text", text=_err(str(exc)))]

        meta = {
            "page": resolved_page,
            "bbox": list(resolved_bbox) if resolved_bbox else None,
            "dpi": clamped_dpi,
            "margin": margin if resolved_bbox else None,
            "grayscale": gray,
        }
        return [
            ImageContent(type="image", data=b64, mimeType="image/png"),
            TextContent(type="text", text=json.dumps(meta)),
        ]

    @mcp.tool()
    async def listen(ctx: Context) -> str:
        """Return a script and client-specific instructions for listening for Call agent.

        Run the returned script using the how field, selected for the connected
        client. Reuse the process after handling each review event.
        """
        _, watch_dir, _ = _load_project()
        try:
            port = binding.require_shared().port
        except ProjectSetupError as error:
            raise RuntimeError(
                f"{error}. If another project holds the port, propose a free port to the user, "
                "edit port in that config after they agree, and call listen() again."
            ) from error
        # The server keeps the press count and the consumption watermark, so the script
        # carries no state of its own: a press made before this call answers it at once,
        # and after a line is delivered the loop parks again rather than replaying the
        # press it acked. The ack comes after the line is printed, so a press is offered
        # until a waiter confirms it landed: delivery is at-least-once.
        script = (
            "#!/bin/sh\n"
            "thread=\n"
            "if [ \"${1-}\" = \"--codex\" ]; then\n"
            "  thread=${2:?Pass the Codex thread ID}\n"
            "  command -v codex >/dev/null || exit 1\n"
            "fi\n"
            "deliver() {\n"
            "  if [ -n \"$thread\" ]; then\n"
            "    until codex queue --thread \"$thread\" --message \"[TeX review event]\n"
            "$1\"; do\n"
            "      printf '%s\\n' 'Queue delivery failed; retrying in 5 seconds' >&2\n"
            "      sleep 5\n"
            "    done\n"
            "  else\n"
            "    printf '%s\\n' \"$1\"\n"
            "  fi\n"
            "}\n"
            f"# Prints one line each time the reviewer presses 'Call agent' on http://127.0.0.1:{port},\n"
            "# at once if an unacknowledged press is waiting, and keeps waiting for the next. A server\n"
            "# that goes away is waited for too: one [gone] line, then [back] when it answers again.\n"
            "headers=$(mktemp) || exit 1\n"
            "trap 'rm -f \"$headers\"' EXIT\n"
            "gone=0\n"
            "delay=2\n"
            "while :; do\n"
            f"  out=$(curl -sf -D \"$headers\" --max-time 60 \"http://127.0.0.1:{port}/wait-review\"); rc=$?\n"
            "  if [ $gone -eq 1 ] && { [ $rc -eq 0 ] || [ $rc -eq 28 ]; }; then\n"
            "    deliver '[back] review server reachable again'; gone=0; delay=2\n"
            "  fi\n"
            "  if [ $rc -eq 0 ] && [ -n \"$out\" ]; then\n"
            "    deliver \"$out\"\n"
            "    press=$(tr -d '\\r' < \"$headers\" | sed -n 's/^[Xx]-[Pp]ress: *//p' | head -1)\n"
            f"    [ -n \"$press\" ] && curl -sf -X POST \"http://127.0.0.1:{port}/wait-review/ack?upto=$press\" >/dev/null\n"
            "  fi\n"
            "  if [ $rc -ne 0 ] && [ $rc -ne 28 ]; then\n"
            "    if [ $gone -eq 0 ]; then deliver \"[gone] review server unreachable (curl exit $rc); waiting for it\"; gone=1; fi\n"
            "    sleep $delay\n"
            "    [ $delay -lt 30 ] && delay=$((delay * 2))\n"
            "  fi\n"
            "done\n"
        )
        # Replaced atomically so a waiter started from the previous script keeps reading
        # the file it opened.
        directory = watch_dir / ".tex-mcp-web"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "wait-review.sh"
        staging = directory / "wait-review.sh.new"
        staging.write_text(script, encoding="utf-8")
        staging.chmod(0o755)
        staging.replace(target)
        return _ok({
            "script": str(target),
            "how": (
                _wait_method(ctx)
                + " Start another copy only after the previous process has ended. "
                "On [review], call read_comments(new=True) and handle the review. "
                "[gone] means the review server is unreachable; the script keeps retrying. "
                "[back] means it is reachable again."
            ),
        })

    return mcp


import re

# A LaTeX-style label: short alpha prefix + colon + identifier without spaces.
# Matches ``sec:methods``, ``eq:foo-bar``, ``thm:main``.  Does *not* match
# ``Introduction: A Survey`` (space) or filenames (long prefix / has dot).
_LABEL_LIKE = re.compile(r"^[a-zA-Z]{2,8}:[A-Za-z0-9_.\-:]+$")


def parse_goto_target(target: str, default_file: str) -> dict[str, Any]:
    """Convert a CLI goto target string into a request body for ``/goto``.

    Recognized forms (in order):
      ``pN``         -> ``{"page": N}``
      ``N``          -> ``{"line": N, "file": default_file}``
      ``FILE:N``     -> ``{"file": FILE, "line": N}``  (right-hand side digits)
      ``sec:foo``    -> ``{"label": "sec:foo"}``       (LaTeX label syntax)
      anything else  -> ``{"section": target}``
    """
    if target.startswith("p") and target[1:].isdigit():
        return {"page": int(target[1:])}
    if target.isdigit():
        return {"line": int(target), "file": default_file}
    if ":" in target and target.rsplit(":", 1)[1].isdigit():
        file, line = target.rsplit(":", 1)
        return {"file": file, "line": int(line)}
    if _LABEL_LIKE.match(target):
        return {"label": target}
    return {"section": target}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def check(start_dir: Path) -> list[str]:
    """Build the server the way stdio would and return the names of its tools.

    An install is judged before it is registered: this fails where serving would, with
    the same message, and needs no paper in start_dir.
    """
    _check_deps()
    server = create_server(ProjectBinding(start_dir))
    return sorted(tool.name for tool in asyncio.run(server.list_tools()))


def main(start_dir: Path | None = None) -> None:
    """Run the MCP server with stdio transport, serving the viewer alongside."""
    _check_deps()
    binding = ProjectBinding(Path.cwd() if start_dir is None else start_dir)
    # Only read the configuration here. Starting the review server walks the paper's
    # sources and stats hundreds of paths, which is quick against a warm filesystem and
    # slow against a cold one; doing it now would put that wait in front of the client's
    # handshake and lose the connection to a timeout. The first tool call that needs the
    # server starts it.
    try:
        binding.describe()
    except ProjectSetupError as error:
        print(f"tex-mcp-web project is not ready: {error}", file=sys.stderr)
    mcp = create_server(binding)
    try:
        asyncio.run(mcp.run_stdio_async())
    finally:
        binding.stop()
