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


@pytest.mark.parametrize("written", [
    "main: main.tex\ndir: ../papers/p1\nreferences: refs\nguideline: mine\n",
    "main: 'main.tex'\ndir: \"../papers/p1\"  # where the paper is\nreferences: 'refs'\nguideline: \"mine\"\n",
    "# a paper\nwatch:\n  - '*.tex'\n  - dir: no\nmain: main.tex\n\ndir:   ../papers/p1\nreferences: refs # papers\nguideline: mine\n",
    "main: main.tex\noverleaf_add: ['*.tex']\ntranslation:\n  language: Korean\n  dir: elsewhere\n",
    "main: main.tex\nreferences:\nguideline:\n",
])
def test_the_gate_reads_a_configuration_as_the_server_does(tmp_path: Path, monkeypatch, written: str) -> None:
    """The gate runs outside the server's environment and reads the configuration without its
    YAML reader: the paper's folder, whether there are references and the guideline it finds
    are the ones the server finds."""
    import importlib.util

    from tex_mcp_web.config import get_guideline_files, get_references_dir, get_watch_dir, load_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    guide = tmp_path / "config" / "tex-mcp-web" / "guidelines" / "mine" / "GUIDELINE.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("# How to write\n", encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".tex-mcp-web.yaml").write_text(written, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("guide_gate", GATE)
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    config = load_config(work / ".tex-mcp-web.yaml")
    watched, terms, guides = gate.required(work)
    assert watched == get_watch_dir(config)
    assert bool(terms) is (get_references_dir(config) is not None)
    assert guides == get_guideline_files(config)


def test_a_hook_that_fails_lets_the_call_through(tmp_path: Path) -> None:
    """A hook that cannot run, as one whose shared file is missing, ends with an error Claude
    Code does not take for a refusal: the call goes on."""
    alone = tmp_path / "hooks"
    alone.mkdir()
    (alone / "guide_gate.py").write_bytes(GATE.read_bytes())
    event = {"session_id": "s1", "cwd": str(tmp_path), "tool_name": "mcp__tex-mcp__compile", "tool_input": {}}
    result = subprocess.run([sys.executable, str(alone / "guide_gate.py"), "before-tool"], input=json.dumps(event),
                            capture_output=True, text=True)
    assert result.returncode == 1 and result.stdout == "" and "guide_record" in result.stderr


def test_a_hook_reads_its_event_as_utf8_whatever_the_system_reads_text_in(tmp_path: Path) -> None:
    """Claude Code gives the event as UTF-8. Read in a system's own code page, as Windows reads
    text, a folder named in Korean came out garbled and the hook found no project there: the
    hook reads the bytes as UTF-8, and finds it."""
    folder = tmp_path / "논문"
    folder.mkdir()
    (folder / ".tex-mcp-web.yaml").write_text("main: main.tex\n", encoding="utf-8")
    event = {"session_id": "s", "cwd": str(folder), "hook_event_name": "SessionStart"}
    result = subprocess.run([sys.executable, "-X", "utf8=0", str(GATE), "session-start"],
                            input=json.dumps(event, ensure_ascii=False).encode("utf-8"), capture_output=True,
                            env={**os.environ, "PYTHONIOENCODING": "cp1252", "XDG_CACHE_HOME": str(tmp_path / "cache")})
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert "guide()" in json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


HOOKS = GATE.parent


def test_every_hook_runs_through_the_script_that_finds_a_python() -> None:
    """Claude Code runs a hook in Git Bash on Windows, where python3 may be only the Microsoft
    Store's stand-in: no hook names python3 itself."""
    commands = [hook["command"] for groups in json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))["hooks"].values()
                for group in groups for hook in group["hooks"]]
    assert commands and all(command.startswith('sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" ') for command in commands)


@pytest.mark.skipif(sys.platform == "win32", reason="stands in for Git Bash's uname with a script on the PATH")
@pytest.mark.parametrize("system, ran", [("MINGW64_NT-10.0-26100", "py -3"), ("Linux", "python3")])
def test_the_hook_script_takes_py_on_windows_and_python3_elsewhere(tmp_path: Path, system: str, ran: str) -> None:
    for name, said in (("uname", f"echo {system}"), ("py", 'echo py "$@"'), ("python3", 'echo python3 "$@"')):
        (tmp_path / name).write_text(f"#!/bin/sh\n{said}\n", encoding="utf-8")
        (tmp_path / name).chmod(0o755)
    result = subprocess.run(["sh", str(HOOKS / "python.sh"), "gate.py", "before-tool"], capture_output=True, text=True,
                            env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"})
    assert result.stdout.strip() == f"{ran} gate.py before-tool"
