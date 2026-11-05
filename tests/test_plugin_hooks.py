"""The Claude Code hooks that make an agent read what guide() lists before using tex-mcp."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "guide_gate.py"
TOOLS = ("compile", "read_comments", "write_comments", "image", "listen")


@pytest.fixture()
def run(tmp_path: Path):
    """Runs one hook of the gate the way Claude Code does, with its own cache directory."""
    cache = tmp_path / "cache"

    def hook(kind: str, event: dict) -> dict | None:
        result = subprocess.run(
            [sys.executable, str(GATE), kind], input=json.dumps(event), capture_output=True, text=True,
            env={**os.environ, "XDG_CACHE_HOME": str(cache)}, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    return hook


def paper(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    (root / ".tex-mcp-web.yaml").write_text("main: main.tex\nreferences: refs\n", encoding="utf-8")
    (root / ".tex-mcp-web").mkdir()
    terms = root / ".tex-mcp-web" / "terms.md"
    terms.write_text("# Terms the reference papers use\n\n- light curve (4)\n- radial velocity (3)\n", encoding="utf-8")
    return root, terms


def context(event: dict, session: str, agent: str | None) -> dict:
    event["session_id"] = session
    if agent is not None:
        event["agent_id"] = agent
    return event


def call(session: str, root: Path, tool: str = "compile", agent: str | None = None) -> dict:
    """What the PreToolUse hook receives when a tex-mcp tool is called."""
    return context({"cwd": str(root), "tool_name": f"mcp__tex-mcp__{tool}", "tool_input": {}}, session, agent)


def guided(session: str, root: Path, files: list[Path], agent: str | None = None) -> dict:
    """What the PostToolUse hook receives after guide(): one line of JSON in a text block."""
    answer = {"read": [str(path) for path in files]}
    return context({"cwd": str(root), "tool_name": "mcp__tex-mcp__guide",
                    "tool_response": [{"type": "text", "text": json.dumps(answer)}]}, session, agent)


def read(session: str, path: Path, agent: str | None = None, start: int = 1, lines: int | None = None,
         content: str | None = None) -> dict:
    """What the PostToolUse hook receives after Read, as Claude Code reports a returned file."""
    text = path.read_text(encoding="utf-8")
    total = len(text.splitlines())
    returned = {"filePath": str(path), "content": text if content is None else content,
                "numLines": total if lines is None else lines, "startLine": start, "totalLines": total}
    return context({"cwd": str(path.parent), "tool_name": "Read", "tool_input": {"file_path": str(path)},
                    "tool_response": {"type": "text", "file": returned}}, session, agent)


def unchanged(session: str, path: Path, offset: int | None = None, limit: int | None = None) -> dict:
    """What the PostToolUse hook receives when Claude Code answers a Read of a file it already
    returned in this context: the file's path and no contents."""
    tool_input = {"file_path": str(path)} if offset is None else {"file_path": str(path), "offset": offset, "limit": limit}
    return context({"cwd": str(path.parent), "tool_name": "Read", "tool_input": tool_input,
                    "tool_response": {"type": "file_unchanged", "file": {"filePath": str(path)}}}, session, None)


def reason(answer: dict | None) -> str | None:
    if answer is None:
        return None
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    return answer["hookSpecificOutput"]["permissionDecisionReason"]


def test_tex_mcp_tools_wait_for_the_listed_files_and_wait_again_after_a_compaction(tmp_path: Path, run) -> None:
    root, terms = paper(tmp_path / "paper")
    started = run("session-start", {"session_id": "s1", "cwd": str(root), "source": "startup"})
    assert "call guide() and read every file in its read list in full" in started["hookSpecificOutput"]["additionalContext"]
    # A session that listens from its start never sees listen()'s how, and the server's
    # instructions saying a press is a request left it a notice to ask about. Context a hook
    # adds is the user's own, and an agent told so here handled the press.
    assert "the user's request to handle the review now" in started["hookSpecificOutput"]["additionalContext"]
    # Before guide() the tools are refused, and the reason says to call it. guide() itself,
    # and setup_info() for setting a project up, are open.
    for tool in TOOLS:
        assert "guide() has not succeeded" in reason(run("before-tool", call("s1", root, tool)))
    assert run("before-tool", call("s1", root, "guide")) is None
    assert run("before-tool", call("s1", root, "setup_info")) is None
    # After guide() the tools wait for the files it lists, named in the reason.
    run("guide-read", guided("s1", root, [terms]))
    assert str(terms) in reason(run("before-tool", call("s1", root)))
    # A Read of part of a file, or one that came back cut short, does not count.
    run("file-read", read("s1", terms, start=2, lines=2))
    run("file-read", read("s1", terms, content="# Terms the reference papers use\n"))
    assert str(terms) in reason(run("before-tool", call("s1", root)))
    run("file-read", read("s1", terms))
    for tool in TOOLS:
        assert run("before-tool", call("s1", root, tool)) is None
    # guide() called again in the same context lists the file again. Reading it again finds it
    # unchanged, which counts for a whole Read and not for a part.
    run("guide-read", guided("s1", root, [terms]))
    assert str(terms) in reason(run("before-tool", call("s1", root)))
    run("file-read", unchanged("s1", terms, offset=2, limit=2))
    assert str(terms) in reason(run("before-tool", call("s1", root)))
    run("file-read", unchanged("s1", terms))
    assert run("before-tool", call("s1", root)) is None
    # A subagent starts without what its parent read.
    assert "guide() has not succeeded" in reason(run("before-tool", call("s1", root, agent="a1")))
    # A compaction starts the context again.
    run("session-start", {"session_id": "s1", "cwd": str(root), "source": "compact"})
    assert "guide() has not succeeded" in reason(run("before-tool", call("s1", root)))


def test_a_paper_with_nothing_to_read_opens_once_guide_has_answered(tmp_path: Path, run) -> None:
    root, _ = paper(tmp_path / "paper")
    assert "guide() has not succeeded" in reason(run("before-tool", call("s1", root)))
    run("guide-read", guided("s1", root, []))
    assert run("before-tool", call("s1", root)) is None


def test_outside_a_project_the_context_is_not_told_anything(tmp_path: Path, run) -> None:
    assert run("session-start", {"session_id": "s1", "cwd": str(tmp_path), "source": "startup"}) is None
