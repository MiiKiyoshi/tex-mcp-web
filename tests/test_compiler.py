"""Tests for compiler module."""

import pytest

from tex_mcp_web.compiler import (
    CompileMessage,
    CompileResult,
    _detect_compiler,
    _get_compiler_command,
    _parse_errors,
    _parse_pandoc_errors,
    _parse_warnings,
    check_compiler_available,
    enrich_error_context,
)
from pathlib import Path


class TestGetCompilerCommand:
    """Tests for _get_compiler_command function."""

    def test_latexmk(self, tmp_path):
        """Test latexmk command generation."""
        main_file = tmp_path / "main.tex"
        cmd = _get_compiler_command("latexmk", main_file, tmp_path)

        assert cmd[0] == "latexmk"
        assert "-pdf" in cmd
        assert "-synctex=1" in cmd
        assert "-file-line-error" in cmd
        assert "main.tex" in cmd

    def test_pdflatex(self, tmp_path):
        """Test pdflatex command generation."""
        main_file = tmp_path / "doc.tex"
        cmd = _get_compiler_command("pdflatex", main_file, tmp_path)

        assert cmd[0] == "pdflatex"
        assert "-synctex=1" in cmd
        assert "doc.tex" in cmd

    def test_xelatex(self, tmp_path):
        """Test xelatex command generation."""
        main_file = tmp_path / "doc.tex"
        cmd = _get_compiler_command("xelatex", main_file, tmp_path)

        assert cmd[0] == "xelatex"

    def test_unknown_compiler(self, tmp_path):
        """Test unknown compiler raises error."""
        main_file = tmp_path / "doc.tex"
        with pytest.raises(ValueError, match="Unknown compiler"):
            _get_compiler_command("unknown", main_file, tmp_path)


class TestParseErrors:
    """Tests for _parse_errors function."""

    def test_parse_standard_error(self):
        """Test parsing standard LaTeX error."""
        log = """
! Undefined control sequence.
l.42 \\badcommand
"""
        errors = _parse_errors(log, "main.tex")
        assert len(errors) == 1
        assert errors[0].type == "error"
        assert errors[0].line == 42
        assert "Undefined control sequence" in errors[0].message

    def test_parse_file_line_error(self):
        """Test parsing file:line:error format."""
        log = "chapters/intro.tex:15: Missing $ inserted."
        errors = _parse_errors(log, "main.tex")
        assert len(errors) == 1
        assert errors[0].file == "chapters/intro.tex"
        assert errors[0].line == 15
        assert "Missing $ inserted" in errors[0].message

    def test_no_errors(self):
        """Test log with no errors."""
        log = "This is pdfTeX, Version 3.14159265"
        errors = _parse_errors(log, "main.tex")
        assert len(errors) == 0


class TestParseWarnings:
    """Tests for _parse_warnings function."""

    def test_parse_latex_warning(self):
        """Test parsing LaTeX warning."""
        log = "LaTeX Warning: Reference `fig:test' on page 3 undefined on input line 42."
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) >= 1
        # Check that we found the warning (may have duplicates from pattern matching)
        found = any("Reference" in w.message or w.line == 42 for w in warnings)
        assert found

    def test_parse_package_warning(self):
        """Test parsing package warning."""
        log = "Package hyperref Warning: Token not allowed in a PDF string"
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) >= 1

    def test_parse_underfull(self):
        """Test parsing underfull box warning."""
        log = "Underfull \\hbox (badness 10000) at lines 42--43"
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) == 1
        assert warnings[0].line == 42
        assert "Underfull" in warnings[0].message

    def test_parse_overfull(self):
        """Test parsing overfull box warning."""
        log = "Overfull \\hbox (15.0pt too wide) at line 100"
        warnings = _parse_warnings(log, "main.tex")
        assert len(warnings) == 1
        assert warnings[0].line == 100


class TestCheckCompilerAvailable:
    """Tests for check_compiler_available function."""

    def test_existing_command(self):
        """Test with command that should exist."""
        # 'python' should be available in test environment
        assert check_compiler_available("python") is True

    def test_nonexistent_command(self):
        """Test with command that doesn't exist."""
        assert check_compiler_available("nonexistent_compiler_xyz") is False


class TestCompileResult:
    """Tests for CompileResult dataclass."""

    def test_default_values(self):
        """Test CompileResult default values."""
        result = CompileResult(success=True)
        assert result.success is True
        assert result.errors == []
        assert result.warnings == []
        assert result.output_file is None
        assert result.duration_seconds == 0.0

    def test_with_messages(self):
        """Test CompileResult with errors and warnings."""
        error = CompileMessage(
            file="main.tex", line=10, message="Error", type="error"
        )
        warning = CompileMessage(
            file="main.tex", line=20, message="Warning", type="warning"
        )

        result = CompileResult(
            success=False, errors=[error], warnings=[warning]
        )

        assert len(result.errors) == 1
        assert len(result.warnings) == 1
        assert result.errors[0].line == 10
        assert result.warnings[0].line == 20


class TestDetectCompiler:
    """Tests for _detect_compiler function."""

    def test_auto_detect_tex(self, tmp_path):
        """Test .tex files resolve to latexmk."""
        assert _detect_compiler(tmp_path / "doc.tex") == "latexmk"

    def test_auto_detect_md(self, tmp_path):
        """Test .md files resolve to pandoc."""
        assert _detect_compiler(tmp_path / "doc.md") == "pandoc"

    def test_auto_detect_markdown(self, tmp_path):
        """Test .markdown files resolve to pandoc."""
        assert _detect_compiler(tmp_path / "doc.markdown") == "pandoc"

    def test_auto_detect_txt(self, tmp_path):
        """Test .txt files resolve to pandoc."""
        assert _detect_compiler(tmp_path / "notes.txt") == "pandoc"


class TestPandocCommand:
    """Tests for pandoc command generation."""

    def test_pandoc_command(self, tmp_path):
        """Test pandoc command is correct."""
        main_file = tmp_path / "doc.md"
        cmd = _get_compiler_command("pandoc", main_file, tmp_path)
        assert cmd == ["pandoc", "doc.md", "-o", "doc.pdf"]

    def test_auto_command_md(self, tmp_path):
        """Test auto resolves to pandoc for .md files."""
        main_file = tmp_path / "doc.md"
        cmd = _get_compiler_command("auto", main_file, tmp_path)
        assert cmd[0] == "pandoc"
        assert "doc.md" in cmd
        assert "doc.pdf" in cmd

    def test_auto_command_tex(self, tmp_path):
        """Test auto resolves to latexmk for .tex files."""
        main_file = tmp_path / "main.tex"
        cmd = _get_compiler_command("auto", main_file, tmp_path)
        assert cmd[0] == "latexmk"


class TestParsePandocErrors:
    """Tests for _parse_pandoc_errors function."""

    def test_empty_stderr(self):
        """Test empty stderr returns no errors."""
        assert _parse_pandoc_errors("") == []
        assert _parse_pandoc_errors("  \n  ") == []

    def test_stderr_with_error(self):
        """Test stderr content is captured as error."""
        errors = _parse_pandoc_errors("Error: Could not find file 'input.md'")
        assert len(errors) == 1
        assert errors[0].type == "error"
        assert "Could not find file" in errors[0].message

    def test_long_stderr_truncated(self):
        """Test long stderr is truncated to 200 chars."""
        long_msg = "x" * 300
        errors = _parse_pandoc_errors(long_msg)
        assert len(errors[0].message) == 200


class TestCheckCompilerWithAuto:
    """Tests for check_compiler_available with auto detection."""

    def test_auto_with_tex_file(self, tmp_path):
        """Test auto resolves to latexmk for .tex."""
        main_file = tmp_path / "doc.tex"
        # This tests that the function doesn't crash;
        # actual availability depends on the system
        result = check_compiler_available("auto", main_file=main_file)
        assert isinstance(result, bool)

    def test_auto_without_main_file(self):
        """Test auto without main_file defaults to latexmk."""
        result = check_compiler_available("auto")
        assert isinstance(result, bool)


class TestCompileMessageContext:
    """Tests for CompileMessage context field."""

    def test_context_default_none(self):
        """Test that context defaults to None."""
        msg = CompileMessage(file="main.tex", line=10, message="Error", type="error")
        assert msg.context is None

    def test_context_accepts_list(self):
        """Test that context can be set to a list of strings."""
        ctx = ["line 1", ">>> line 2 <<<", "line 3"]
        msg = CompileMessage(
            file="main.tex", line=2, message="Error", type="error", context=ctx
        )
        assert msg.context == ctx
        assert len(msg.context) == 3


class TestEnrichErrorContext:
    """Tests for enrich_error_context function."""

    def _make_tex_file(self, tmp_path, filename="main.tex", num_lines=20):
        """Create a tex file with numbered lines."""
        lines = [f"Line {i + 1} content" for i in range(num_lines)]
        path = tmp_path / filename
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_basic_context_extraction(self, tmp_path):
        """Test that context is extracted with correct markers."""
        self._make_tex_file(tmp_path, num_lines=20)
        msg = CompileMessage(file="main.tex", line=10, message="Error", type="error")
        enrich_error_context([msg], tmp_path)

        assert msg.context is not None
        assert len(msg.context) == 11  # lines 5-15 (10 +/- 5)
        # The error line should be marked
        assert ">>> Line 10 content <<<" in msg.context
        # Other lines should not be marked
        assert "Line 5 content" in msg.context
        assert "Line 15 content" in msg.context

    def test_context_near_start(self, tmp_path):
        """Test context extraction near start of file."""
        self._make_tex_file(tmp_path, num_lines=20)
        msg = CompileMessage(file="main.tex", line=2, message="Error", type="error")
        enrich_error_context([msg], tmp_path)

        assert msg.context is not None
        # Lines 1-7 (line 2, can only go 1 above, 5 below)
        assert msg.context[0] == "Line 1 content"
        assert ">>> Line 2 content <<<" in msg.context

    def test_context_near_end(self, tmp_path):
        """Test context extraction near end of file."""
        self._make_tex_file(tmp_path, num_lines=10)
        msg = CompileMessage(file="main.tex", line=9, message="Error", type="error")
        enrich_error_context([msg], tmp_path)

        assert msg.context is not None
        # Should include lines 4-10
        assert ">>> Line 9 content <<<" in msg.context
        assert msg.context[-1] == "Line 10 content"

    def test_no_context_without_line(self, tmp_path):
        """Test that messages without line numbers are skipped."""
        self._make_tex_file(tmp_path)
        msg = CompileMessage(file="main.tex", line=None, message="Error", type="error")
        enrich_error_context([msg], tmp_path)
        assert msg.context is None

    def test_no_context_without_file(self, tmp_path):
        """Test that messages without file are skipped."""
        msg = CompileMessage(file="", line=10, message="Error", type="error")
        enrich_error_context([msg], tmp_path)
        assert msg.context is None

    def test_no_context_missing_file(self, tmp_path):
        """Test that messages with non-existent file are skipped."""
        msg = CompileMessage(
            file="nonexistent.tex", line=10, message="Error", type="error"
        )
        enrich_error_context([msg], tmp_path)
        assert msg.context is None

    def test_line_out_of_range(self, tmp_path):
        """Test that out-of-range line numbers are skipped."""
        self._make_tex_file(tmp_path, num_lines=5)
        msg = CompileMessage(file="main.tex", line=100, message="Error", type="error")
        enrich_error_context([msg], tmp_path)
        assert msg.context is None

    def test_multiple_messages_same_file(self, tmp_path):
        """Test that multiple messages from the same file share cached content."""
        self._make_tex_file(tmp_path, num_lines=20)
        msg1 = CompileMessage(file="main.tex", line=5, message="Err1", type="error")
        msg2 = CompileMessage(file="main.tex", line=15, message="Err2", type="error")
        enrich_error_context([msg1, msg2], tmp_path)

        assert msg1.context is not None
        assert msg2.context is not None
        assert ">>> Line 5 content <<<" in msg1.context
        assert ">>> Line 15 content <<<" in msg2.context

    def test_custom_window_size(self, tmp_path):
        """Test context with a custom window size."""
        self._make_tex_file(tmp_path, num_lines=20)
        msg = CompileMessage(file="main.tex", line=10, message="Error", type="error")
        enrich_error_context([msg], tmp_path, window=2)

        assert msg.context is not None
        assert len(msg.context) == 5  # lines 8-12 (10 +/- 2)

    def test_single_line_file(self, tmp_path):
        """Test context for a single-line file."""
        path = tmp_path / "single.tex"
        path.write_text("Only line\n", encoding="utf-8")
        msg = CompileMessage(file="single.tex", line=1, message="Error", type="error")
        enrich_error_context([msg], tmp_path)

        assert msg.context is not None
        assert len(msg.context) == 1
        assert msg.context[0] == ">>> Only line <<<"
