"""Tests for the slimmed structure parser (sections only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tex_mcp_web.structure import (
    DocumentStructure,
    Section,
    _extract_braced,
    _parse_sections,
    _strip_comment,
    find_section,
    parse_structure,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestExtractBraced:
    def test_simple(self):
        assert _extract_braced("{hello}", 0) == ("hello", 7)

    def test_nested(self):
        assert _extract_braced("{a {b} c}", 0) == ("a {b} c", 9)

    def test_unmatched(self):
        assert _extract_braced("{no close", 0) is None

    def test_escaped_braces(self):
        assert _extract_braced(r"{\{inner\}}", 0) == (r"\{inner\}", 11)


class TestStripComment:
    def test_full_comment(self):
        assert _strip_comment("% comment") == ""

    def test_inline_comment(self):
        assert _strip_comment("text % comment") == "text "

    def test_escaped_percent(self):
        assert _strip_comment("50\\% of data") == "50\\% of data"


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------


class TestParseSections:
    def test_simple(self):
        result = _parse_sections(r"\section{Introduction}", "main.tex")
        assert result == [Section(level="section", title="Introduction", file="main.tex", line=1)]

    def test_levels(self):
        for level in ("chapter", "section", "subsection", "subsubsection"):
            result = _parse_sections(rf"\{level}{{Title}}", "f.tex")
            assert result[0].level == level

    def test_starred(self):
        result = _parse_sections(r"\section*{Acknowledgments}", "f.tex")
        assert result[0].title == "Acknowledgments"

    def test_optional_short_title(self):
        result = _parse_sections(r"\section[Short]{Long Title}", "f.tex")
        assert result[0].title == "Long Title"

    def test_nested_braces_in_title(self):
        result = _parse_sections(r"\section{The $O(n^{2})$ Algorithm}", "f.tex")
        assert result[0].title == "The $O(n^{2})$ Algorithm"

    def test_section_picks_up_following_label(self):
        content = "\\section{Methods}\n\\label{sec:methods}\n"
        result = _parse_sections(content, "f.tex")
        assert result[0].label == "sec:methods"

    def test_section_without_label(self):
        content = "\\section{Methods}\nsome prose\n"
        result = _parse_sections(content, "f.tex")
        assert result[0].label is None

    def test_commented_section_ignored(self):
        result = _parse_sections("% \\section{Old}\n\\section{New}\n", "f.tex")
        assert len(result) == 1
        assert result[0].title == "New"

    def test_multiple_sections(self):
        content = "\\section{A}\n\\subsection{B}\n\\subsubsection{C}\n"
        result = _parse_sections(content, "f.tex")
        assert [(s.line, s.level) for s in result] == [
            (1, "section"),
            (2, "subsection"),
            (3, "subsubsection"),
        ]

    def test_no_sections(self):
        assert _parse_sections("Some prose without sections.\n", "f.tex") == []


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestParseStructure:
    def _setup_project(self, tmp_path: Path) -> Path:
        main = tmp_path / "main.tex"
        main.write_text(
            "\\documentclass{article}\n"
            "\\input{preamble}\n"
            "\\begin{document}\n"
            "\\section{Introduction}\n"
            "\\label{sec:intro}\n"
            "Some prose with \\cite{ref1, ref2}.\n"
            "\\input{chapters/methods}\n"
            "\\end{document}\n"
        )
        chapters = tmp_path / "chapters"
        chapters.mkdir()
        (chapters / "methods.tex").write_text(
            "\\section*{Methods}\n"
            "See \\cite{ref3}.\n"
            "\\label{sec:methods}\n"
        )
        return main

    def test_parses_all_sections(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        titles = {s.title for s in ds.sections}
        assert {"Introduction", "Methods"} <= titles

    def test_section_picks_up_following_label(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        intro = next(s for s in ds.sections if s.title == "Introduction")
        assert intro.label == "sec:intro"

    def test_paths_are_relative(self, tmp_path):
        self._setup_project(tmp_path)
        ds = parse_structure(tmp_path)
        for s in ds.sections:
            assert not Path(s.file).is_absolute()

    def test_empty_directory(self, tmp_path):
        ds = parse_structure(tmp_path)
        assert ds == DocumentStructure()


# ---------------------------------------------------------------------------
# main_file mode: walks via \input/\include, ignores unbuilt siblings
# ---------------------------------------------------------------------------


class TestParseStructureMainFile:
    def test_recursive_input_chain(self, tmp_path: Path):
        """main -> chap1 -> sub1 should pick up sections from all three."""
        (tmp_path / "main.tex").write_text(
            "\\section{Top}\n\\input{chap1}\n"
        )
        (tmp_path / "chap1.tex").write_text(
            "\\section{Chap 1}\n\\input{sub1}\n"
        )
        (tmp_path / "sub1.tex").write_text("\\subsection{Sub 1}\n")
        ds = parse_structure(tmp_path, tmp_path / "main.tex")
        titles = {s.title for s in ds.sections}
        assert titles == {"Top", "Chap 1", "Sub 1"}

    def test_unbuilt_sibling_excluded(self, tmp_path: Path):
        """A .tex file in the same dir but not \\input'd must be skipped.

        This is the v0.5.2 bug: paper-full-proofs.tex sat next to
        paper.tex; rglob walked both; sections from the unbuilt variant
        leaked into the agent's view.
        """
        (tmp_path / "paper.tex").write_text("\\section{Real}\n")
        (tmp_path / "paper-variant.tex").write_text(
            "\\section{Should Not Appear}\n"
        )
        ds = parse_structure(tmp_path, tmp_path / "paper.tex")
        files = {s.file for s in ds.sections}
        assert files == {"paper.tex"}
        assert "paper-variant.tex" not in files

    def test_cycle_terminates(self, tmp_path: Path):
        """a \\input{b}, b \\input{a} must not infinite-loop."""
        (tmp_path / "a.tex").write_text("\\section{A}\n\\input{b}\n")
        (tmp_path / "b.tex").write_text("\\section{B}\n\\input{a}\n")
        ds = parse_structure(tmp_path, tmp_path / "a.tex")
        titles = {s.title for s in ds.sections}
        assert titles == {"A", "B"}

    def test_missing_input_does_not_crash(self, tmp_path: Path):
        (tmp_path / "main.tex").write_text(
            "\\section{Real}\n\\input{nonexistent}\n"
        )
        ds = parse_structure(tmp_path, tmp_path / "main.tex")
        assert {s.title for s in ds.sections} == {"Real"}

    def test_input_in_subdir(self, tmp_path: Path):
        (tmp_path / "main.tex").write_text(
            "\\section{Top}\n\\input{chapters/intro}\n"
        )
        chapters = tmp_path / "chapters"
        chapters.mkdir()
        (chapters / "intro.tex").write_text("\\section{Introduction}\n")
        ds = parse_structure(tmp_path, tmp_path / "main.tex")
        assert {s.title for s in ds.sections} == {"Top", "Introduction"}
        files = {s.file for s in ds.sections}
        assert "chapters/intro.tex" in files

    def test_explicit_extension(self, tmp_path: Path):
        (tmp_path / "main.tex").write_text(
            "\\section{Top}\n\\input{intro.tex}\n"
        )
        (tmp_path / "intro.tex").write_text("\\section{Intro}\n")
        ds = parse_structure(tmp_path, tmp_path / "main.tex")
        assert {s.title for s in ds.sections} == {"Top", "Intro"}

    def test_commented_input_ignored(self, tmp_path: Path):
        """\\input inside a comment line must not be followed."""
        (tmp_path / "main.tex").write_text(
            "\\section{Top}\n% \\input{would-be-evil}\n"
        )
        (tmp_path / "would-be-evil.tex").write_text("\\section{Evil}\n")
        ds = parse_structure(tmp_path, tmp_path / "main.tex")
        assert {s.title for s in ds.sections} == {"Top"}

    def test_missing_main_file_falls_back_to_glob(self, tmp_path: Path):
        """When main_file doesn't exist on disk, fall back to the legacy
        rglob walk rather than returning nothing."""
        (tmp_path / "a.tex").write_text("\\section{A}\n")
        ds = parse_structure(tmp_path, tmp_path / "missing.tex")
        assert {s.title for s in ds.sections} == {"A"}


# ---------------------------------------------------------------------------
# find_section
# ---------------------------------------------------------------------------


class TestFindSection:
    @pytest.fixture
    def structure(self) -> DocumentStructure:
        return DocumentStructure(
            sections=[
                Section(level="section", title="Introduction", file="main.tex", line=10),
                Section(level="section", title="Methods", file="main.tex", line=50, label="sec:methods"),
                Section(level="subsection", title="Setup", file="main.tex", line=55),
                Section(level="section", title="Results", file="main.tex", line=120),
            ],
        )

    def test_match_by_title(self, structure):
        assert find_section(structure, title="Methods") == ("main.tex", 50, 54)

    def test_match_by_label(self, structure):
        assert find_section(structure, label="sec:methods") == ("main.tex", 50, 54)

    def test_match_case_insensitive_title(self, structure):
        assert find_section(structure, title="methods") == ("main.tex", 50, 54)

    def test_match_last_section_returns_eof_marker(self, structure):
        assert find_section(structure, title="Results") == ("main.tex", 120, -1)

    def test_no_match(self, structure):
        assert find_section(structure, title="Nonexistent") is None

    def test_label_takes_priority_over_title(self, structure):
        # If both are given, the label match wins
        assert find_section(structure, title="Wrong", label="sec:methods") == ("main.tex", 50, 54)

    def test_empty_structure(self):
        assert find_section(DocumentStructure(), title="x") is None
