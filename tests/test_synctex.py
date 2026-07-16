"""Tests for synctex module."""

import gzip
import logging
import subprocess

import pytest

from tex_mcp_web.synctex import (
    PDFPosition,
    SourcePosition,
    SyncTeXData,
    _normalize_path,
    find_synctex_file,
    get_visible_lines,
    parse_synctex,
    selection_to_source_range,
    source_to_page,
)


# Sample SyncTeX content (simplified) — WITH column field
SAMPLE_SYNCTEX = """SyncTeX Version:1
Input:1:./main.tex
Input:2:./chapter1.tex
Output:main.pdf
Magnification:1000
Unit:1
X Offset:0
Y Offset:0
Content:
{1
[1,42,0:0,0:0,0,0
h1,1,0:6553600,39321600:0,0
h1,10,0:6553600,45875200:0,0
h2,5,0:6553600,52428800:0,0
]
}
{2
[2,100,0:0,0:0,0,0
h1,50,0:6553600,39321600:0,0
]
}
"""

# Sample SyncTeX content — WITHOUT column field (pdflatex/memoir style)
# Also uses absolute paths with ./ component and (/) hbox records
SAMPLE_SYNCTEX_NO_COLUMN = """SyncTeX Version:1
Input:1:/base/dir/./main.tex
Input:2:/base/dir/./chapter1.tex
Output:main.pdf
Magnification:1000
Unit:1
X Offset:0
Y Offset:0
Content:
{1
[1,1:4736286,45851110:30736384,41114824,0
(1,1:4736286,6964510:30736384,0,0
h1,1:6553600,39321600:0,0,0
g1,10:6553600,45875200
k1,10:6553600,45875200:65781
v1,10:6553600,45875200:0,0,0
)
h2,5:6553600,52428800:0,0,0
]
}
{2
[2,100:0,0:0,0,0
h1,50:6553600,39321600:0,0,0
]
}
"""


@pytest.fixture
def synctex_file(tmp_path):
    """Create a test synctex file."""
    synctex_path = tmp_path / "main.synctex.gz"
    with gzip.open(synctex_path, "wt") as f:
        f.write(SAMPLE_SYNCTEX)
    return synctex_path


@pytest.fixture
def synctex_data():
    """Create sample SyncTeXData."""
    return SyncTeXData(
        pdf_to_source={
            1: [
                (600.0, SourcePosition(file="main.tex", line=1)),
                (700.0, SourcePosition(file="main.tex", line=10)),
                (800.0, SourcePosition(file="chapter1.tex", line=5)),
            ],
            2: [
                (600.0, SourcePosition(file="main.tex", line=50)),
            ],
        },
        source_to_pdf={
            ("main.tex", 1): [PDFPosition(page=1, x=100.0, y=600.0)],
            ("main.tex", 10): [PDFPosition(page=1, x=100.0, y=700.0)],
            ("main.tex", 50): [PDFPosition(page=2, x=100.0, y=600.0)],
            ("chapter1.tex", 5): [PDFPosition(page=1, x=100.0, y=800.0)],
        },
        input_files={1: "main.tex", 2: "chapter1.tex"},
    )


class TestParseSynctex:
    """Tests for parse_synctex function."""

    def test_parse_gzipped(self, synctex_file):
        """Test parsing gzipped synctex file."""
        data = parse_synctex(synctex_file)
        assert data is not None
        assert 1 in data.input_files
        assert 2 in data.input_files
        assert "main.tex" in data.input_files[1]
        assert "chapter1.tex" in data.input_files[2]

    def test_parse_uncompressed(self, tmp_path):
        """Test parsing uncompressed synctex file."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX)

        data = parse_synctex(synctex_path)
        assert data is not None

    def test_parse_nonexistent(self, tmp_path):
        """Test parsing nonexistent file."""
        data = parse_synctex(tmp_path / "nonexistent.synctex.gz")
        assert data is None

    def test_parse_invalid_gzip(self, tmp_path):
        """Test parsing invalid gzip file."""
        bad_path = tmp_path / "bad.synctex.gz"
        bad_path.write_bytes(b"not a gzip file")

        data = parse_synctex(bad_path)
        assert data is None


class TestParseSynctexNoColumn:
    """Tests for parsing SyncTeX without column field (pdflatex/memoir style)."""

    def test_parse_no_column_format(self, tmp_path):
        """Test parsing SyncTeX records without column field."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX_NO_COLUMN)

        data = parse_synctex(synctex_path)
        assert data is not None
        assert len(data.source_to_pdf) > 0, "source_to_pdf should have entries"
        assert len(data.pdf_to_source) > 0, "pdf_to_source should have entries"

    def test_no_column_source_to_pdf_entries(self, tmp_path):
        """Test that records without column produce correct source_to_pdf keys."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX_NO_COLUMN)

        data = parse_synctex(synctex_path)
        assert data is not None
        file_names = set(f for f, l in data.source_to_pdf.keys())
        # Absolute paths with ./ should be kept (since they're outside base_dir tmp_path)
        # The file names will be the absolute paths since they can't be relativized to tmp_path
        assert any("main.tex" in f for f in file_names)

    def test_no_column_visible_lines(self, tmp_path):
        """No-column records remain available for page visibility."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX_NO_COLUMN)

        data = parse_synctex(synctex_path)
        assert data is not None
        lines = get_visible_lines(data, 1)
        assert lines is not None
        assert lines[0] > 0

    def test_no_column_hbox_records(self, tmp_path):
        """Test that ( and ) hbox records are parsed."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX_NO_COLUMN)

        data = parse_synctex(synctex_path)
        assert data is not None
        # Page 1 should have entries from ( records
        assert 1 in data.pdf_to_source
        assert len(data.pdf_to_source[1]) > 0

    def test_no_column_glue_and_kern_records(self, tmp_path):
        """Test that g and k records without column are parsed."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX_NO_COLUMN)

        data = parse_synctex(synctex_path)
        assert data is not None
        # g and k records for line 10 should exist
        has_line_10 = any(l == 10 for _, l in data.source_to_pdf.keys())
        assert has_line_10, "Should have entries for line 10 from g/k records"


class TestFindSynctexFile:
    """Tests for find_synctex_file function."""

    def test_find_gzipped(self, tmp_path):
        """Test finding gzipped synctex file."""
        pdf_path = tmp_path / "document.pdf"
        synctex_path = tmp_path / "document.synctex.gz"
        pdf_path.touch()
        synctex_path.touch()

        result = find_synctex_file(pdf_path)
        assert result == synctex_path

    def test_find_uncompressed(self, tmp_path):
        """Test finding uncompressed synctex file."""
        pdf_path = tmp_path / "document.pdf"
        synctex_path = tmp_path / "document.synctex"
        pdf_path.touch()
        synctex_path.touch()

        result = find_synctex_file(pdf_path)
        assert result == synctex_path

    def test_prefer_gzipped(self, tmp_path):
        """Test that gzipped is preferred over uncompressed."""
        pdf_path = tmp_path / "document.pdf"
        gz_path = tmp_path / "document.synctex.gz"
        raw_path = tmp_path / "document.synctex"
        pdf_path.touch()
        gz_path.touch()
        raw_path.touch()

        result = find_synctex_file(pdf_path)
        assert result == gz_path

    def test_not_found(self, tmp_path):
        """Test when no synctex file exists."""
        pdf_path = tmp_path / "document.pdf"
        pdf_path.touch()

        result = find_synctex_file(pdf_path)
        assert result is None


def test_selection_to_source_range_requires_one_project_file(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.touch()
    source = tmp_path / "tex" / "intro.tex"
    source.parent.mkdir()
    source.write_text("one\ntwo\nthree\n")
    outputs = iter([
        f"SyncTeX result begin\nInput:{source}\nLine:2\nColumn:-1\nSyncTeX result end\n",
        f"SyncTeX result begin\nInput:{source}\nLine:3\nColumn:-1\nSyncTeX result end\n",
    ])
    monkeypatch.setattr("tex_mcp_web.synctex.shutil.which", lambda _: "/bin/synctex")
    monkeypatch.setattr(
        "tex_mcp_web.synctex.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=next(outputs), stderr=""
        ),
    )

    result = selection_to_source_range(
        pdf,
        1,
        [(10, 20, 30, 30), (10, 31, 30, 40)],
        tmp_path,
    )

    assert result == ("tex/intro.tex", 2, 3)


def test_selection_to_source_range_rejects_mixed_files(tmp_path, monkeypatch):
    pdf = tmp_path / "paper.pdf"
    pdf.touch()
    first = tmp_path / "first.tex"
    second = tmp_path / "second.tex"
    first.write_text("first\n")
    second.write_text("second\n")
    outputs = iter([
        f"Input:{first}\nLine:1\n",
        f"Input:{second}\nLine:1\n",
    ])
    monkeypatch.setattr("tex_mcp_web.synctex.shutil.which", lambda _: "/bin/synctex")
    monkeypatch.setattr(
        "tex_mcp_web.synctex.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout=next(outputs), stderr=""
        ),
    )

    assert selection_to_source_range(
        pdf,
        1,
        [(10, 20, 30, 30), (10, 31, 30, 40)],
        tmp_path,
    ) is None


class TestSourceToPage:
    """Tests for source_to_page function."""

    def test_exact_match(self, synctex_data):
        """Test finding exact line match."""
        pos = source_to_page(synctex_data, "main.tex", 10)
        assert pos is not None
        assert pos.page == 1
        assert pos.y == 700.0

    def test_different_file(self, synctex_data):
        """Test finding line in different file."""
        pos = source_to_page(synctex_data, "chapter1.tex", 5)
        assert pos is not None
        assert pos.page == 1

    def test_nearest_line(self, synctex_data):
        """Test finding nearest line when exact not found."""
        pos = source_to_page(synctex_data, "main.tex", 8)
        assert pos is not None
        # Should find line 10 (closest to 8 among 1, 10, 50)
        assert pos.page == 1

    def test_not_found(self, synctex_data):
        """Test when file not in synctex data."""
        pos = source_to_page(synctex_data, "nonexistent.tex", 1)
        assert pos is None


class TestGetVisibleLines:
    """Tests for get_visible_lines function."""

    def test_single_page(self, synctex_data):
        """Test getting visible lines for a page."""
        result = get_visible_lines(synctex_data, 1)
        assert result is not None
        min_line, max_line = result
        # Page 1 has lines 1, 10 from main.tex and 5 from chapter1.tex
        assert min_line == 1
        assert max_line == 10

    def test_page_not_found(self, synctex_data):
        """Test when page not in synctex data."""
        result = get_visible_lines(synctex_data, 99)
        assert result is None


class TestSyncTeXLogging:
    """Tests for debug logging in synctex functions."""

    def test_source_to_page_logs_exact_match(self, synctex_data, caplog):
        """Test that exact match is logged."""
        with caplog.at_level(logging.DEBUG, logger="tex_mcp_web.synctex"):
            source_to_page(synctex_data, "main.tex", 10)
        assert "EXACT match" in caplog.text

    def test_source_to_page_logs_nearest_match(self, synctex_data, caplog):
        """Test that nearest match is logged with delta."""
        with caplog.at_level(logging.DEBUG, logger="tex_mcp_web.synctex"):
            source_to_page(synctex_data, "main.tex", 8)
        assert "NEAREST match" in caplog.text
        assert "delta=" in caplog.text

    def test_source_to_page_logs_no_match(self, synctex_data, caplog):
        """Test that no-match is logged."""
        with caplog.at_level(logging.DEBUG, logger="tex_mcp_web.synctex"):
            source_to_page(synctex_data, "nonexistent.tex", 1)
        assert "NO MATCH" in caplog.text

    def test_parse_synctex_logs_stats(self, synctex_file, caplog):
        """Test that parse_synctex logs file and page counts."""
        with caplog.at_level(logging.DEBUG, logger="tex_mcp_web.synctex"):
            parse_synctex(synctex_file)
        assert "input file" in caplog.text.lower()
        assert "pages" in caplog.text

    def test_parse_synctex_logs_failure(self, tmp_path, caplog):
        """Test that parse_synctex logs read failure."""
        with caplog.at_level(logging.DEBUG, logger="tex_mcp_web.synctex"):
            parse_synctex(tmp_path / "nonexistent.synctex.gz")
        assert "failed to read" in caplog.text


class TestNormalizePath:
    """Tests for _normalize_path function."""

    def test_strips_dot_slash_prefix(self, tmp_path):
        """Test that ./ prefix is stripped from relative paths."""
        result = _normalize_path("./main.tex", tmp_path)
        assert result == "main.tex"

    def test_strips_dot_slash_nested(self, tmp_path):
        """Test that ./ prefix is stripped from nested relative paths."""
        result = _normalize_path("./chapters/intro.tex", tmp_path)
        assert result == "chapters/intro.tex"

    def test_bare_relative_unchanged(self, tmp_path):
        """Test that bare relative paths are unchanged."""
        result = _normalize_path("main.tex", tmp_path)
        assert result == "main.tex"

    def test_absolute_path_relativized(self, tmp_path):
        """Test that absolute paths under base_dir are relativized."""
        abs_path = str(tmp_path / "main.tex")
        result = _normalize_path(abs_path, tmp_path)
        assert result == "main.tex"

    def test_absolute_path_outside_base(self, tmp_path):
        """Test that absolute paths outside base_dir are returned as-is."""
        result = _normalize_path("/other/dir/main.tex", tmp_path)
        assert result == "/other/dir/main.tex"

    def test_parse_synctex_normalizes_dot_slash(self, tmp_path):
        """Test that parse_synctex strips ./ from input file paths."""
        synctex_path = tmp_path / "main.synctex"
        synctex_path.write_text(SAMPLE_SYNCTEX)
        data = parse_synctex(synctex_path)
        assert data is not None
        # SAMPLE_SYNCTEX has "Input:1:./main.tex" — should be normalized
        assert data.input_files[1] == "main.tex"
        assert data.input_files[2] == "chapter1.tex"


class TestSourceToPageBasenameFallback:
    """Tests for basename fallback in source_to_page."""

    def test_basename_fallback_matches(self):
        """Test basename fallback when full path doesn't match."""
        data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("chapters/intro.tex", 10): [
                    PDFPosition(page=2, x=72.0, y=500.0, width=200.0, height=12.0)
                ],
            },
            input_files={1: "chapters/intro.tex"},
        )
        # Look up by basename only
        pos = source_to_page(data, "intro.tex", 10)
        assert pos is not None
        assert pos.page == 2
        assert pos.y == 500.0

    def test_basename_fallback_nearest_line(self):
        """Test basename fallback finds nearest line."""
        data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("chapters/intro.tex", 5): [PDFPosition(page=1, x=72.0, y=400.0)],
                ("chapters/intro.tex", 20): [PDFPosition(page=1, x=72.0, y=600.0)],
            },
            input_files={1: "chapters/intro.tex"},
        )
        pos = source_to_page(data, "intro.tex", 18)
        assert pos is not None
        assert pos.y == 600.0  # line 20 is closest to 18

    def test_basename_not_used_when_exact_match_exists(self):
        """Test basename fallback is not reached when exact match exists."""
        data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 10): [PDFPosition(page=1, x=72.0, y=500.0)],
                ("other/main.tex", 10): [PDFPosition(page=3, x=72.0, y=100.0)],
            },
            input_files={1: "main.tex", 2: "other/main.tex"},
        )
        pos = source_to_page(data, "main.tex", 10)
        assert pos is not None
        assert pos.page == 1  # exact match, not basename

    def test_no_match_at_all(self):
        """Test returns None when neither exact nor basename matches."""
        data = SyncTeXData(
            pdf_to_source={},
            source_to_pdf={
                ("main.tex", 10): [PDFPosition(page=1, x=72.0, y=500.0)],
            },
            input_files={1: "main.tex"},
        )
        pos = source_to_page(data, "nonexistent.tex", 10)
        assert pos is None
