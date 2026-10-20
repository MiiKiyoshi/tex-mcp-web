"""The Claude Code hooks that make an agent read the terms list before writing the paper."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "terms_gate.py"


@pytest.fixture()
def run(tmp_path: Path):
    """Runs one hook the way Claude Code does, with its own cache directory."""
    cache = tmp_path / "cache"

    def hook(kind: str, event: dict) -> dict | None:
        result = subprocess.run(
            [sys.executable, str(GATE), kind], input=json.dumps(event), capture_output=True, text=True,
            env={**os.environ, "XDG_CACHE_HOME": str(cache)}, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    return hook


def paper(root: Path, terms: bool = True) -> Path:
    root.mkdir(parents=True)
    (root / ".tex-mcp-web.yaml").write_text("main: main.tex\nreferences: refs\n", encoding="utf-8")
    (root / "main.tex").write_text("\\section{Intro}\n", encoding="utf-8")
    if terms:
        (root / ".tex-mcp-web").mkdir()
        (root / ".tex-mcp-web" / "terms.md").write_text(
            "# Terms the reference papers use\n\n- light curve (4)\n- radial velocity (3)\n", encoding="utf-8")
    return root


def context(event: dict, session: str, agent: str | None) -> dict:
    event["session_id"] = session
    if agent is not None:
        event["agent_id"] = agent
    return event


def call(session: str, root: Path, tool: str = "compile", agent: str | None = None) -> dict:
    return context({"cwd": str(root), "tool_name": f"mcp__tex-mcp__{tool}", "tool_input": {}}, session, agent)


def edit(session: str, path: Path, agent: str | None = None, tool: str = "Edit") -> dict:
    return context({"cwd": str(path.parent), "tool_name": tool, "tool_input": {"file_path": str(path)}}, session, agent)


def read(session: str, path: Path, agent: str | None = None, start: int = 1, lines: int | None = None,
         content: str | None = None) -> dict:
    """What the PostToolUse hook receives after Read, as Claude Code reports a returned file."""
    text = path.read_text(encoding="utf-8")
    total = len(text.splitlines())
    returned = {"filePath": str(path), "content": text if content is None else content,
                "numLines": total if lines is None else lines, "startLine": start, "totalLines": total}
    return context({"cwd": str(path.parent), "tool_name": "Read", "tool_input": {"file_path": str(path)},
                    "tool_response": {"type": "text", "file": returned}}, session, agent)


def refused(answer: dict | None) -> bool:
    if answer is None:
        return False
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "terms the paper's reference papers use" in answer["hookSpecificOutput"]["permissionDecisionReason"]
    return True


def test_writing_waits_for_the_terms_list_and_waits_again_after_a_compaction(tmp_path: Path, run) -> None:
    root = paper(tmp_path / "paper")
    terms = root / ".tex-mcp-web" / "terms.md"
    started = run("session-start", {"session_id": "s1", "cwd": str(root), "source": "startup"})
    assert str(terms) in started["hookSpecificOutput"]["additionalContext"]
    # The paper's sources and tex-mcp's tools wait. setup_info, notes beside the paper and
    # files of other folders do not.
    for tool in ("compile", "read_comments", "write_comments", "image", "listen"):
        assert refused(run("before-tool", call("s1", root, tool)))
    assert refused(run("before-tool", edit("s1", root / "main.tex")))
    assert refused(run("before-tool", edit("s1", root / "sections" / "intro.tex", tool="Write")))
    assert run("before-tool", call("s1", root, "setup_info")) is None
    assert run("before-tool", context({"cwd": str(root), "tool_name": "mcp__other__compile", "tool_input": {}},
                                      "s1", None)) is None
    assert run("before-tool", edit("s1", root / "notes.md")) is None
    assert run("before-tool", edit("s1", tmp_path / "elsewhere" / "main.tex")) is None
    # Part of the list, or a list cut short, does not count.
    run("file-read", read("s1", terms, start=2, lines=2))
    run("file-read", read("s1", terms, content="# Terms the reference papers use\n"))
    assert refused(run("before-tool", call("s1", root)))
    run("file-read", read("s1", terms))
    assert run("before-tool", call("s1", root)) is None
    assert run("before-tool", edit("s1", root / "main.tex")) is None
    # A subagent starts without what its parent read.
    assert refused(run("before-tool", edit("s1", root / "main.tex", agent="a1")))
    # A rebuilt list is read again.
    os.utime(terms, ns=(terms.stat().st_mtime_ns + 10**9,) * 2)
    assert refused(run("before-tool", call("s1", root)))
    run("file-read", read("s1", terms))
    assert run("before-tool", call("s1", root)) is None
    # A compaction starts the context again.
    run("session-start", {"session_id": "s1", "cwd": str(root), "source": "compact"})
    assert refused(run("before-tool", edit("s1", root / "main.tex")))


def test_a_paper_without_a_terms_list_is_not_held(tmp_path: Path, run) -> None:
    root = paper(tmp_path / "paper", terms=False)
    assert run("session-start", {"session_id": "s1", "cwd": str(root), "source": "startup"}) is None
    assert run("before-tool", call("s1", root)) is None
    assert run("before-tool", edit("s1", root / "main.tex")) is None
