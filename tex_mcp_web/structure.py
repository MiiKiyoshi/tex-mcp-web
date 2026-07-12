"""LaTeX section parsing for SectionAnchor resolution.

tex-mcp-web v0.5.0 deliberately does *not* parse labels, citations, or
``\\input``/``\\include`` references.  The agent (Claude Code) can grep
for those itself with semantic understanding we can't match in regex.
The only thing we keep is section parsing, because it's load-bearing
for ``SectionAnchor`` staleness — given a section title or label, we
need to find the line range it occupies.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """A section-level heading (\\section/\\chapter/etc).

    Attributes:
        level: ``chapter``/``section``/``subsection``/``subsubsection``.
        title: Section title.
        file: Source file (relative to watch_dir).
        line: 1-indexed line number.
        label: Closest ``\\label{...}`` following the heading, or None.
        number: Heading number as LaTeX would print it ("3.2"), or None
            for starred (unnumbered) headings.
    """

    level: str
    title: str
    file: str
    line: int
    label: str | None = None
    number: str | None = None


@dataclass
class DocumentStructure:
    """Just sections.  v0.4.0 also tracked labels/citations/inputs; the
    agent does that better with Grep, so they're gone."""

    sections: list[Section] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Regex / helpers
# ---------------------------------------------------------------------------


_SECTION_PREFIX_RE = re.compile(
    r"\\(chapter|section|subsection|subsubsection)(\*)?(?:\[[^\]]*\])?"
)
_LEVELS = ("chapter", "section", "subsection", "subsubsection")
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
_INPUT_RE = re.compile(r"\\(?:input|include)\{([^}]+)\}")


def _count_preceding_backslashes(text: str, pos: int) -> int:
    count = 0
    j = pos - 1
    while j >= 0 and text[j] == "\\":
        count += 1
        j -= 1
    return count


def _extract_braced(text: str, pos: int) -> tuple[str, int] | None:
    """Extract content within matched braces starting at *pos*.

    Returns ``(content, end_pos)``; handles nested braces and ``\\{`` /
    ``\\}`` escapes.
    """
    if pos >= len(text) or text[pos] != "{":
        return None
    depth = 0
    for i in range(pos, len(text)):
        ch = text[i]
        if ch == "{" and _count_preceding_backslashes(text, i) % 2 == 0:
            depth += 1
        elif ch == "}" and _count_preceding_backslashes(text, i) % 2 == 0:
            depth -= 1
            if depth == 0:
                return text[pos + 1 : i], i + 1
    return None


def _strip_comment(line: str) -> str:
    """Strip an inline LaTeX comment.

    A ``%`` is a comment marker only when preceded by an even number of
    backslashes.
    """
    i = 0
    while i < len(line):
        if line[i] == "%" and _count_preceding_backslashes(line, i) % 2 == 0:
            return line[:i]
        i += 1
    return line


def _relative(path: Path, watch_dir: Path) -> str:
    try:
        return str(path.relative_to(watch_dir))
    except ValueError:
        return str(path)


def _resolve_input(arg: str, parent_dir: Path, watch_dir: Path) -> Path | None:
    """Resolve an ``\\input{...}`` argument to a real .tex file path.

    LaTeX appends `.tex` if missing, and resolves relative to either the
    parent file's directory or the project root.  We try both.
    """
    candidates = [
        (base / (arg + suffix)).resolve()
        for base in (parent_dir, watch_dir)
        for suffix in ("", ".tex")
    ]
    for p in candidates:
        if p.is_file():
            return p
    logger.debug("structure: could not resolve \\input{%s}; tried %s", arg, candidates)
    return None


def _files_reachable_from(main_file: Path, watch_dir: Path) -> list[Path]:
    """Walk \\input / \\include from *main_file* and return the set of
    .tex files actually used in the build.  Avoids picking up unbuilt
    variant files that happen to live in the same directory.
    """
    seen: set[Path] = set()
    out: list[Path] = []
    stack: list[Path] = [main_file.resolve()]
    while stack:
        path = stack.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        out.append(path)
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        children: list[Path] = []
        for raw_line in content.splitlines():
            if raw_line.lstrip().startswith("%"):
                continue
            for m in _INPUT_RE.finditer(_strip_comment(raw_line)):
                resolved = _resolve_input(m.group(1).strip(), path.parent, watch_dir)
                if resolved is not None and resolved not in seen:
                    children.append(resolved)
        # Pre-order DFS: reversed so the LIFO pop yields \input order.
        stack.extend(reversed(children))
    return out


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------


def _parse_sections(content: str, rel_path: str) -> list[tuple[Section, bool]]:
    """Extract section headings (and any immediately-following label).

    Returns ``(section, starred)`` pairs; numbering happens in
    :func:`parse_structure` once all files are collected in document order.
    """
    sections: list[tuple[Section, bool]] = []
    lines = content.splitlines()
    n = len(lines)

    for line_no, raw_line in enumerate(lines, start=1):
        if raw_line.lstrip().startswith("%"):
            continue
        line = _strip_comment(raw_line)
        for m in _SECTION_PREFIX_RE.finditer(line):
            pos = m.end()
            while pos < len(line) and line[pos] in " \t":
                pos += 1
            extracted = _extract_braced(line, pos)
            if not extracted:
                continue
            title = extracted[0].strip()

            # Look ahead a few lines for \label{...}
            label: str | None = None
            for ahead in range(line_no - 1, min(line_no + 3, n)):
                lm = _LABEL_RE.search(_strip_comment(lines[ahead]))
                if lm:
                    label = lm.group(1).strip()
                    break

            sections.append(
                (
                    Section(
                        level=m.group(1),
                        title=title,
                        file=rel_path,
                        line=line_no,
                        label=label,
                    ),
                    m.group(2) is not None,
                )
            )
    return sections


def _assign_numbers(parsed: list[tuple[Section, bool]]) -> list[Section]:
    """Reproduce LaTeX heading numbers over document-ordered sections.

    Starred headings get no number and do not advance counters.
    """
    counters = dict.fromkeys(_LEVELS, 0)
    out: list[Section] = []
    for section, starred in parsed:
        if starred:
            out.append(section)
            continue
        depth = _LEVELS.index(section.level)
        counters[section.level] += 1
        for deeper in _LEVELS[depth + 1 :]:
            counters[deeper] = 0
        parts = [str(counters[lvl]) for lvl in _LEVELS[: depth + 1] if counters[lvl] > 0]
        out.append(replace(section, number=".".join(parts)))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_structure(
    watch_dir: Path, main_file: Path | None = None
) -> DocumentStructure:
    """Parse sections across the .tex files used in the build.

    When *main_file* is provided, the parser starts there and follows
    ``\\input`` / ``\\include`` recursively.  This avoids picking up
    unbuilt variant files (e.g. ``paper-full-proofs.tex`` next to a
    main ``paper.tex``) that live in the same directory but aren't part
    of the active build.

    When *main_file* is None (legacy call sites), falls back to walking
    every ``*.tex`` under *watch_dir*.
    """
    structure = DocumentStructure()
    if main_file is not None and main_file.is_file():
        files = _files_reachable_from(main_file, watch_dir)
    else:
        files = sorted(watch_dir.rglob("*.tex"))
    parsed: list[tuple[Section, bool]] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("structure: failed to read %s", path)
            continue
        parsed.extend(_parse_sections(content, _relative(path, watch_dir)))
    structure.sections = _assign_numbers(parsed)
    return structure


def find_section(
    structure: DocumentStructure,
    title: str | None = None,
    label: str | None = None,
) -> tuple[str, int, int] | None:
    """Resolve a section anchor to ``(file, line_start, line_end)``.

    Match order:
        1. exact label match (when *label* is given)
        2. exact title match (case-sensitive)
        3. case-insensitive title match

    The end line is the line just before the next section in the same
    file, or ``-1`` to mean "to end-of-file" (caller can resolve by
    reading the file).  Returns None if no section matches.
    """
    sections = structure.sections
    if not sections:
        return None

    target: Section | None = None
    if label:
        target = next((s for s in sections if s.label == label), None)
    if target is None and title is not None:
        target = next((s for s in sections if s.title == title), None)
        if target is None:
            lc = title.lower()
            target = next((s for s in sections if s.title.lower() == lc), None)
    if target is None:
        return None

    same_file_after = [s for s in sections if s.file == target.file and s.line > target.line]
    end_line = (min(s.line for s in same_file_after) - 1) if same_file_after else -1
    return (target.file, target.line, end_line)
