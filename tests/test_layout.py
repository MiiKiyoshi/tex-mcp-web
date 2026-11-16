"""Where the body ends and which paragraphs end in a short line, read from the PDF."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pymupdf
import pytest

from tex_mcp_web import layout

WORDS = "The harbour keeps a ledger of every tide that the keeper has seen since the lamp was lit "
LEFT, RIGHT = pymupdf.Rect(50, 60, 300, 740), pymupdf.Rect(312, 60, 562, 740)


def paragraphs(page: pymupdf.Page, column: pymupdf.Rect, texts: list[str]) -> None:
    """Justified paragraphs set one under another in a column, as a paper's are."""
    top = column.y0
    for text in texts:
        box = pymupdf.Rect(column.x0, top, column.x1, column.y1)
        left = page.insert_textbox(box, text, fontsize=10, align=pymupdf.TEXT_ALIGN_JUSTIFY)
        assert left > 0, "the text does not fit its column"
        top = column.y1 - left


def test_the_body_end_and_the_short_last_lines_are_read_from_a_two_column_page(tmp_path: Path) -> None:
    """Each column is read by itself. Of the paragraphs set, those whose last line is under
    a quarter of the column are named with their page, column and words, and no other
    line is. The body ends where the last column's text does, with the room under it
    counted in lines."""
    pdf = tmp_path / "paper.pdf"
    # Paragraphs a few words apart in length, so some end in a word or two and some do not.
    texts = [(WORDS * 4 + " ".join(WORDS.split()[:count])).strip() for count in range(1, 15, 2)]
    with pymupdf.open() as document:
        page = document.new_page(width=612, height=792)
        paragraphs(page, LEFT, [WORDS * 28])                      # two columns as full as each other
        paragraphs(page, RIGHT, [WORDS * 28])
        page = document.new_page(width=612, height=792)
        paragraphs(page, LEFT, texts[:4])
        paragraphs(page, RIGHT, texts[4:])
        document.save(pdf)

    lines, edges = layout.read_lines(pdf)
    assert sorted(edges) == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert all(abs(edges[key][0] - 50) < 2 and abs(edges[key][1] - 300) < 2 for key in ((1, 1), (2, 1)))

    report = layout.check(pdf, tmp_path)
    end = report["body_end"]
    assert (end["page"], end["column"], end["columns"]) == (2, 2, 2)
    last = [line for line in lines if (line.page, line.column) == (2, 2)]
    full = len([line for line in lines if (line.page, line.column) == (1, 1)])
    assert end["room_lines"] == full - len(last) and "source" not in end

    # Each paragraph's last line is the line its text ends with.
    endings = {}
    for text in texts:
        ending = max((line for line in lines if line.page == 2 and text.endswith(line.text)),
                     key=lambda line: len(line.text))
        endings[ending.text] = (ending.x1 - ending.x0) / 250
    expected = sorted(words for words, share in endings.items() if share < 0.25)
    assert 0 < len(expected) < len(texts)
    assert sorted(line["text"] for line in report["short_lines"]) == expected
    assert all(int(line["width"].rstrip("%")) < 25 and line["page"] == 2 for line in report["short_lines"])


def test_the_bibliography_is_found_by_a_handful_of_lookups() -> None:
    lines = [layout.Line(1, 1, 0, index, 10, index + 1, index + 1, 10, str(index)) for index in range(400)]
    asked = []

    def in_bibliography(line: layout.Line) -> bool:
        asked.append(line)
        return line.y0 >= 311

    assert layout._bibliography_start(lines, in_bibliography) == 311
    assert len(asked) < 14
    assert layout._bibliography_start(lines, lambda line: False) is None
    assert layout._bibliography_start([], lambda line: True) is None


@pytest.mark.skipif(shutil.which("latexmk") is None or shutil.which("synctex") is None,
                    reason="latexmk and synctex are required")
def test_the_body_of_a_typeset_paper_ends_before_its_bibliography(tmp_path: Path) -> None:
    """In a paper LaTeX set, the body's last line is the one before the bibliography, named
    by its source line, and a paragraph ending in a word is named by its source line too."""
    filler = "The harbour keeps a ledger of every tide that the keeper has seen since the lamp was lit. "
    (tmp_path / "paper.tex").write_text(
        "\\documentclass[twocolumn]{article}\n\\begin{document}\n"
        + (filler * 4).strip() + "\\linebreak Tides\n\n"
        + (filler * 40).strip() + "\n\n"
        "The last body sentence.\n"
        "\\input{paper.bbl}\n\\end{document}\n", encoding="utf-8")
    (tmp_path / "paper.bbl").write_text(
        "\\begin{thebibliography}{1}\n"
        + "".join(f"\\bibitem{{k{n}}} A. Keeper, ``Ledger {n} of the tides,'' Harbour Notes, 19{n:02d}.\n" for n in range(8))
        + "\\end{thebibliography}\n", encoding="utf-8")
    built = subprocess.run(["latexmk", "-pdf", "-bibtex-", "-synctex=1", "-interaction=nonstopmode", "paper.tex"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stdout[-2000:]

    report = layout.check(tmp_path / "paper.pdf", tmp_path)
    end = report["body_end"]
    assert (end["page"], end["column"], end["columns"]) == (1, 2, 2)
    assert end["source"] == "paper.tex:7" and end["room_lines"] > 5
    short = report["short_lines"]
    # SyncTeX places a paragraph's lines where the paragraph ends in the source.
    assert [(line["text"], line["source"]) for line in short] == [("Tides", "paper.tex:4")]
    # Each is placed on its page, for the review page to point at.
    with pymupdf.open(tmp_path / "paper.pdf") as doc:
        printed = doc[0].search_for("Tides")
    assert any(all(abs(got - want) < 3 for got, want in zip(short[0]["bbox"], hit)) for hit in printed)
    assert len(end["bbox"]) == 4


@pytest.mark.skipif(shutil.which("latexmk") is None or shutil.which("synctex") is None,
                    reason="latexmk and synctex are required")
def test_a_centered_caption_and_a_list_item_end_short_and_an_equation_and_a_reference_do_not(tmp_path: Path) -> None:
    """Centered text, as a table's caption, ends short too: its last line sits in the middle
    of the column under a wider centered one. A displayed equation under a full line does not."""
    filler = "The harbour keeps a ledger of every tide that the keeper has seen since the lamp was lit. "
    (tmp_path / "paper.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\n"
        "\\begin{center}Heights of the tides the keeper wrote in the ledger by the\\\\lamp.\\end{center}\n"
        + (filler * 4).strip() + "\n\\[ z = b \\]\n"
        + (filler * 4).strip() + "\n"
        "\\begin{itemize}\\item " + (filler * 2).strip() + "\\linebreak Item.\\end{itemize}\n"
        "\\begin{thebibliography}{1}\\bibitem{k} " + (filler * 2).strip() + "\\linebreak 1901.\\end{thebibliography}\n"
        "\\end{document}\n", encoding="utf-8")
    built = subprocess.run(["latexmk", "-pdf", "-synctex=1", "-interaction=nonstopmode", "paper.tex"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert built.returncode == 0, built.stdout[-2000:]

    short = layout.check(tmp_path / "paper.pdf", tmp_path).get("short_lines", [])
    # A list item's last line counts as a paragraph's does, and a bibliography entry's does not.
    assert [(line["text"], line["source"]) for line in short] == [("lamp.", "paper.tex:3"), ("Item.", "paper.tex:7")]
