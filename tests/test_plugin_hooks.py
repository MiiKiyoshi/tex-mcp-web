"""The Claude Code hooks that make an agent read what guide() lists before using tex-mcp."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "guide_gate.py"
TOOLS = ("compile", "read_comments", "write_comments", "listen")


@pytest.fixture()
def run(tmp_path: Path):
    """Runs one hook of the gate the way Claude Code does, with its own cache directory."""
    cache = tmp_path / "cache"

    def hook(kind: str, event: dict) -> dict | None:
        result = subprocess.run(
            [sys.executable, str(GATE), kind], input=json.dumps(event), capture_output=True, text=True,
            env={**os.environ, "XDG_CACHE_HOME": str(cache), "XDG_CONFIG_HOME": str(tmp_path / "config")},
            check=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    return hook


def paper(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True)
    (root / ".tex-mcp-web.yaml").write_text("main: main.tex\nreferences: refs\n", encoding="utf-8", newline="")
    (root / ".tex-mcp-web").mkdir()
    terms = root / ".tex-mcp-web" / "terms.md"
    terms.write_text("# Terms the reference papers use\n\n- light curve (4)\n- radial velocity (3)\n", encoding="utf-8", newline="")
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
    # setup_info() for setting a project up, and image(), which only draws a page, are open.
    for tool in TOOLS:
        assert "guide() has not succeeded" in reason(run("before-tool", call("s1", root, tool)))
    for tool in ("guide", "setup_info", "image"):
        assert run("before-tool", call("s1", root, tool)) is None
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


def edit(session: str, cwd: Path, path: Path, tool: str = "Edit", agent: str | None = None) -> dict:
    """What the PreToolUse hook receives when Claude Code is about to change a file itself."""
    return context({"cwd": str(cwd), "tool_name": tool, "tool_input": {"file_path": str(path)}}, session, agent)


def test_editing_paper_sources_directly_waits_for_the_terms_and_guidelines(tmp_path: Path, run) -> None:
    """An agent that changes a .tex or .bib file with Edit or Write, without going through
    tex-mcp's tools, still reads the paper's terms and writing guidelines first."""
    root, terms = paper(tmp_path / "paper")
    guides = tmp_path / "config" / "tex-mcp-web" / "guidelines"
    base = guides / "plain" / "GUIDELINE.md"
    base.parent.mkdir(parents=True)
    base.write_text("# Plain writing\n", encoding="utf-8")
    guide = guides / "mine" / "GUIDELINE.md"
    guide.parent.mkdir()
    guide.write_text("---\nname: mine\nextends: plain\n---\n# How to write\n\n- one idea per sentence\n", encoding="utf-8")
    (root / ".tex-mcp-web.yaml").write_text("main: main.tex\nreferences: refs\nguideline: mine\n", encoding="utf-8")
    (root / "tex").mkdir()
    source = root / "tex" / "abs.tex"
    source.write_text("x\n", encoding="utf-8")
    for tool in ("Edit", "Write", "MultiEdit"):
        refused = reason(run("before-edit", edit("s1", tmp_path, source, tool)))
        assert str(terms) in refused and str(guide) in refused and str(base) in refused
    # Files that are not paper sources, and files outside any paper, are left alone.
    assert run("before-edit", edit("s1", root, root / "notes.md")) is None
    assert run("before-edit", edit("s1", tmp_path, tmp_path / "other.tex")) is None
    run("file-read", read("s1", terms))
    refused = reason(run("before-edit", edit("s1", root, source)))
    assert str(terms) not in refused and str(guide) in refused
    run("file-read", read("s1", guide))
    assert str(base) in reason(run("before-edit", edit("s1", root, source)))
    run("file-read", read("s1", base))
    assert run("before-edit", edit("s1", root, source)) is None
    # The edit gate does not open tex-mcp's tools: they still wait for guide().
    assert "guide() has not succeeded" in reason(run("before-tool", call("s1", root)))
    # A compaction or a subagent starts again.
    assert reason(run("before-edit", edit("s1", root, source, agent="a1"))) is not None
    run("session-start", {"session_id": "s1", "cwd": str(root), "source": "compact"})
    assert reason(run("before-edit", edit("s1", root, source))) is not None


def test_a_config_kept_outside_the_paper_folder_covers_its_sources(tmp_path: Path, run) -> None:
    """A .tex-mcp-web.yaml in the working folder whose dir points at the paper elsewhere."""
    work, terms = paper(tmp_path / "work")
    paper_dir = tmp_path / "papers" / "p1"
    (paper_dir / "tex").mkdir(parents=True)
    (work / ".tex-mcp-web.yaml").write_text("main: main.tex\ndir: ../papers/p1\nreferences: refs\n", encoding="utf-8")
    source = paper_dir / "tex" / "intro.tex"
    source.write_text("x\n", encoding="utf-8")
    assert str(terms) in reason(run("before-edit", edit("s1", work, source)))
    run("file-read", read("s1", terms))
    assert run("before-edit", edit("s1", work, source)) is None


def test_a_missing_term_list_or_guideline_says_how_to_get_it(tmp_path: Path, run) -> None:
    root, terms = paper(tmp_path / "paper")
    terms.unlink()
    (root / ".tex-mcp-web.yaml").write_text(
        "main: main.tex\nreferences: refs\nguideline: nope\n", encoding="utf-8")
    source = root / "main.tex"
    source.write_text("x\n", encoding="utf-8")
    refused = reason(run("before-edit", edit("s1", root, source, "Write")))
    assert "tex-mcp-web terms" in refused and "nope" in refused
