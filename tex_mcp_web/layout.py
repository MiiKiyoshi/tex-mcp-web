"""Where the typeset paper stands against a page limit, read from the PDF.

Two things an edit to a sentence can change without the build saying so: where the body
ends, and which paragraphs end in a line of a word or two, which a shorter sentence would
pull up. Both are read from the PDF's text lines and named by source line through SyncTeX.

The body is what comes before the bibliography at the paper's end, whose lines SyncTeX
places in a ``.bbl`` file. A paragraph's last line is a line that starts at its column's left edge under a line
filling the column's width, and it is short when narrower than ``SHORT_LINE`` of the column.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pymupdf

from .synctex import selection_to_source_range

# A paragraph's last line narrower than this share of its column counts as short.
SHORT_LINE = 0.25
# How far, in points, a line's edge may be from its column's and still be at it.
EDGE = 2.0
# At most this many short lines are named, the first ones in reading order.
SHORT_LIMIT = 20


@dataclass
class Line:
    page: int          # 1-indexed
    column: int        # 1-indexed, left to right
    x0: float
    y0: float
    x1: float
    y1: float
    base: float        # the baseline's height on the page
    size: float
    text: str

    @property
    def rect(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


def _pieces(page: pymupdf.Page) -> list[tuple[float, float, float, float, float, float, str]]:
    """The page's runs of text as (x0, y0, x1, y1, base, size, text). Inline math, a change
    of font and the wide gaps of a loosely set line split a typeset line into several. The
    size is the one most of a run's letters are set in, and the baseline is theirs, so a
    subscript changes neither."""
    pieces = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [span for span in line["spans"] if span["text"].strip()]
            if not spans or abs(line["dir"][1]) > 0.01:
                continue
            x0, y0, x1, y1 = line["bbox"]
            size = Counter(round(span["size"], 1) for span in spans for _ in span["text"]).most_common(1)[0][0]
            base = next(span["origin"][1] for span in spans if round(span["size"], 1) == size)
            pieces.append((x0, y0, x1, y1, base, size, "".join(span["text"] for span in line["spans"]).strip()))
    return pieces


def _joined(pieces: list[tuple]) -> list[tuple]:
    """A column's runs as its lines, top to bottom: the runs on one baseline are one line."""
    lines: list[list[Any]] = []
    for piece in sorted(pieces, key=lambda piece: (round(piece[4]), piece[0])):
        last = lines[-1] if lines else None
        if last is not None and abs(piece[4] - last[4]) < 0.3 * last[5]:
            last[0], last[2] = min(last[0], piece[0]), max(last[2], piece[2])
            last[1], last[3] = min(last[1], piece[1]), max(last[3], piece[3])
            last[6] = f"{last[6]} {piece[6]}"
        else:
            lines.append(list(piece))
    return [tuple(line) for line in lines]


def _edge(values: list[float], pick: Callable[[list[float]], float]) -> float:
    """A column's edge: the outermost one that three lines or more share, to a point, so
    that an overfull line does not move it, nor the indent most lines of a bibliography
    start at. With no edge so shared, the one most lines share."""
    counts = Counter(round(value) for value in values)
    enough = min(3, max(counts.values()))
    return pick([value for value in values if counts[round(value)] >= enough])


def _columns(lines: list[tuple], width: float) -> list[tuple[float, float]]:
    """The page's columns as (left, right), from where its lines start and end: one column,
    or two when lines start at the left and again past the middle and few span both."""
    body = [line for line in lines if line[2] - line[0] > 0.2 * width]
    if not body:
        return []
    left = min(line[0] for line in body)
    right = max(line[2] for line in body)
    middle = (left + right) / 2
    crossing = [line for line in body if line[0] < middle - 0.05 * width and line[2] > middle + 0.05 * width]
    first = [line for line in body if line[2] <= middle + EDGE]
    second = [line for line in body if line[0] >= middle - EDGE]
    groups = [first, second] if len(first) >= 3 and len(second) >= 3 \
        and len(crossing) < min(len(first), len(second)) else [body]
    columns = []
    for group in groups:
        low, high = min(line[0] for line in group), max(line[2] for line in group)
        # A paragraph's last line starts at the edge without reaching across, so in a
        # column of few lines a short line at the others' left or just left of it says
        # where the edge is. It is told from a figure's label by the size the column's text is set in.
        size = Counter(line[5] for line in group).most_common(1)[0][0]
        starts = [line[0] for line in group] + [
            line[0] for line in lines
            if line[5] == size and line not in group and low - 3 * EDGE - 20 <= line[0] <= low + EDGE
            and line[2] <= high + EDGE]
        columns.append((_edge(starts, min), _edge([line[2] for line in group], max)))
    return columns


def _in_margin(line: tuple, neighbour: tuple | None, height: float) -> bool:
    """Whether a column's first or last line is a running head or a page number: near the
    page's top or in its lower third, and three lines or more apart from the text, or
    alone there."""
    outer = line[4] < 0.2 * height or line[4] > 0.66 * height
    return outer and (neighbour is None or abs(neighbour[4] - line[4]) > 3 * line[5])


def read_lines(pdf_path: Path) -> tuple[list[Line], dict[tuple[int, int], tuple[float, float]]]:
    """Every text line of the PDF in reading order, each in its page's column, and each
    column's (left, right). A line across both columns of a page is in neither and is
    left out, as a title or a wide caption is, and so are running heads and page numbers."""
    found: list[Line] = []
    edges: dict[tuple[int, int], tuple[float, float]] = {}
    with pymupdf.open(pdf_path) as pdf:
        for number, page in enumerate(pdf, start=1):
            lines = _pieces(page)
            columns = _columns(lines, page.rect.width)
            for index, (left, right) in enumerate(columns, start=1):
                # A line is in the column its middle is in, overfull or not.
                inside = _joined([line for line in lines if left - EDGE <= line[0]
                                  and (line[0] + line[2]) / 2 < right and line[2] <= right + 0.1 * (right - left)])
                if inside and _in_margin(inside[0], inside[1] if len(inside) > 1 else None, page.rect.height):
                    inside = inside[1:]
                if inside and _in_margin(inside[-1], inside[-2] if len(inside) > 1 else None, page.rect.height):
                    inside = inside[:-1]
                if inside:
                    edges[(number, index)] = (left, right)
                found += [Line(number, index, *line) for line in inside]
    return found, edges


def _mode(values: list[float]) -> float | None:
    return Counter(round(value, 1) for value in values).most_common(1)[0][0] if values else None


def _bibliography_start(lines: list[Line], in_bibliography: Callable[[Line], bool]) -> int | None:
    """The index of the first line the bibliography sets, or None when the paper ends in
    none. The bibliography is the paper's last part, so its first line is looked for by
    halving, with a handful of lookups."""
    last = next((index for index in range(len(lines) - 1, max(len(lines) - 4, -1), -1)
                 if in_bibliography(lines[index])), None)
    if last is None:
        return None
    body, bibliography = -1, last
    while bibliography - body > 1:
        middle = (body + bibliography) // 2
        if in_bibliography(lines[middle]):
            bibliography = middle
        else:
            body = middle
    return bibliography


def check(pdf_path: Path, watch_dir: Path) -> dict[str, Any]:
    """Where the body ends and which paragraphs end in a short line, for the agent and the
    page alike. ``body_end`` names the page and the column the body's last line is in, of
    how many columns, the room under it down to where the text block ends, in points and
    in lines of the body's leading, when the paper has full columns to show where that
    is, and its source. ``short_lines``
    names each short last line with its words, its share of the column's width and its
    source. Empty for a PDF with no text."""
    lines, edges = read_lines(pdf_path)
    if not lines:
        return {}
    size = _mode([line.size for line in lines for _ in line.text])
    by_column: dict[tuple[int, int], list[Line]] = {}
    for line in lines:
        by_column.setdefault((line.page, line.column), []).append(line)
    pitches = [below.base - above.base for column in by_column.values() for above, below in zip(column, column[1:])
               if abs(above.size - size) < 0.5 > abs(below.size - size) and 0 < below.base - above.base < 2 * size]
    # The body's leading: the commonest distance between its baselines, averaged over the
    # lines that keep it, since a tenth of a point off adds up down a column.
    usual = _mode(pitches)
    kept = [pitch for pitch in pitches if usual is not None and abs(pitch - usual) < 0.2]
    pitch = sum(kept) / len(kept) if kept else 1.2 * (size or 10)
    # Where the text block ends: the lowest baseline that more than one column reaches.
    # A paper with no two columns ending alike does not show where that is.
    bottoms = Counter(round(column[-1].base) for column in by_column.values())
    shared = [bottom for bottom, count in bottoms.items() if count > 1]
    floor = float(max(shared)) if shared else None

    def source(line: Line) -> str | None:
        placed = selection_to_source_range(pdf_path, line.page, [line.rect], watch_dir)
        return f"{placed[0]}:{placed[1]}" if placed is not None else None

    report: dict[str, Any] = {}
    start = _bibliography_start(lines, lambda line: (source(line) or "").rpartition(":")[0].endswith(".bbl"))
    body = lines if start is None else lines[:start]
    if body:
        last = body[-1]
        room = max(0.0, floor - last.base) if floor is not None else None
        report["body_end"] = {
            "page": last.page, "column": last.column,
            "columns": max(column for page, column in edges if page == last.page),
            **({"room_pt": round(room), "room_lines": int(room / pitch + 0.2)} if room is not None else {}),
            **({"source": at} if (at := source(last)) is not None else {}),
            "bbox": [round(edge, 1) for edge in last.rect],
        }
    inside = {id(line) for line in body}
    short = []
    for column in by_column.values():
        left, right = edges[(column[0].page, column[0].column)]
        for above, line in zip(column, column[1:]):
            # Set in one size, give or take the tenths a font is scaled by, one line under the other.
            if not (id(line) in inside and abs(line.size - above.size) < 0.5
                    and 0 < line.base - above.base < 1.6 * pitch):
                continue
            full = above.x1 >= right - EDGE and above.x0 <= left + 2 * line.size
            share = (line.x1 - line.x0) / (right - left)
            if full and line.x0 <= left + EDGE and share < SHORT_LINE:
                short.append((line, share))
    short.sort(key=lambda item: (item[0].page, item[0].column, item[0].base))
    if short:
        report["short_lines"] = [
            {"page": line.page, "column": line.column, "text": line.text, "width": f"{int(100 * share)}%",
             **({"source": at} if (at := source(line)) is not None else {}),
             "bbox": [round(edge, 1) for edge in line.rect]}
            for line, share in short[:SHORT_LIMIT]]
        if len(short) > SHORT_LIMIT:
            report["more_short_lines"] = len(short) - SHORT_LIMIT
    return report
