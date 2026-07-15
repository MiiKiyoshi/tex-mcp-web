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
    )
    return tmp_path


def test_init_creates_yaml(project_dir, capsys):
    rc = main(["init", "--main", "main.tex"])
    assert rc == 0
    assert (project_dir / ".tex-mcp-web.yaml").exists()


def test_init_refuses_to_overwrite_existing(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    rc = main(["init", "--main", "main.tex"])
    assert rc == 1


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
    data = yaml.safe_load((project_dir / ".tex-mcp-web.yaml").read_text())
    assert data["port"] == 9000
    assert data["main"] == "main.tex"  # other keys untouched


def test_config_list_value_roundtrip(project_dir, capsys):
    main(["init", "--main", "main.tex"])
    capsys.readouterr()
    assert main(["config", "watch", "*.tex, *.bib"]) == 0
    capsys.readouterr()
    main(["config", "watch"])
    assert capsys.readouterr().out.strip() == "*.tex,*.bib"


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
    bad.write_text("\\documentclass{article}\\begin{document}x\\end{document}\n")
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
