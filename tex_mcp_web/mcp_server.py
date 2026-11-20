"""MCP server for tex-mcp-web.

Exposes tools to agents via stdio:

    guide()                 files to read in full before editing the paper
    read_comments(...)      what is new, selected threads in full, or a listing
    write_comments(...)     add/reply/suggest/withdraw/edit/delete
    compile()               compile, return structured errors
    image(...)              render a PDF page or exact region
    overleaf(...)           this folder against its Overleaf project, and bringing Overleaf's edits in
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
import os
import re
import secrets
import shlex
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from . import page_token, plugin_monitor, shared_file

try:
    from mcp.server.fastmcp import Context, FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
    from mcp.types import ImageContent, TextContent
    from pydantic import BaseModel, ConfigDict, Field, model_validator

    from .mcp_client import INIT_GUIDE, ProjectBinding, ProjectSetupError, SharedProjectServer

    def _compact_schema(node: Any, in_properties: bool = False) -> None:
        """Drop pydantic's generated titles and null defaults. A field that is itself
        named title stays, because it is a key of a properties mapping."""
        if isinstance(node, dict):
            if not in_properties:
                node.pop("title", None)
                if "default" in node and node["default"] is None:
                    del node["default"]
            for key, value in node.items():
                _compact_schema(value, in_properties=key == "properties" and not in_properties)
        elif isinstance(node, list):
            for item in node:
                _compact_schema(item)

    class _Server(FastMCP):
        """FastMCP with compact tool schemas that rejects arguments a tool does not
        take. Every client that loads a tool pays for its schema, and FastMCP would
        otherwise drop an unknown argument silently."""

        async def list_tools(self):
            tools = await super().list_tools()
            for tool in tools:
                tool.description = " ".join(tool.description.split())
                _compact_schema(tool.inputSchema)
            return tools

        async def call_tool(self, name, arguments):
            tool = next((t for t in await self.list_tools() if t.name == name), None)
            if tool is not None:
                accepted = list(tool.inputSchema["properties"])
                unknown = sorted(set(arguments) - set(accepted))
                if unknown:
                    raise ToolError(f"{name} does not take {', '.join(unknown)}. "
                                    f"It takes: {', '.join(accepted) or 'no arguments'}.")
            return await super().call_tool(name, arguments)

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

        old: Annotated[str, Field(min_length=1, description="Exact text from the file the comment is on; it must occur there exactly once")]
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
            "Install tex-mcp-web into this interpreter.",
            file=sys.stderr,
        )
        sys.exit(1)


def _load_project(binding: "ProjectBinding"):
    """The bound project's config, folder and comment store.

    Every tool uses the project the review page serves, even when a closer config
    appears later. The config file is read again, as the server reads it on a save.
    """
    from .comments import CommentStore
    from .config import load_config

    shared = binding.require_shared()
    cfg = load_config(shared.config_path)
    store = CommentStore(shared.watch_dir / ".tex-mcp-web" / "comments.json")
    return cfg, shared.watch_dir, store


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
        return str(json.loads(shared_file.read_text(_cursor_path(watch_dir)))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cursor_moment(watch_dir: Path) -> datetime | None:
    """The moment read_comments(new=True) last carried the reviewer's words up to, or None."""
    previous = read_cursor(watch_dir)
    if previous is None:
        return None
    edge = datetime.fromisoformat(previous)
    return edge if edge.tzinfo else edge.replace(tzinfo=timezone.utc)


def _moment(at: str) -> datetime:
    when = datetime.fromisoformat(at)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def write_cursor(watch_dir: Path, at: str) -> None:
    path = _cursor_path(watch_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written whole and then put in place: the page's server reads it meanwhile.
    staging = path.with_name(f"{path.name}.{os.getpid()}")
    staging.write_text(json.dumps({"at": at}), encoding="utf-8")
    shared_file.replace(staging, path)


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
# A search shows as much of a long entry around the words found, and so many of a
# thread's entries holding them, the newest ones: enough to tell the thread, which is read
# whole by its id.
MATCH_PREVIEW = 240
MATCHES_SHOWN = 3

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


def _stored_status(status: str) -> str | None:
    """The status the store holds the threads listed under *status* by: a hidden thread is
    stored open. None for all."""
    return None if status == "all" else "open" if status == "hidden" else status


def _on_overleaf(watch_dir: Path) -> set[str]:
    """The comments brought in from Overleaf threads."""
    from . import overleaf

    return {known["comment"] for known in overleaf.ledger(watch_dir).values()}


def _unplaced(watch_dir: Path, comment_id: str) -> str | None:
    """Where on Overleaf a comment from there is, while its words are not in the file here."""
    from . import overleaf
    from .server import _place_name

    return next((_place_name(known["place"]) for known in overleaf.ledger(watch_dir).values()
                 if known["comment"] == comment_id and "place" in known), None)


def _range_text(watch_dir: Path, spec: str) -> dict[str, str]:
    """What a "file:first-last" range of the source holds now, and the file's whole text."""
    from .server import _parse_source_range

    name, first, last = _parse_source_range(spec)
    file = (watch_dir / name).resolve()
    if not file.is_relative_to(watch_dir.resolve()):
        raise ValueError(f"{name} is not in the paper's folder")
    try:
        whole = file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{name} cannot be read") from error
    lines = whole.split("\n")
    if not 1 <= first <= last <= len(lines):
        raise ValueError(f"{spec} is outside {name}, which has {len(lines)} lines")
    return {"file": name, "text": "\n".join(lines[first - 1:last]), "whole": whole}


def _draft_ranges(watch_dir: Path, specs: list[str]) -> tuple[list["DraftRange"], dict[str, str]]:
    """The "file:first-last" ranges a draft describes, each holding what it holds now, and
    the texts of their files, from which later edits carry them as they carry a comment."""
    from .comments import DraftRange, ResolvedSource, capture_source_selector
    from .server import _parse_source_range

    ranges, texts = [], {}
    for spec in specs:
        held = _range_text(watch_dir, spec)
        _, first, last = _parse_source_range(spec)
        selector = capture_source_selector(watch_dir / held["file"], first, last)
        if selector is None:
            raise ValueError(f"{spec} does not identify readable source lines")
        ranges.append(DraftRange(held["text"], ResolvedSource(held["file"], first, last), selector))
        texts[held["file"]] = held["whole"]
    return ranges, texts


async def _upstream(binding: "ProjectBinding", method: str = "GET") -> dict[str, Any] | None:
    """Where the folder stands against its Overleaf project, as the review server last read it
    (GET) or reads it now (POST). None when the server does not answer."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=120.0, headers=page_token.headers()) as client:
            response = await client.request(method, f"{binding.base_url()}/overleaf/upstream")
    except Exception:
        return None
    return response.json() if response.headers.get("content-type", "").startswith("application/json") else None


def _upstream_line(upstream: dict[str, Any] | None) -> str | None:
    """One sentence on what keeps the folder from Overleaf's text before an edit, or None."""
    if not upstream:
        return None
    if upstream.get("behind"):
        count = upstream["behind"]
        return (f"Overleaf has {count} commit{'' if count == 1 else 's'} that this folder does not have yet. "
                'overleaf(action="get") brings them in. Do it before editing.')
    if upstream.get("get"):
        return (f"Overleaf has edits to {', '.join(upstream['get'])} that this folder does not have yet. "
                'overleaf(action="get") brings them in. Do it before editing.')
    if upstream.get("choose"):
        return f"{', '.join(upstream['choose'])} wait for the reviewer to choose between this text and Overleaf's."
    if upstream.get("apart"):
        return f"Not yet the same as on Overleaf: {', '.join(upstream['apart'])}."
    return None


def _agent_comment_to_dict(comment, watch_dir: Path) -> dict[str, Any]:
    """Return only the comment information an agent can act on."""
    from .comments import AreaAnchor, SectionAnchor, SourceRangeAnchor, TextSelectionAnchor
    from .server import read_anchored_source

    anchor = comment.anchor
    payload: dict[str, Any] = {
        "id": comment.id,
        "status": comment.shown_status,
        "kind": anchor.kind,
        "comment": comment.text,
    }
    if isinstance(anchor, TextSelectionAnchor):
        payload.update({
            "quote": anchor.quote,
            "page": anchor.selection.page,
        })
    elif isinstance(anchor, SourceRangeAnchor) and comment.shown_status == "open":
        # What the file holds here now: the text a suggestion quotes its fragments out
        # of. A resolved, archived or hidden thread is read for what was said, not
        # rewritten, so it does not carry the paragraph with it.
        quote = read_anchored_source(watch_dir, comment)
        if quote is not None:
            payload["quote"] = quote
    elif isinstance(anchor, AreaAnchor):
        # The box in PDF points, whole ones, for image(page, bbox) or image(comment=id).
        payload["page"] = anchor.page
        payload["bbox"] = [round(value) for value in anchor.bbox]
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
    if comment.id in _on_overleaf(watch_dir):
        payload["on_overleaf"] = True
        if (where := _unplaced(watch_dir, comment.id)) is not None:
            # Its words are in Overleaf's file and not yet in this one: the comment sits on
            # the paper and moves onto them once they are here.
            payload["overleaf_place"] = f"{where}, not found here"
    elif comment.for_overleaf:
        payload["new_thread"] = True
    if comment.overleaf_draft is not None:
        # The reply drafted for Overleaf, which the reviewer has not sent yet. A thread with a
        # draft and not on Overleaf is a new thread drafted for it, and one with no entries
        # has the draft alone. A first message the reviewer wrote says so.
        payload["overleaf_draft"] = comment.overleaf_draft.text
        if comment.overleaf_draft.mine:
            payload["overleaf_draft_by"] = "reviewer"
        if not comment.thread:
            del payload["comment"]
    if comment.stale:
        payload["stale"] = True
    return payload


def _comment_add(
    store,
    cfg,
    watch_dir: Path,
    text: str | None,
    anchor: "CommentAnchorInput | None",
    changes: "list[FragmentInput] | None",
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
        derive_suggestion,
        resolve_section_to_source,
    )
    from .structure import AmbiguousSection, parse_structure

    if not text or not anchor:
        return _err("add requires text and anchor")

    resolved: ResolvedSource | None = None
    source_selector = None
    base_text = None
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
        base_text = (watch_dir / file).read_text(encoding="utf-8")
    elif kind == "section":
        try:
            resolved = resolve_section_to_source(
                parse_structure(watch_dir, get_main_file(cfg)),
                watch_dir,
                anchor_data["title"],
                anchor_data["label"] if "label" in anchor_data else None,
            )
        except AmbiguousSection as exc:
            return _err(f"{exc}. Give the label, or anchor on the heading's lines with source_range.")
        if resolved is not None:
            base_text = (watch_dir / resolved.file).read_text(encoding="utf-8")

    a = anchor_from_dict(anchor_data)
    suggestion = None
    if changes:
        # Measured before the thread exists, so a piece the file does not hold leaves no thread.
        suggestion = derive_suggestion(watch_dir, resolved, [(change.old, change.new) for change in changes])

    comment = store.add(
        anchor=a,
        text=text,
        author="agent",
        resolved_source=resolved,
        source_selector=source_selector,
        suggestion=suggestion,
        # The text the lines or the heading were just found in, which later edits carry the
        # comment from.
        base_text=base_text,
    )
    return _ok({"id": comment.id, "status": comment.status, "rev": revision_of(comment.updated)})


def _overleaf_thread_add(store, watch_dir: Path, text: str, anchor: "CommentAnchorInput",
                         edits: list[str]) -> str:
    """Implementation of ``write_comments(action="overleaf", anchor=...)``: a new thread
    drafted for Overleaf on source lines, which the reviewer sends."""
    from .comments import OverleafDraft, ResolvedSource, SourceRangeAnchor, capture_source_selector

    if anchor.kind != "source_range":
        return _err("a thread on Overleaf is anchored on words in a file: give a source_range anchor")
    held = _range_text(watch_dir, f"{anchor.file}:{anchor.start}-{anchor.end}")
    if not held["text"].strip():
        return _err("those lines are blank, and a thread on Overleaf must be anchored on words")
    selector = capture_source_selector(watch_dir / anchor.file, anchor.start, anchor.end)
    if selector is None:
        return _err("source_range does not identify readable source lines")
    ranges, texts = _draft_ranges(watch_dir, edits)
    comment = store.add_overleaf_thread(
        SourceRangeAnchor(file=anchor.file, line_start=anchor.start, line_end=anchor.end),
        OverleafDraft(text, ranges),
        ResolvedSource(file=anchor.file, line_start=anchor.start, line_end=anchor.end),
        selector,
        {**texts, held["file"]: held["whole"]},
    )
    return _ok({"id": comment.id, "status": comment.status, "rev": revision_of(comment.updated)})


# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------


def _is_gemini(ctx: "Context") -> bool:
    """Gemini CLI and Antigravity: a client that waits in Gemini's lane on the review page."""
    try:
        name = (ctx.session.client_params.clientInfo.name or "").casefold()
    except AttributeError:
        return False
    return any(tag in name for tag in ("gemini", "antigravity", "agy"))


def _wait_method(ctx: "Context", command: str) -> str:
    name = ctx.session.client_params.clientInfo.name.casefold()
    if _is_gemini(ctx):
        return (
            "Run <command> as a background shell command, then end the turn and do not poll. "
            "It shows Gemini as waiting on the review page and ends as soon as the reviewer "
            "presses Call agent for Gemini, printing the [review] line, which may end with "
            "[gemini: model=M effort=E], the model and effort the reviewer picked. After handling "
            "that press, start the command again; otherwise leave it stopped and tell the user "
            "in one line. Stop a running one when the review is finished or the user asks."
        )
    if "claude" in name:
        if plugin_monitor.hand_over(command):
            return (
                "The tex-mcp plugin's monitor runs the command for this session and turns each "
                "line it prints into a notification for as long as the session lasts. Do not "
                "start the command yourself, and end the turn."
            )
        return (
            'Run the command with Monitor(command=<command>, description="tex-mcp review", '
            "timeout_ms=1800000), then end the turn. When it expires, leave it stopped and tell the "
            "user in one line that listening has stopped and that they can ask again to resume it. "
            "Nothing is lost while it is stopped: new "
            "comments wait for read_comments(new=True), and a Call agent press waits for the next "
            "waiter. Stop an active Monitor when the review is finished or the user asks. "
            "Start another copy only after the previous process has ended."
        )
    if "codex" in name:
        return (
            'Run <command> --codex "$CODEX_THREAD_ID" with '
            'exec_command(yield_time_ms=1000, sandbox_permissions="require_escalated", '
            'justification="Allow the TeX review waiter to deliver events to this Codex thread?"). '
            "Once it is running, end the turn and do not poll. "
            "The waiter uses codex queue to deliver events as labeled user messages, "
            "even while the thread is idle; delivery may take about 10 seconds. "
            "It requires codex queue on PATH and CODEX_THREAD_ID in the agent's shell. "
            "Run one waiter at a time. "
            "Stop its process when the review is finished or the user asks. "
            "Start another copy only after the previous process has ended."
        )
    return (
        "Run the command with your shell tool and read its output. If the tool returns a "
        "running session, read the later output with the tool that reads from that session. "
        "After handling an event, stop the command; listening resumes when the user asks again. "
        "Start another copy only after the previous process has ended."
    )


# The id this process's session listens by. One per process, so a session asked to listen
# again restarts its waiter without that waiter hearing that another session took over.
LISTENER = secrets.token_hex(8)


def _waiter_command(port: int) -> str:
    """The command that waits for this session's Call agent presses, run as it is or by the
    plugin's monitor: Python, so that it is the same on every system."""
    return f"{shlex.quote(Path(sys.executable).as_posix())} -m tex_mcp_web.waiter {port} {LISTENER}"


def create_server(binding: "ProjectBinding") -> "FastMCP":
    _check_deps()
    mcp = _Server(
        "tex-mcp-web",
        instructions=(
            "Before editing the paper, call guide() and read every file in its read list in full. "
            "Work from read_comments(new=True), which returns what the reviewer has "
            "written since you last called it, each thread with the rev that a write passes back. "
            "read_comments(ids=[...]) returns whole threads, for when you no longer remember "
            "a conversation. Read source with your own file tools. "
            "Within the user's editing scope, fix what a thread reports, call compile() once "
            "after the batch, verify, and reply with edits naming the ranges you changed. When the "
            "reviewer should decide the wording, put a suggestion on their thread instead of "
            "opening another comment. Respect read-only or discussion-only requests. "
            "Answer a question or a request to explain with a reply in its thread, with or "
            "without an edit, including on a thread from Overleaf. "
            "Each entry in a thread is private or public. A private entry stays on the review "
            "page for the reviewer and you: your replies, your suggestions and what the reviewer "
            "writes on the page. A public entry is on Overleaf, for everyone in the project. You "
            "never send anything public: write_comments with action=\"overleaf\" drafts what should "
            "be public, and only the reviewer sends it. "
            "After replying, write in chat only what no thread carries, such as a failed tool or a "
            "choice left to the user, and do not repeat the replies or the edits they report. "
            "When the user asks you to listen, call listen() and follow its how. "
            "Do not poll or duplicate it. Unacknowledged presses stay queued. "
            "The config is in the startup directory, and its dir can point to the paper's folder anywhere. "
            f"To set up review or translation, read {INIT_GUIDE}."
        ),
    )
    # A path, not the list: the agent reads it with its own file tool, and the plugin's
    # hook checks that the Read returned it whole. Plain text, since structured output
    # would wrap the answer in {"result": ...} and the hook reads the answer as written.
    @mcp.tool(structured_output=False)
    async def guide() -> str:
        """Paths of the files to read in full before editing the paper: the paper's guideline and
        the guidelines it extends, base first, when guideline is set, and the terms its reference
        papers use, when references is set."""
        from .config import get_guideline_files
        from .terms import refresh

        cfg, _, _ = _load_project(binding)
        try:
            guidelines = get_guideline_files(cfg)
        except (OSError, ValueError) as error:
            return _err(str(error))
        terms = refresh(cfg)
        return _ok({"read": [str(path) for path in guidelines] + ([] if terms is None else [str(terms)])})

    @mcp.tool(structured_output=False)
    async def read_comments(
        ids: list[str] | None = None,
        new: Annotated[bool, Field(description="Only what the reviewer has written since the last call with new; cannot be combined with ids.")] = False,
        status: Literal["open", "resolved", "archived", "hidden", "all"] = "open",
        unanswered: Annotated[bool, Field(description="Only threads where the reviewer wrote to you after the thread's last answer: your reply, your Overleaf draft, or a reply sent to Overleaf.")] = False,
        since: Annotated[str | None, Field(description="A time this server printed, like '09-19 00:52'; only what came after it.")] = None,
        until: Annotated[str | None, Field(description="A time this server printed; a search covers only what came up to the end of that minute.")] = None,
        find: Annotated[str | None, Field(description="Words to search for, ignoring case, in a thread's entries and its draft for Overleaf.")] = None,
        on_overleaf: Annotated[bool | None, Field(description="true keeps only threads from Overleaf or started for it, false only the page's own threads, which are entirely private, and leaving it out keeps both. For a listing or a search.")] = None,
        file: Annotated[str | None, Field(description="A source file's name or path, like 'intro.tex' or 'tex/intro.tex', to keep only the threads on that file. For a listing or a search.")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        save: bool = False,
    ) -> str:
        """Read review threads: what is new, selected threads in full, or a listing.

        ``new`` answers a Call agent press in one call. For each open thread it returns what
        the reviewer has written that no earlier call with ``new`` returned and no answer has
        covered yet, with the thread's rev, which a write passes back. A thread's last answer
        covers everything before it; an answer is your reply, your Overleaf draft, or a public
        reply the reviewer sent to Overleaf. A suggestion the reviewer applied is returned
        once, as an entry marked applied with the lines it changed. Your wording is then in the
        file, and the entry needs no reply, except on a thread from Overleaf with no draft,
        where the change calls for a reply. A suggestion or an Overleaf draft the reviewer
        rejected is also returned once, as an entry marked rejected with the rejected text,
        and it stays in the thread for a later read by id. It needs no reply. Do not propose
        the same thing again unless the reviewer asks; if they give a reason, it is in one of
        their replies. The answer gives the moment the read started from (``from``) and the
        one it moved to (``cursor``), so a turn that went wrong can be repeated with
        since=<from>; to see again what was already answered, read the thread by id. A thread from Overleaf is marked on_overleaf, and one the reviewer
        started for Overleaf but has not sent there yet is marked new_thread; a thread with a
        draft for Overleaf comes with that draft.
        An entry is public, on Overleaf for everyone in the project, when it has ``name``
        (written there) or ``overleaf`` (sent there from the page), and so is ``comment``, the
        first message, of a thread marked on_overleaf. Every other entry is private, for the
        reviewer and you, and so is everything in a thread that is not on Overleaf.

        ``ids`` returns those threads in full, for when you no longer remember a
        conversation; with ``since`` it returns only the entries after that time.

        With neither, it returns a listing: the newest ``limit`` threads, each request
        shortened to its beginning and the rest counted in ``older``, narrowed by status,
        unanswered or since. With ``find`` or ``until`` it is a search instead, entry by
        entry: a thread is listed when something said in it between ``since`` and ``until``
        contains ``find``'s words, whether the reviewer's, yours, a public reply sent to
        Overleaf (marked ``overleaf``) or its draft for Overleaf, and it comes with those
        entries and its location. A long entry is shortened to the words around the match and
        marked ``cut``, and only a thread's newest few matches are shown, with the rest counted
        in ``more_matches``; read the thread by id to see it in full. The bounds and the words
        are each optional, and status="all" also searches closed threads. A thread from Overleaf
        the reviewer hid on the page has status "hidden": it is set aside, as an archived
        thread is, so new and the open listing leave it out. ``on_overleaf``
        limits a listing or a search to Overleaf's threads or to the page's own, which are
        entirely private, and ``file`` limits either to the threads on one source file, each
        with its lines in ``source``. Times are shown and passed back as ``MM-DD HH:MM`` on
        this server's clock. ``save`` requires ids; edit only the Reply and Edit blocks of the
        saved draft.
        """
        cfg, watch_dir, store = _load_project(binding)
        try:
            moment = parse_short_time(since) if since is not None else None
            # The minute named is whole: what came up to its end.
            end = parse_short_time(until) + timedelta(minutes=1) if until is not None else None
        except ValueError as error:
            return _err(str(error))
        if (find is not None or until is not None) and (new or ids is not None or save):
            return _err("find and until run a search and cannot be combined with new, ids or save")

        if file is not None and (new or ids is not None or save):
            return _err("file narrows a listing or a search and cannot be combined with new, ids or save")

        if new:
            if ids is not None or save:
                return _err("new cannot be combined with ids or save")
            previous = read_cursor(watch_dir)
            # The cursor is the server's own note to itself, kept exact; only what it
            # shows an agent is cut to the minute.
            edge = moment if moment is not None else cursor_moment(watch_dir)
            fresh, latest = [], previous
            brought = _on_overleaf(watch_dir)
            from . import overleaf as _overleaf

            # With overleaf_picked, a thread from Overleaf asks with its Overleaf words only
            # when the reviewer picked it at the press, and then whole and once.
            chosen = _overleaf.picked(watch_dir) if cfg.overleaf_picked else set()
            carried: set[str] = set()
            # Open threads only. A resolved or archived one is not where a new request
            # arrives; the reviewer reopens it first, and it comes back open.
            for comment in store.list(status="open"):
                # Unread and unanswered, both. The cursor says what has been carried
                # before; the thread's last answer says what has been taken up already.
                # Asked for the first time there is no cursor, and the second half alone
                # is what keeps that call off the whole history.
                if comment.id in chosen:
                    said = comment.waiting(None, True)
                    carried.add(comment.id)
                else:
                    said = comment.waiting(edge, comment.id in brought, not cfg.overleaf_picked)
                if not said:
                    continue
                told = {"id": comment.id, "rev": revision_of(comment.updated),
                        "status": comment.shown_status,
                        "entries": [_agent_entry(entry) for entry in said]}
                if comment.id in brought:
                    told["on_overleaf"] = True
                elif comment.for_overleaf:
                    told["new_thread"] = True
                if comment.overleaf_draft is not None:
                    # What the reviewer says may be about the draft, which they have not sent.
                    told["overleaf_draft"] = comment.overleaf_draft.text
                    if comment.overleaf_draft.mine:
                        told["overleaf_draft_by"] = "reviewer"
                if comment.resolved_source is not None:
                    source = comment.resolved_source
                    told["source"] = f"{source.file}:{source.line_start}-{source.line_end}"
                from .server import read_anchored_source

                quote = read_anchored_source(watch_dir, comment)
                if quote is not None:
                    told["quote"] = quote
                fresh.append(told)
                newest = max((entry.at for entry in said), key=_moment)
                # Moments, not their spelling: one written with another offset sorts wrong as text.
                latest = newest if latest is None or _moment(newest) > _moment(latest) else latest
            _overleaf.carried(watch_dir, carried)
            if latest is None:
                # A quiet first call still fixes where the next one starts.
                latest = datetime.now(timezone.utc).isoformat()
            if latest != previous:
                write_cursor(watch_dir, latest)
            answer: dict[str, Any] = {"comments": fresh}
            if cfg.overleaf and (line := _upstream_line(await _upstream(binding))) is not None:
                answer["overleaf"] = line
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
        brought = _on_overleaf(watch_dir)

        def wanted(comment) -> bool:
            """Whether the comment is on the side asked for: Overleaf's threads and those
            started or drafted for it, as the page's leaf tab lists them, or the page's own."""
            there = comment.id in brought or comment.for_overleaf or comment.overleaf_draft is not None
            source = comment.resolved_source
            # A bare name finds the file in whatever folder it is.
            if file is not None and (source is None or not f"/{source.file}".endswith(f"/{file.lstrip('./')}")):
                return False
            return on_overleaf is None or on_overleaf == there

        if find is not None or until is not None:
            words = (find or "").casefold()
            if find is not None and not words.strip():
                return _err("find needs the words to look for")

            def shown_cut(shown: dict[str, Any], text: str) -> dict[str, Any]:
                """A long entry as the words around what was found: a search is for finding
                the thread, which is then read by its id."""
                if len(text) <= MATCH_PREVIEW:
                    return shown
                at = max(text.casefold().find(words), 0)
                start = max(0, min(at - MATCH_PREVIEW // 3, len(text) - MATCH_PREVIEW))
                piece = text[start:start + MATCH_PREVIEW]
                piece = ("…" if start > 0 else "") + piece + ("…" if start + MATCH_PREVIEW < len(text) else "")
                return {**shown, "draft" if "draft" in shown else "text": piece, "cut": True}

            def within(at: str) -> bool:
                when = datetime.fromisoformat(at)
                when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
                return (moment is None or when > moment) and (end is None or when < end)

            found = []
            for comment in store.list(status=_stored_status(status)):
                if status != "all" and comment.shown_status != status:
                    continue
                if not wanted(comment):
                    continue
                said = [(_agent_entry(entry), entry.at, entry.text) for entry in comment.thread]
                if comment.overleaf_draft is not None:
                    draft = comment.overleaf_draft
                    said.append(({"draft": draft.text, "at": short_time(draft.at),
                                  **({"by": "reviewer"} if draft.mine else {})}, draft.at, draft.text))
                matches = [shown_cut(shown, text) for shown, at, text in said
                           if within(at) and words in text.casefold()]
                if not matches:
                    continue
                source = comment.resolved_source
                hit: dict[str, Any] = {"id": comment.id, "rev": revision_of(comment.updated), "status": comment.shown_status,
                                       "source": f"{source.file}:{source.line_start}-{source.line_end}"
                                       if source is not None else comment.anchor.kind,
                                       "matches": matches[-MATCHES_SHOWN:]}
                if len(matches) > MATCHES_SHOWN:
                    hit["more_matches"] = len(matches) - MATCHES_SHOWN
                if comment.id in brought:
                    hit["on_overleaf"] = True
                elif comment.for_overleaf:
                    hit["new_thread"] = True
                found.append(hit)
            listed = {"comments": found[-limit:]}
            if len(found) > limit:
                listed["older"] = len(found) - limit
            return _ok(listed)
        summaries = []
        for comment in store.list(status=_stored_status(status)):
            if (status != "all" and comment.shown_status != status) or not wanted(comment):
                continue
            if unanswered and not comment.waiting(on_overleaf=comment.id in brought):
                continue
            human = next((entry for entry in reversed(comment.thread) if entry.asks_agent), None)
            if moment is not None and (human is None or not _after(human, moment)):
                continue
            request = human.text if human is not None else comment.text
            summaries.append({
                "id": comment.id,
                # The token a write quotes back. Without it here, answering a thread costs
                # a full read of it first, and a thread grows.
                "rev": revision_of(comment.updated),
                "status": comment.shown_status,
                "kind": comment.anchor.kind,
                # A listing is for picking which threads to open, so each request is cut
                # to as much as it takes to recognise it.
                "request": request[:REQUEST_PREVIEW] + "…" if len(request) > REQUEST_PREVIEW else request,
                "thread_entries": len(comment.thread),
                "last_human_at": short_time(human.at) if human is not None else None,
                **({"source": f"{comment.resolved_source.file}:{comment.resolved_source.line_start}-"
                              f"{comment.resolved_source.line_end}"} if file is not None else {}),
            })
        # The newest threads, still in the order the paper reads in: a cap that dropped
        # them would hide exactly the requests the reviewer has just written.
        listed = {"comments": summaries[-limit:]}
        if len(summaries) > limit:
            listed["older"] = len(summaries) - limit
        return _ok(listed)

    @mcp.tool(structured_output=False)
    async def overleaf(action: Literal["state", "get", "push"] = "state") -> str:
        """Compare this folder with its Overleaf project. state reads Overleaf again and reports only what differs: get (Overleaf's edits
        not here), send (edits here not on Overleaf), choose (files waiting for the reviewer to
        choose), and, in a git clone, the commits behind and ahead. suggested lists the edits
        made on Overleaf with track changes on, which wait for the reviewer to accept or reject
        them on the page; Overleaf's text contains the words of a suggested insertion but not
        those of a suggested deletion. get brings Overleaf's edits into this folder (git pull in
        a git clone, Get otherwise); it changes only files here, and a conflict changes nothing.
        push sends a git clone's commits (git push). Without git, only the reviewer sends
        edits, from the page."""
        import httpx

        cfg, _, _ = _load_project(binding)
        if not cfg.overleaf:
            return _err("this paper names no Overleaf project")
        if action == "state":
            upstream = await _upstream(binding, "POST")
            return _ok(upstream) if upstream is not None else _err("the review server did not answer")
        upstream = await _upstream(binding)
        if upstream is None:
            return _err("the review server did not answer")
        if upstream.get("git"):
            route = {"get": "git/pull", "push": "git/push"}[action]
        elif action == "push":
            return _err("only a git clone can push. Without git, the reviewer sends edits from the page")
        elif upstream.get("by") == "live":
            return _ok({"by": "live", "note": "Overleaf's edits arrive in the files automatically"})
        elif "unread" in upstream:
            return _err(upstream["unread"])
        else:
            route = "files/get"
        try:
            async with httpx.AsyncClient(timeout=300.0, headers=page_token.headers()) as client:
                response = await client.post(f"{binding.base_url()}/overleaf/{route}")
        except Exception as exc:
            return _err(f"review server request failed: {exc}")
        if response.status_code != 200:
            try:
                return _err(response.json()["error"])
            except Exception:
                return _err(f"HTTP {response.status_code}")
        after = await _upstream(binding)
        return _ok(after if after is not None else {"done": action})

    @mcp.tool(structured_output=False)
    async def compile() -> str:
        """Compile the paper. Returns whether it succeeded, which pages changed, where the
        log is, and, when it failed, where each error is.

        Call once after a batch of source edits. ``pages_changed``
        compares extracted PDF text and excludes visual-only changes. Read the log
        yourself for warnings and for the context around an error.

        A successful build also reports how the paper fits a page limit, as the page's
        Problems tab does. ``body_end`` is the last line before the bibliography, with its
        page, its column and the number of columns, the space below it to the end of the text
        block in points and in lines (once the paper has full columns to measure by), and its
        source. ``short_lines`` lists the paragraphs whose last line is narrower than a quarter
        of its column, including those in a list item or in centered text such as a table's
        caption, each with its page, column, words, width and source; shortening such a
        paragraph removes that line. A source is the line that contains a
        paragraph's last words.
        """
        import httpx

        try:
            base = binding.base_url()
            async with httpx.AsyncClient(timeout=300.0, headers=page_token.headers()) as client:
                response = await client.post(f"{base}/compile")
        except Exception as exc:
            return _err(f"review server request failed: {exc}")
        if response.status_code != 200:
            return _err(
                f"compile failed with HTTP {response.status_code}: {response.text}"
            )
        result = response.json()
        cfg, watch_dir, _ = _load_project(binding)
        # The log is a file on disk and the source lines around an error are readable
        # with any file tool, so neither is copied into the answer. Warnings are a
        # count: a paper writes a paragraph per line, and five lines of context around
        # each overfull box costs more than everything else this server sends.
        report: dict[str, Any] = {
            "success": result["success"],
            "pages_changed": result["pages_changed"],
            "warnings": len(result["warnings"]),
            # Beside the paper the server built, which a changed dir in the config is not yet.
            "log": str((watch_dir / cfg.main).with_suffix(".log")),
        }
        # Where each line is printed on its page is for the review page, which points at it.
        placed = lambda at: {key: value for key, value in at.items() if key != "bbox"}
        report.update({key: placed(value) if key == "body_end"
                       else [placed(line) for line in value] if key == "short_lines" else value
                       for key, value in (result.get("layout") or {}).items()})
        if not result["success"]:
            report["errors"] = [
                {"at": f"{error['file']}:{error['line']}" if error["line"] else error["file"],
                 "message": error["message"]}
                for error in result["errors"][:ERROR_LIMIT]
            ]
            if len(result["errors"]) > ERROR_LIMIT:
                report["more_errors"] = len(result["errors"]) - ERROR_LIMIT
        return _ok(report)

    @mcp.tool(structured_output=False)
    async def write_comments(
        action: Literal["add", "reply", "suggest", "withdraw", "overleaf", "edit", "delete"],
        id: str | None = None,
        text: str | None = None,
        anchor: CommentAnchorInput | None = None,
        edits: list[str] | None = None,
        changes: list[FragmentInput] | None = None,
        draft: str | None = None,
        entry: str | None = None,
        rev: str | None = None,
    ) -> str:
        """Write to a comment thread. What you write is private, on the review page for the
        reviewer and you. overleaf drafts what becomes public, on Overleaf for everyone
        in the project, once the reviewer sends it.

        add: text and anchor, plus changes, quoted as for suggest, when the reviewer has asked
        for proposals left as comments; the new thread then carries that proposal. reply: id
        and text, or a saved draft on its own. suggest: id, text, changes, rev. Each change's
        old is quoted from the file the comment is on and must occur there exactly once, as an
        editing tool expects; never give a line or a column. It replaces the thread's single
        current proposal and adds text as a reply, so the conversation stays complete. It is
        refused on a thread the reviewer has not written in: propose on their thread instead
        of opening your own beside it. withdraw: id, text, rev. overleaf: id, text, rev, and
        edits naming the source ranges the reply describes, by their current lines; later
        edits move these ranges as they move a comment, so the draft does not need rewriting
        when lines shift. On a thread that came from Overleaf (on_overleaf), it drafts the
        public reply that the reviewer may send there. A thread has one draft, which this
        replaces, and only the reviewer sends it. A first message the reviewer wrote
        themselves (overleaf_draft_by: reviewer) is not replaced: your text is added to the
        thread as your private reply suggesting the wording. The reviewer is asked first when a file that
        contains those ranges differs between here and Overleaf. Since the draft is public
        once sent, word it for the people on Overleaf, and answer what the reviewer asks you
        in the thread with reply, which is private. With anchor (a source_range) instead of
        id and rev, overleaf drafts a new public thread for Overleaf on those lines, with text
        as its first message. The result is a thread that has the draft and is not on
        Overleaf. Call overleaf with its id and rev to redraft it. Only the reviewer sends it,
        and only once the current text of those ranges is on Overleaf. The reviewer may discuss the draft with you in the
        thread before sending it, and those entries are private. A thread the reviewer started
        for Overleaf (new_thread) is waiting for its first message there. What they wrote in
        it is private and meant for you, and overleaf with id and rev drafts the message they
        asked for, keeping the meaning of their words and using the register they asked for.
        edit: id, entry, text, rev; rewrites an entry you wrote. delete: id; refused once the
        reviewer has written in the thread.

        rev is the thread's token from read_comments; if the thread has changed since, the
        write is refused. edits names the source ranges a reply changed. A field the action
        does not take is refused. The reviewer resolves threads, not you. Returns id, status
        and rev.
        """
        # What each action reads, so a field it would not use is refused rather than dropped.
        takes = {
            "add": {"text", "anchor", "changes"},
            "reply": {"id", "text", "edits", "draft"},
            "suggest": {"id", "text", "changes", "rev"},
            "withdraw": {"id", "text", "rev"},
            "overleaf": {"id", "text", "anchor", "edits", "rev"},
            "edit": {"id", "entry", "text", "rev"},
            "delete": {"id"},
        }
        given = {"id": id, "text": text, "anchor": anchor, "edits": edits, "changes": changes,
                 "draft": draft, "entry": entry, "rev": rev}
        if stray := [name for name, value in given.items() if value is not None and name not in takes[action]]:
            return _err(f"{action} does not take {', '.join(stray)}")
        cfg, watch_dir, store = _load_project(binding)

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
                return _comment_add(store, cfg, watch_dir, text, anchor, changes)
            if action == "reply":
                if not id or not text:
                    return _err("reply requires id and text")
                return receipt(store.reply(id, text=text, author="agent", edits=edits or []))
            if action == "suggest":
                if not id or not text or not changes or not rev:
                    return _err("suggest requires id, text, changes and rev")
                from .server import derive_suggestion

                if (where := _unplaced(watch_dir, id)) is not None:
                    return _err(f"this thread is on Overleaf at {where}, and those words are not in the "
                                "file here yet. Once they are (a pull brings in Overleaf's edits), the thread "
                                "moves onto them and accepts a suggestion")
                pairs = [(change.old, change.new) for change in changes]
                return receipt(store.suggest(
                    id, stamp_for(id), text,
                    lambda comment: derive_suggestion(watch_dir, comment.resolved_source, pairs),
                ))
            if action == "withdraw":
                if not id or not text or not rev:
                    return _err("withdraw requires id, text and rev")
                return receipt(store.withdraw_suggestion(id, stamp_for(id), text))
            if action == "overleaf":
                from .comments import OverleafDraft

                if anchor is not None:
                    if id is not None or rev is not None or not text:
                        return _err("overleaf with anchor drafts a new thread: text and anchor, no id or rev")
                    return _overleaf_thread_add(store, watch_dir, text, anchor, edits or [])
                if not id or not text or not rev:
                    return _err("overleaf requires id, text and rev, or text and anchor for a new thread")
                target = store.get(id)
                drafted = target is not None and (target.overleaf_draft is not None or target.for_overleaf)
                if id not in _on_overleaf(watch_dir) and not drafted:
                    return _err("this comment did not come from Overleaf")
                ranges, texts = _draft_ranges(watch_dir, edits or [])
                written = store.draft_overleaf(id, stamp_for(id), OverleafDraft(text, ranges), texts)
                if written.overleaf_draft is not None and written.overleaf_draft.mine:
                    return _ok({"id": written.id, "status": written.status, "rev": revision_of(written.updated),
                                "note": "The reviewer wrote this thread's first message themselves, so it stays "
                                        "as they wrote it. Your wording was added to the thread as your private "
                                        "reply, for them to use."})
                return receipt(written)
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

    @mcp.tool(structured_output=False)
    async def image(
        page: Annotated[int, Field(ge=1)] | None = None,
        bbox: tuple[float, float, float, float] | None = None,
        source: Annotated[
            str, Field(pattern=r"^.+:\d+(?:-\d+)?$")
        ] | None = None,
        comment: str | None = None,
        dpi: Annotated[int, Field(description="96 is enough to read text. 150 or more shows fine detail and costs more tokens.")] = 96,
        margin: Annotated[float, Field(ge=0)] = 12.0,
        grayscale: Annotated[bool, Field(description="Set false when colour itself is being checked.")] = True,
    ) -> list[ImageContent | TextContent]:
        """Render one PDF target as PNG.

        Choose exactly one of page (with optional bbox),
        source="file.tex:lstart-lend", or comment. Margin expands an exact
        bbox only while rendering. A multi-page source range renders the page
        with the most SyncTeX matches.
        """
        import base64

        from . import imaging
        from .config import get_main_file
        from .server import _clamp_dpi, _parse_source_range

        cfg, watch_dir, store = _load_project(binding)
        pdf_path = get_main_file(cfg).with_suffix(".pdf")
        if not pdf_path.exists():
            return [TextContent(type="text",
                text=_err("no PDF on disk; start the review page with `tex-mcp-web serve` and wait for its initial compile"))]

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
            def render() -> bytes:
                if resolved_bbox is None:
                    return imaging.render_page(pdf_path, resolved_page, clamped_dpi, gray=grayscale)
                return imaging.render_region(
                    pdf_path, resolved_page, resolved_bbox, clamped_dpi, margin=margin, gray=grayscale)

            # The picture goes as an image block alone, which the client counts by its pixels
            # rather than by its base64 text, so it is sent as asked. The resolution sets its
            # tokens, and grayscale halves the PNG of a text page.
            b64 = base64.b64encode(await asyncio.to_thread(render)).decode("ascii")
        except (ValueError, imaging.ImagingError) as exc:
            return [TextContent(type="text", text=_err(str(exc)))]

        meta = {
            "page": resolved_page,
            "bbox": list(resolved_bbox) if resolved_bbox else None,
            "dpi": clamped_dpi,
            "margin": margin if resolved_bbox else None,
            "grayscale": grayscale,
        }
        return [
            ImageContent(type="image", data=b64, mimeType="image/png"),
            TextContent(type="text", text=json.dumps(meta)),
        ]

    @mcp.tool(structured_output=False)
    async def listen(ctx: Context) -> str:
        """Return a command and client-specific instructions for listening for Call agent
        presses.

        Run the returned command as its how field describes; how is written for the
        connected client. Stop it when the review is finished or the user asks.
        """
        try:
            shared = binding.require_shared()
        except ProjectSetupError as error:
            raise RuntimeError(
                f"{error}. If another project uses the port, propose a free port to the user, "
                "change port in that config once they agree, and call listen() again."
            ) from error
        _, watch_dir, _ = _load_project(binding)
        import httpx

        if _is_gemini(ctx):
            # Gemini waits in a lane of its own and takes no presses from the listening session.
            command = f"{shlex.quote(Path(sys.executable).as_posix())} -m tex_mcp_web.waiter {shared.port} - --agent gemini --once"
        else:
            session = plugin_monitor.claude_pid()
            async with httpx.AsyncClient(timeout=10.0, headers=page_token.headers()) as client:
                response = await client.post(f"{binding.base_url()}/wait-review/listen", params={
                    "listener": LISTENER, **({"session": session} if session is not None else {})})
            response.raise_for_status()
            command = _waiter_command(shared.port)
        return _ok({
            "command": command,
            "how": (
                _wait_method(ctx, command)
                + " On [review], call read_comments(new=True) and handle the review. "
                "[gone] means the review server is unreachable; the waiter keeps retrying. "
                "[back] means it is reachable again. [moved] means a newer waiter took over "
                "listening, in another session or in this one as the line says, and this waiter has ended."
            ),
        })

    @mcp.tool(structured_output=False)
    async def setup_info() -> str:
        """Report setup paths, the existing binding with the paper's folder it serves, the
        guidelines to choose from, the path of the tex-mcp-web command, whether translation
        is set up, and whether the reviewer's Overleaf cookie is saved, without connecting or
        changing files."""
        import yaml

        from .config import Config, find_config, list_guidelines, load_config, translation_command, user_config_file
        from .mcp_client import CLI

        discovered = find_config(binding.start_dir)
        bound = binding.bound_config_path()
        # The language from the paper's config, the command from it or from the user's own
        # config file, and that file's path, where a command for every paper goes.
        try:
            paper = Config(main="main.tex") if discovered is None else load_config(discovered)
            command = translation_command(paper)
            translation: dict[str, Any] = {"language": paper.translation_language,
                                           "command": None if command is None else shlex.join(command)}
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            translation = {"error": str(error)}
        translation["user_config"] = str(user_config_file())
        from . import overleaf as overleaf_site

        return _ok({
            "startup_dir": str(binding.start_dir),
            "discovered_config_path": None if discovered is None else str(discovered.resolve()),
            "bound_config_path": None if bound is None else str(bound),
            # The paper's folder this session serves, which stays the one it started with
            # while the config's dir changes under it.
            "bound_dir": None if bound is None else str(binding.bound_dir()),
            "guidelines": list_guidelines(),
            # The shell an agent runs commands in need not have this on its PATH.
            "cli": str(CLI),
            "translation": translation,
            # Where the reviewer's Overleaf cookie comes from ("file", "environment" or None),
            # never the cookie. It is the reviewer's own and serves every paper.
            "overleaf_cookie": overleaf_site.cookie_source(),
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


# How long the startup waits for the plugin's monitor, which Claude Code starts beside it.
MONITOR_START = 30.0


def _listen_from_start(shared: "SharedProjectServer") -> None:
    """In a session the plugin's monitor serves, listen from the start, unless another
    session listens to this paper already."""
    if plugin_monitor.claude_pid() is None:
        return
    deadline = time.monotonic() + MONITOR_START
    while (session := plugin_monitor.monitored_session()) is None:
        if time.monotonic() > deadline:
            return
        time.sleep(1)
    request = urllib.request.Request(
        f"http://127.0.0.1:{shared.port}/wait-review/listen?listener={LISTENER}&if_free=1&session={session}",
        headers=page_token.headers(), method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        if not json.loads(response.read())["listening"]:
            return
    plugin_monitor.hand_over(_waiter_command(shared.port))


def _listen_in_background(shared: "SharedProjectServer") -> None:
    """Listen from the start beside whatever came to the server: the first connection, or a
    tool call that started the server again once the session serving it had gone, taking
    that session's waiter with it."""
    def listen() -> None:
        try:
            _listen_from_start(shared)
        except (OSError, KeyError, ValueError) as error:
            print(f"tex-mcp-web did not start listening: {error}", file=sys.stderr)

    threading.Thread(target=listen, daemon=True).start()


def _connect_in_background(binding: "ProjectBinding") -> None:
    try:
        binding.connect()
    except ProjectSetupError as error:
        print(f"tex-mcp-web project is not ready: {error}", file=sys.stderr)


def main(start_dir: Path | None = None) -> None:
    """Run the MCP server with stdio transport, serving the viewer alongside."""
    _check_deps()
    binding = ProjectBinding(Path.cwd() if start_dir is None else start_dir, on_serve=_listen_in_background)
    # A discovered paper gets its review page without waiting for a tool call. Starting
    # the review server walks the paper's sources and stats hundreds of paths, quick against
    # a warm filesystem and slow against a cold one, so it runs beside the client's
    # handshake rather than in front of it, which lost the connection to a timeout. A tool
    # call made meanwhile waits for it.
    try:
        if binding.describe() is not None:
            threading.Thread(target=_connect_in_background, args=(binding,), daemon=True).start()
    except ProjectSetupError as error:
        print(f"tex-mcp-web project is not ready: {error}", file=sys.stderr)
    mcp = create_server(binding)
    try:
        asyncio.run(mcp.run_stdio_async())
    finally:
        binding.stop()
