"""MCP server for tex-mcp-web v0.7.0.

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
import re
import secrets
import shlex
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from . import plugin_monitor

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
        return str(json.loads(_cursor_path(watch_dir).read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cursor_moment(watch_dir: Path) -> datetime | None:
    """The moment read_comments(new=True) last carried the reviewer's words up to, or None."""
    previous = read_cursor(watch_dir)
    if previous is None:
        return None
    edge = datetime.fromisoformat(previous)
    return edge if edge.tzinfo else edge.replace(tzinfo=timezone.utc)


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
        async with httpx.AsyncClient(timeout=120.0) as client:
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
        return (f"Overleaf has {count} commit{'' if count == 1 else 's'} this folder lacks. "
                'overleaf(action="get") brings them in. Do it before editing.')
    if upstream.get("get"):
        return (f"Overleaf has edits to {', '.join(upstream['get'])} this folder lacks. "
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
    if comment.overleaf_draft is not None:
        # The reply drafted for Overleaf, which the reviewer has not sent yet. A comment with
        # no entries is a new thread drafted for Overleaf, and the draft is all it holds.
        payload["overleaf_draft"] = comment.overleaf_draft.text
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

    comment = store.add(
        anchor=a,
        text=text,
        author="agent",
        resolved_source=resolved,
        source_selector=source_selector,
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
        return _err("a thread on Overleaf is on words in a file: give a source_range anchor")
    held = _range_text(watch_dir, f"{anchor.file}:{anchor.start}-{anchor.end}")
    if not held["text"].strip():
        return _err("those lines are blank, and a thread on Overleaf is on words")
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


def _wait_method(ctx: "Context", command: str) -> str:
    name = ctx.session.client_params.clientInfo.name.casefold()
    if "claude" in name:
        if plugin_monitor.hand_over(command):
            return (
                "The tex-mcp plugin's monitor now runs the command for this session and turns each "
                "line it prints into a notification, for as long as the session lasts. Do not "
                "start it yourself, and end the turn."
            )
        return (
            'Run the command with Monitor(command=<command>, description="tex-mcp review", '
            "timeout_ms=1800000), then end the turn. When it expires, leave it stopped and tell the "
            "user in one line that listening stopped and that asking again resumes it. Nothing is "
            "lost while it is stopped: new "
            "comments wait for read_comments(new=True), and a Call agent press waits for the next "
            "waiter. Stop an active Monitor when the review is finished or the user asks. "
            "Start another copy only after the previous process has ended."
        )
    if "codex" in name:
        return (
            'Run sh <command> --codex "$CODEX_THREAD_ID" with '
            'exec_command(yield_time_ms=1000, sandbox_permissions="require_escalated", '
            'justification="Allow the TeX review waiter to deliver events to this Codex thread?"). '
            "Once running, end the turn; do not poll. "
            "The script uses codex queue to deliver events as labeled user messages, "
            "including while idle. Delivery may take about 10 seconds. "
            "Requires codex queue on PATH and CODEX_THREAD_ID in the agent shell. "
            "Run one waiter at a time. "
            "Stop its process when the review is finished or the user asks. "
            "Start another copy only after the previous process has ended."
        )
    return (
        "Run the command with your shell tool and read its output. If the tool returns a "
        "running session, read what follows with the tool that reads subsequent output. "
        "After handling an event, stop it; listening resumes when the user asks again. "
        "Start another copy only after the previous process has ended."
    )


# The id this process's session listens by. One per process, so a session asked to listen
# again restarts its waiter without that waiter hearing that another session took over.
LISTENER = secrets.token_hex(8)


def _write_waiter(watch_dir: Path, port: int) -> Path:
    """Write the paper's review waiter and return its path. It runs as <path> <listener>,
    with the id the review server knows the listening session by."""
    # The server keeps the press count and the consumption watermark, so the script
    # carries no state of its own: a press made before this call answers it at once,
    # and after a line is delivered the loop parks again rather than replaying the
    # press it acked. The ack comes after the line is printed, so a press is offered
    # until a waiter confirms it landed: delivery is at-least-once.
    script = (
        "#!/bin/sh\n"
        "listener=${1:?Pass the listener id that listen() gave}\n"
        "thread=\n"
        "if [ \"${2-}\" = \"--codex\" ]; then\n"
        "  thread=${3:?Pass the Codex thread ID}\n"
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
        "# Presses go to one waiter: when a newer one takes over listening, one [moved] line ends this one.\n"
        "headers=$(mktemp) || exit 1\n"
        "trap 'rm -f \"$headers\"' EXIT\n"
        "gone=0\n"
        "delay=2\n"
        "while :; do\n"
        f"  out=$(curl -sf -D \"$headers\" --max-time 60 \"http://127.0.0.1:{port}/wait-review?listener=$listener&pid=$$\"); rc=$?\n"
        "  if [ $gone -eq 1 ] && { [ $rc -eq 0 ] || [ $rc -eq 28 ]; }; then\n"
        "    deliver '[back] review server reachable again'; gone=0; delay=2\n"
        "  fi\n"
        "  if [ $rc -eq 0 ] && [ -n \"$out\" ]; then\n"
        "    deliver \"$out\"\n"
        "    grep -qi '^x-moved:' \"$headers\" && exit 0\n"
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
    return target


def create_server(binding: "ProjectBinding") -> "FastMCP":
    _check_deps()
    mcp = _Server(
        "tex-mcp-web",
        instructions=(
            "Before editing the paper, call guide() and read every file in its read list in full. "
            "Work from read_comments(new=True), which gives what the reviewer has "
            "written since you last asked, each thread with the rev a write quotes back. "
            "read_comments(ids=[...]) gives a thread whole when its conversation is no "
            "longer in mind. Read source with your own file tools. "
            "Within the user's editing scope, correct what a thread reports, compile() once "
            "after the batch, verify, and reply naming the ranges you changed. When the wording "
            "is the reviewer's to decide, put a suggestion on their own thread rather than "
            "opening another comment. Respect read-only or discussion-only requests. "
            "After replying, write in chat only what no thread carries, such as a failed tool or a choice left to the user, not the replies or the edits they report. Call listen() when the user asks to listen and follow "
            "how. Do not poll or duplicate it. Unacknowledged presses stay queued. "
            "The config sits in the startup directory, and its dir can name the paper's folder anywhere. "
            f"Setup: read {INIT_GUIDE}."
        ),
    )
    # A path, not the list: the agent reads it with its own file tool, and the plugin's
    # hook checks that the Read returned it whole. Plain text, since structured output
    # would wrap the answer in {"result": ...} and the hook reads the answer as written.
    @mcp.tool(structured_output=False)
    async def guide() -> str:
        """Paths to files to read in full before editing the paper: its guideline and the ones that
        guideline extends, base first, when guideline is set, and the terms its reference papers
        use, when references is set."""
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
        new: Annotated[bool, Field(description="Only what the reviewer has written since this call last answered; excludes ids.")] = False,
        status: Literal["open", "resolved", "archived", "all"] = "open",
        unanswered: Annotated[bool, Field(description="Only threads where the reviewer wrote to you after the thread's last answer: your reply, your Overleaf draft, or a reply sent to Overleaf.")] = False,
        since: Annotated[str | None, Field(description="A time this server printed, like '09-19 00:52'; only what came after it.")] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
        save: bool = False,
    ) -> str:
        """Read review threads: what is new, selected threads in full, or a listing.

        ``new`` answers a Call agent in one step: on open threads, what the reviewer has
        written that this has neither carried before nor already taken up, thread by
        thread, each with the rev a write quotes back. A thread's last answer takes up
        what came before it: your reply, your Overleaf draft, or a reply the reviewer sent
        to Overleaf, which is for the people there. A suggestion applied asks nothing. It reports the moment
        it read ``from`` and the one it moved to, so a turn that went wrong is taken again
        with since=<from>; to see again what was already answered, ask for the thread by id.

        ``ids`` gives those threads whole, which is what to ask for when the conversation
        is no longer in mind; with ``since`` it gives only the entries after that time.

        Without either, a listing: each request cut to its opening, the newest ``limit``
        threads with the rest counted in ``older``, narrowed by status, unanswered or
        since. Times read and are written back as ``MM-DD HH:MM`` on this server's clock.
        ``save`` needs ids; edit only the draft's Reply and Edit blocks.
        """
        cfg, watch_dir, store = _load_project(binding)
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
            edge = moment if moment is not None else cursor_moment(watch_dir)
            fresh, latest = [], previous
            brought = _on_overleaf(watch_dir)
            # Open threads only. A resolved or archived one is not where a new request
            # arrives; the reviewer reopens it first, and it comes back open.
            for comment in store.list(status="open"):
                # Unread and unanswered, both. The cursor says what has been carried
                # before; the thread's last answer says what has been taken up already.
                # Asked for the first time there is no cursor, and the second half alone
                # is what keeps that call off the whole history.
                said = comment.waiting(edge)
                if not said:
                    continue
                told = {"id": comment.id, "rev": revision_of(comment.updated),
                        "status": comment.status,
                        "entries": [_agent_entry(entry) for entry in said]}
                if comment.id in brought:
                    told["on_overleaf"] = True
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
        summaries = []
        for comment in store.list(status=None if status == "all" else status):
            if unanswered and not comment.waiting():
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

    @mcp.tool(structured_output=False)
    async def overleaf(action: Literal["state", "get", "push"] = "state") -> str:
        """This folder against its Overleaf project. state reads Overleaf again and names only
        what differs: get, Overleaf's edits not here, send, edits here not on Overleaf, choose,
        files waiting for the reviewer, and in a git clone the commits behind and ahead. get
        brings Overleaf's edits here (git pull in a git clone, Get otherwise), changing only
        files here, and a clash changes nothing. push sends a git clone's commits (git push).
        Sending edits without git is the reviewer's, from the page."""
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
            return _err("only a git clone pushes. Without git, the reviewer sends from the page")
        elif upstream.get("by") == "live":
            return _ok({"by": "live", "note": "Overleaf's edits come into the files by themselves"})
        elif "unread" in upstream:
            return _err(upstream["unread"])
        else:
            route = "files/get"
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
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
        cfg, _, _ = _load_project(binding)
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
        """Write to a comment thread.

        add: text, anchor. reply: id, text, or a saved draft alone. suggest: id, text,
        changes, rev; each change's old is quoted from the file that comment sits in and
        must occur there once, the way an editing tool takes it, never a line or a
        column. It replaces the thread's one live proposal and adds text as a reply, so
        the conversation stays whole, and it is refused on a thread the reviewer has not
        written in: propose on theirs rather than opening your own. withdraw: id, text,
        rev. overleaf: id, text, rev, and edits naming the source ranges the reply
        describes, as their lines are now: later edits move them as they move a comment, so
        a draft is not written again when lines shift. On a thread that came from Overleaf (on_overleaf), it puts the reply the
        reviewer may send there: a thread holds one draft, which this replaces, and only
        the reviewer sends it, asked first when a file those ranges are in is not the same
        here as on Overleaf. With anchor, a source_range, instead of id and rev, it drafts a
        new thread for Overleaf on those lines, text being its first message: a comment with
        no entries and that draft, which id and rev then redraft and only the reviewer
        sends, once what those ranges now hold is on Overleaf. edit: id,
        entry, text, rev, rewriting an entry you wrote. delete: id,
        refused once the reviewer has written in the thread.

        rev is the thread's token from read_comments; a thread that moved on refuses the
        write. edits names the source ranges a reply changed. The reviewer resolves
        threads, not you. Returns id, status and rev.
        """
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
                return _comment_add(store, cfg, watch_dir, text, anchor)
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
                                "file here yet. Once they are (a pull brings Overleaf's edits), the thread "
                                "moves onto them and takes a suggestion")
                pairs = [(change.old, change.new) for change in changes]
                return receipt(store.suggest(
                    id, stamp_for(id), text,
                    lambda comment: derive_suggestion(watch_dir, comment, pairs),
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
                drafted = target is not None and not target.thread and target.overleaf_draft is not None
                if id not in _on_overleaf(watch_dir) and not drafted:
                    return _err("this comment did not come from Overleaf")
                ranges, texts = _draft_ranges(watch_dir, edits or [])
                return receipt(store.draft_overleaf(id, stamp_for(id), OverleafDraft(text, ranges), texts))
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
        dpi: Annotated[int, Field(description="96 reads text. 150 or more shows fine detail at more tokens.")] = 96,
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
        """Return a command and client-specific instructions for listening for Call agent.

        Run the returned command using the how field, selected for the connected
        client. Stop it when the review is finished or the user asks.
        """
        try:
            shared = binding.require_shared()
        except ProjectSetupError as error:
            raise RuntimeError(
                f"{error}. If another project holds the port, propose a free port to the user, "
                "edit port in that config after they agree, and call listen() again."
            ) from error
        _, watch_dir, _ = _load_project(binding)
        import httpx

        session = plugin_monitor.claude_pid()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{binding.base_url()}/wait-review/listen", params={
                "listener": LISTENER, **({"session": session} if session is not None else {})})
        response.raise_for_status()
        command = f"{shlex.quote(str(_write_waiter(watch_dir, shared.port)))} {LISTENER}"
        return _ok({
            "command": command,
            "how": (
                _wait_method(ctx, command)
                + " On [review], call read_comments(new=True) and handle the review. "
                "[gone] means the review server is unreachable; the script keeps retrying. "
                "[back] means it is reachable again. [moved] means a newer waiter, in another "
                "session or in this one as the line says, took over listening, and the script has ended."
            ),
        })

    @mcp.tool(structured_output=False)
    async def setup_info() -> str:
        """Report setup paths, the existing binding and the guidelines to choose from, without
        connecting or changing files."""
        from .config import find_config, list_guidelines

        discovered = find_config(binding.start_dir)
        bound = binding.bound_config_path()
        return _ok({
            "startup_dir": str(binding.start_dir),
            "discovered_config_path": None if discovered is None else str(discovered.resolve()),
            "bound_config_path": None if bound is None else str(bound),
            "guidelines": list_guidelines(),
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
        method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        if not json.loads(response.read())["listening"]:
            return
    plugin_monitor.hand_over(f"{shlex.quote(str(_write_waiter(shared.watch_dir, shared.port)))} {LISTENER}")


def _connect_in_background(binding: "ProjectBinding") -> None:
    try:
        shared = binding.connect()
    except ProjectSetupError as error:
        print(f"tex-mcp-web project is not ready: {error}", file=sys.stderr)
        return
    if shared is not None:
        try:
            _listen_from_start(shared)
        except (OSError, KeyError, ValueError) as error:
            print(f"tex-mcp-web did not start listening: {error}", file=sys.stderr)


def main(start_dir: Path | None = None) -> None:
    """Run the MCP server with stdio transport, serving the viewer alongside."""
    _check_deps()
    binding = ProjectBinding(Path.cwd() if start_dir is None else start_dir)
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
