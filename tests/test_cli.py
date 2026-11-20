"""CLI smoke tests (v0.5.0).

The v0.5.0 CLI is intentionally narrow: serve / init / compile / goto /
mcp.  Comment management lives in the browser and the MCP tools.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tex_mcp_web.cli import main


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\n"
        "\\label{sec:intro}\n"
        "Some prose.\n"
        "\\section{Methods}\n"
        "More prose.\n"
        "\\end{document}\n"
    , encoding="utf-8", newline="")
    return tmp_path


def test_init_creates_yaml(project_dir, capsys):
    rc = main(["init", "--main", "main.tex"])
    assert rc == 0
    assert (project_dir / ".tex-mcp-web.yaml").exists()
    data = yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8"))
    assert data["main"] == "main.tex"


def test_init_finds_the_folders_main_file(project_dir):
    """With no --main, init takes the one .tex file here that has \\documentclass, and
    paper.tex when the folder gives no single answer."""
    held = sorted(path.name for path in project_dir.glob("*.tex")
                  if "\\documentclass" in path.read_text(encoding="utf-8"))
    assert len(held) == 1
    (project_dir / "part.tex").write_text("A section of it.\n", encoding="utf-8", newline="")
    assert main(["init"]) == 0
    assert yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8"))["main"] == held[0]
    (project_dir / "other.tex").write_text("\\documentclass{article}\n", encoding="utf-8", newline="")
    assert main(["init", "--force"]) == 0
    assert yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8"))["main"] == "paper.tex"


def test_init_refuses_to_overwrite_existing(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["init", "--main", "main.tex"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "already exists here" in err and "--force overwrites it" in err


def test_init_under_a_parent_config_says_force_leaves_the_parent(project_dir, monkeypatch, capsys):
    main(["init", "--main", "main.tex"])
    parent_config = (project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8")
    child = project_dir / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    capsys.readouterr()
    assert main(["init", "--main", "child.tex"]) == 1
    err = capsys.readouterr().err
    # --force does not overwrite a parent's config, so the refusal must not say it does.
    assert "parent folder" in err and "overwrite" not in err
    assert main(["init", "--main", "child.tex", "--force"]) == 0
    assert (child / ".tex-mcp-web.yaml").exists()
    assert (project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8") == parent_config


def test_init_force_overwrites(project_dir):
    main(["init", "--main", "main.tex"])
    rc = main(["init", "--main", "other.tex", "--force"])
    assert rc == 0


def test_config_requires_init(project_dir, capsys):
    rc = main(["config", "port"])
    assert rc == 1


def test_config_set_and_get(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    assert main(["config", "port", "9000"]) == 0
    capsys.readouterr()
    assert main(["config", "port"]) == 0
    assert capsys.readouterr().out.strip() == "9000"
    data = yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8"))
    assert data["port"] == 9000
    assert data["main"] == "main.tex"  # other keys untouched


def test_config_names_the_overleaf_project_and_how_files_sync(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    project = "0123456789abcdef01234567"
    assert main(["config", "overleaf", f"https://ko.overleaf.com/project/{project}"]) == 0
    assert main(["config", "overleaf_sync", "true"]) == 0
    data = yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8"))
    assert data["overleaf"] == f"https://www.overleaf.com/project/{project}" and data["overleaf_sync"] is True
    capsys.readouterr()
    # The reviewer's cookie goes to the server in the link, so only overleaf.com is taken.
    assert main(["config", "overleaf", f"https://elsewhere.example/project/{project}"]) == 1
    assert "overleaf.com" in capsys.readouterr().err
    assert main(["config", "overleaf_sync", "yes"]) == 1
    assert yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8")) == data


def test_config_list_value_roundtrip(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    assert main(["config", "watch", "*.tex, *.bib"]) == 0
    capsys.readouterr()
    main(["config", "watch"])
    assert capsys.readouterr().out.strip() == "*.tex,*.bib"


def test_config_sets_the_reference_papers(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    assert main(["config", "references", "../related"]) == 0
    assert yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text(encoding="utf-8"))["references"] == "../related"


def test_init_chooses_no_guideline_and_config_takes_only_one_that_exists(project_dir, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(project_dir / "home"))
    main(["init", "--main", "main.tex"])
    config = project_dir / ".tex-mcp-web.yaml"
    assert "guideline" not in yaml.safe_load(config.read_text(encoding="utf-8"))
    assert main(["config", "guideline", "nowhere"]) == 1
    assert "guideline not found" in capsys.readouterr().err
    assert main(["config", "guideline", "../elsewhere"]) == 1
    assert "must name a folder" in capsys.readouterr().err
    mine = project_dir / "home" / "tex-mcp-web" / "guidelines" / "my-field"
    mine.mkdir(parents=True)
    (mine / "GUIDELINE.md").write_text("# My field\n", encoding="utf-8", newline="")
    assert main(["config", "guideline", "my-field"]) == 0
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["guideline"] == "my-field"


def test_config_rejects_unknown_key_and_compiler(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    assert main(["config", "bogus", "1"]) == 1
    assert main(["config", "page_limit", "10"]) == 1
    assert main(["config", "compiler", "nope"]) == 1


def test_compile_command_returns_nonzero_when_no_compiler(project_dir, capsys, monkeypatch):
    """Without latexmk on PATH, compile should fail cleanly with exit code 1."""
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    monkeypatch.setenv("PATH", "")
    rc = main(["compile", "--json"])
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["success"] is False


# ---------------------------------------------------------------------------
# parse_goto_target — distinguishes label from title from line/page targets.
# ---------------------------------------------------------------------------


def test_parse_goto_target_recognizes_label():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("sec:methods", "main.tex") == {"label": "sec:methods"}
    assert parse_goto_target("eq:foo-bar", "main.tex") == {"label": "eq:foo-bar"}
    assert parse_goto_target("thm:main", "main.tex") == {"label": "thm:main"}


def test_parse_goto_target_section_with_colon_is_title():
    from tex_mcp_web.mcp_server import parse_goto_target

    # Has space, so not label-shaped.
    assert parse_goto_target("Introduction: A Survey", "main.tex") == {
        "section": "Introduction: A Survey"
    }


def test_parse_goto_target_page():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("p3", "main.tex") == {"page": 3}


def test_parse_goto_target_line_in_default_file():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("42", "main.tex") == {"line": 42, "file": "main.tex"}


def test_parse_goto_target_file_line():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("intro.tex:42", "main.tex") == {"file": "intro.tex", "line": 42}


def test_parse_goto_target_falls_through_to_section():
    from tex_mcp_web.mcp_server import parse_goto_target

    assert parse_goto_target("Related Work", "main.tex") == {"section": "Related Work"}


# ---------------------------------------------------------------------------
# Compiler argument validation — guards against `-flag` injection via
# .tex-mcp-web.yaml's main field.
# ---------------------------------------------------------------------------


def test_compile_rejects_main_starting_with_dash(project_dir, capsys):
    """A malicious .tex-mcp-web.yaml with `main: --shell-escape paper.tex`
    must be rejected; otherwise latexmk would parse it as a flag and
    enable \\write18 RCE."""
    import asyncio

    from tex_mcp_web.compiler import compile_tex

    bad = project_dir / "-evil.tex"
    bad.write_text("\\documentclass{article}\\begin{document}x\\end{document}\n", encoding="utf-8", newline="")
    result = asyncio.run(compile_tex(bad, work_dir=project_dir))
    assert result.success is False
    assert any("'-'" in e.message or "may not start" in e.message for e in result.errors)


def test_get_compiler_command_rejects_dash_prefix():
    """Direct unit test for the argv guard."""
    from pathlib import Path

    from tex_mcp_web.compiler import _get_compiler_command

    with pytest.raises(ValueError, match="may not start with '-'"):
        _get_compiler_command("latexmk", Path("-evil.tex"), Path("/tmp"))


def test_get_compiler_command_rejects_dotdot():
    """Direct unit test for the path-traversal guard."""
    from pathlib import Path

    from tex_mcp_web.compiler import _get_compiler_command

    with pytest.raises(ValueError, match="'..'"):
        _get_compiler_command("latexmk", Path("../escape.tex"), Path("/tmp/sub"))


def test_mcp_check_names_the_tools_without_a_paper(tmp_path: Path, monkeypatch, capsys):
    """An install is checked before it is registered, from a folder with no paper."""
    monkeypatch.chdir(tmp_path)
    assert main(["mcp", "--check"]) == 0
    said = capsys.readouterr().out
    assert said.startswith("tex-mcp: 8 tools ("), said
    for tool in ("guide", "read_comments", "write_comments", "compile", "listen", "setup_info", "overleaf"):
        assert tool in said


def test_no_process_the_server_starts_is_given_the_servers_own_input() -> None:
    """Under an agent the server's input is the pipe the agent talks to it through. A child
    that inherits it holds it, and on Windows git and latexmk then never start: every
    process the package starts is given stdin=DEVNULL. The one exception is the
    translation command, which reads the request on its input: input= gives it a pipe of
    its own that carries the request, and DEVNULL when its arguments carry the request."""
    import ast

    package = Path(__file__).parents[1] / "tex_mcp_web"
    fed = "translate.py:run_command"
    missing = []
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        owner = {}
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(function):
                    owner[node] = function.name   # the innermost function, walked last
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in ("create_subprocess_exec", "Popen") or (
                    node.func.attr == "run" and getattr(node.func.value, "id", "") == "subprocess"):
                keywords = {keyword.arg: keyword.value for keyword in node.keywords}
                if f"{path.name}:{owner.get(node)}" == fed:
                    given = "input" in keywords
                else:
                    stdin = keywords.get("stdin")
                    given = isinstance(stdin, ast.Attribute) and stdin.attr == "DEVNULL"
                if not given:
                    missing.append(f"{path.name}:{node.lineno}")
    assert missing == []


def test_serve_says_in_one_line_that_its_port_is_in_use(project_dir, capsys):
    """A port another program holds: serve says so, names the port and how to choose another,
    and ends with a failure, before it says that it serves anything."""
    import socket

    assert main(["init", "--main", "main.tex"]) == 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        capsys.readouterr()
        assert main(["--port", str(port), "serve"]) == 1
    said = capsys.readouterr().err
    assert said.startswith(f"Port {port} is already in use") and "tex-mcp-web config port" in said
    assert "serving" not in said and "Traceback" not in said


@pytest.mark.parametrize("encoding, shown", [
    # Korean Windows' console code page holds Korean letters but no em dash.
    ("cp949", "Watching C:\\Users\\민수\\논문 - 초고\\main.tex"),
    # One that holds no Korean letter puts a stand-in for each, and keeps the dash.
    ("cp1252", "Watching C:\\Users\\??\\?? — ??\\main.tex"),
])
def test_the_log_writes_what_its_stream_can_hold_as_it_is(encoding, shown):
    """A paper folder with a Korean name reads in the log as it is, where the stream's
    encoding holds its letters, and no character comes out as an escape."""
    import io
    import logging

    from tex_mcp_web.cli import _log_handler

    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="backslashreplace", newline="")
    handler = _log_handler(stream)
    handler.emit(logging.LogRecord("tex_mcp_web.server", logging.INFO, __file__, 1,
                                   "Watching %s", ("C:\\Users\\민수\\논문 — 초고\\main.tex",), None))
    stream.flush()
    written = stream.buffer.getvalue().decode(encoding)

    assert written.endswith(f" INFO    tex_mcp_web.server {'-' if encoding == 'cp949' else '—'} {shown}\n")
    assert "\\u" not in written and "\\x" not in written


def test_what_a_child_process_prints_is_never_read_in_the_systems_code_page() -> None:
    """A Windows set to Korean reads a child's output as cp949 unless told otherwise, and
    synctex, git and the TeX tools write UTF-8: a paper folder with Korean letters in its
    name then broke every search between the PDF and the source. No call asks for text
    without naming the encoding."""
    import ast

    package = Path(__file__).parents[1] / "tex_mcp_web"
    unnamed = []
    for path in sorted(package.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            as_text = any(isinstance(keywords.get(name), ast.Constant) and keywords[name].value is True
                          for name in ("text", "universal_newlines"))
            if as_text and "encoding" not in keywords:
                unnamed.append(f"{path.name}:{node.lineno}")
    assert unnamed == []



def test_a_log_that_goes_to_a_file_is_utf8_whatever_the_systems_encoding() -> None:
    """The server's log, read by the program that started it or from a file, is UTF-8 also
    where the system's own encoding is another, as on a Korean Windows."""
    import os
    import subprocess
    import sys

    code = ("import logging; from tex_mcp_web.cli import _configure_logging; "
            "_configure_logging(False); logging.getLogger('t').info('caf\u00e9 \ud55c\uae00')")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          env={**os.environ, "PYTHONIOENCODING": "cp949", "PYTHONUTF8": "0"}, check=True)
    assert "café 한글" in done.stderr.decode("utf-8")


def test_terms_builds_the_term_list_and_prints_what_to_read(project_dir, capsys, monkeypatch):
    """What an agent runs when it is about to edit the paper and has no guide() to call: the
    files guide() lists, in its order, the writing guideline first."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(project_dir / "home"))
    (project_dir / "refs").mkdir()
    (project_dir / "refs" / "a.md").write_text("# A\n\nThe light curve and the light curve.\n", encoding="utf-8")
    guide = project_dir / "home" / "tex-mcp-web" / "guidelines" / "plain" / "GUIDELINE.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("# How to write\n", encoding="utf-8")
    (project_dir / ".tex-mcp-web.yaml").write_text(
        "main: main.tex\nreferences: refs\nguideline: plain\n", encoding="utf-8")
    assert main(["terms"]) == 0
    terms = project_dir / ".tex-mcp-web" / "terms.md"
    assert terms.is_file()
    assert capsys.readouterr().out.split() == [str(guide), str(terms)]


def test_terms_refuses_a_missing_guideline(project_dir, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(project_dir / "home"))
    (project_dir / ".tex-mcp-web.yaml").write_text("main: main.tex\nguideline: nope\n", encoding="utf-8")
    assert main(["terms"]) == 1
    assert "nope" in capsys.readouterr().err
