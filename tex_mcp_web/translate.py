"""Reading aid: source paragraphs translated once and kept by their text.

The language is the one ``translation: {language: ...}`` names in the configuration.
A paragraph is translated only when its text has not been translated into that
language before, so a compile after an edit sends just the paragraphs that changed.
The translation runs ``claude -p`` without ``--model`` or ``--effort``: Claude Code
takes both from the user's own settings, so the translation follows whatever those
settings say.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Paragraphs sent in one call; a page rarely holds more.
BATCH = 24
TIMEOUT = 600

SYSTEM_PROMPT = """You translate paragraphs of a research paper, written in LaTeX source, into {language} for the paper's own authors, who read the translation beside the PDF.
Rules:
- Fluent, natural academic {language}; faithful, no omissions, no additions.
- Keep technical terms, method names, metrics and acronyms as the paper writes them; never transliterate them.
- Glossary terms listed in the request stay as written.
- Render LaTeX as readable text: drop formatting commands (\\textit, ~), write a citation as [*], name a reference such as Fig.~\\ref{{x}} by its kind (the figure), keep inline math as written without $ signs.
- A paragraph may contain an itemize list or a caption; translate each item as its own line starting with "- ".
{instructions}Return ONLY JSON: {{"t": {{"<id>": "<translation>", ...}}}} covering every id."""


def system_prompt(language: str, instructions: str | None = None) -> str:
    extra = "".join(f"- {line.strip()}\n" for line in (instructions or "").splitlines() if line.strip())
    return SYSTEM_PROMPT.format(language=language, instructions=extra)


def store_name(language: str) -> str:
    """``translations-<language>.json``; a name with no ASCII letters is hashed."""
    slug = re.sub(r"[^a-z0-9]+", "-", language.lower()).strip("-")
    return f"translations-{slug or hashlib.sha256(language.encode()).hexdigest()[:12]}.json"


@dataclass(frozen=True)
class Paragraph:
    file: str
    line_start: int
    line_end: int
    text: str

    @property
    def key(self) -> str:
        return paragraph_key(self.text)


def paragraph_key(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()[:24]


_INLINE_COMMENT = re.compile(r"(?<!\\)%.*$")
_COMMAND = re.compile(r"\\[A-Za-z@]+\*?")


def _has_prose(text: str) -> bool:
    words = re.findall(r"[A-Za-z]{2,}", _COMMAND.sub(" ", text))
    return len(words) >= 3


_FLOAT = re.compile(r"\\begin\{(figure|table)(\*?)\}.*?\\end\{\1\2\}", re.DOTALL)
_CAPTION = re.compile(r"\\(?:sub)?caption\*?(?:\[[^\]]*\])?\{")
_COMMENT_LINE = "%"


def _argument_end(text: str, start: int) -> int | None:
    """Index of the brace closing the argument that opens just before *start*."""
    depth = 1
    index = start
    while index < len(text):
        if text[index] == "\\":
            index += 2
            continue
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _captions_only(float_match: re.Match) -> str:
    """A figure or table blanked but for the words of its captions.

    The line breaks stay, so a caption keeps its source lines.
    """
    body = float_match.group(0)
    kept = [char if char == "\n" else " " for char in body]
    for caption in _CAPTION.finditer(body):
        end = _argument_end(body, caption.end())
        if end is not None:
            kept[caption.end():end] = body[caption.end():end]
    return "".join(kept)


def split_paragraphs(text: str, file: str) -> list[Paragraph]:
    """Blank-line separated paragraphs with % comments removed.

    Lines that are only a comment are dropped, so notes kept in comments are never
    translated. Of a figure or table only the caption is read: a drawing's labels and a
    table's cells are not prose. A paragraph without at least three words of prose
    (environment lines, \\label) is skipped.
    """
    paragraphs: list[Paragraph] = []
    current: list[tuple[int, str]] = []

    def flush() -> None:
        if current:
            body = "\n".join(line for _, line in current).strip()
            if _has_prose(body):
                paragraphs.append(Paragraph(file, current[0][0], current[-1][0], body))
            current.clear()

    uncommented = "\n".join(_COMMENT_LINE if raw.lstrip().startswith("%") else _INLINE_COMMENT.sub("", raw)
                            for raw in text.split("\n"))
    for number, raw in enumerate(_FLOAT.sub(_captions_only, uncommented).split("\n"), start=1):
        if raw == _COMMENT_LINE:
            continue
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        current.append((number, line))
    flush()
    return paragraphs


class TranslationStore:
    """``{key: {"text": ..., "at": ...}}`` in one JSON file per language."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        try:
            self._data: dict[str, dict] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str) -> str | None:
        entry = self._data.get(key)
        return entry["text"] if entry else None

    def put_many(self, items: dict[str, str]) -> None:
        if not items:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            for key, text in items.items():
                self._data[key] = {"text": text, "at": now}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)


def load_glossary(terms_md: Path) -> list[str]:
    """Term names from the reference term list (``- term (count), also ...``)."""
    try:
        lines = terms_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    terms = []
    for line in lines:
        match = re.match(r"-\s+(.+?)\s+\(\d+\)", line)
        if match:
            terms.append(match.group(1))
    return terms


def build_request(paragraphs: list[Paragraph], glossary: list[str]) -> tuple[str, list[str]]:
    ids = [f"p{index + 1}" for index in range(len(paragraphs))]
    blocks = {pid: paragraph.text for pid, paragraph in zip(ids, paragraphs)}
    prompt = ""
    if glossary:
        prompt += "Glossary (keep as written): " + ", ".join(glossary) + "\n\n"
    prompt += "Paragraphs (JSON):\n" + json.dumps(blocks, ensure_ascii=False)
    return prompt, ids


def parse_reply(reply: str, ids: list[str]) -> dict[str, str]:
    """The ``{"t": {...}}`` object in the model's reply, restricted to *ids*."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    translated = json.loads(reply[start:end + 1]).get("t", {})
    return {pid: str(translated[pid]).strip() for pid in ids if pid in translated and str(translated[pid]).strip()}


# Environment the review server inherits from the Claude Code session that started
# it; a child Claude must not read the session's own effort or nesting markers.
_SESSION_ENV = ("CLAUDE_EFFORT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


def run_claude(prompt: str, cwd: Path, system: str) -> str:
    """One ``claude -p`` call with the user's model and effort settings."""
    claude = shutil.which("claude")
    if claude is None:
        raise RuntimeError("claude CLI not found")
    env = {key: value for key, value in os.environ.items() if key not in _SESSION_ENV}
    completed = subprocess.run(
        [claude, "-p", "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
         "--no-session-persistence", "--output-format", "json",
         "--settings", json.dumps({"disableAllHooks": True}),
         "--system-prompt", system, prompt],
        cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=TIMEOUT, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[:300] or "claude failed")
    result = json.loads(completed.stdout)
    if result.get("is_error"):
        raise RuntimeError(str(result.get("result"))[:300])
    logger.info("translation call: %s, %.4f USD",
                ",".join((result.get("modelUsage") or {}).keys()), result.get("total_cost_usd") or 0)
    return str(result.get("result", ""))


class Translator:
    """Translates missing paragraphs in the background, one job at a time."""

    def __init__(self, store: TranslationStore, glossary: list[str], workdir: Path,
                 runner: Callable[[str, Path], str] | None = None, *,
                 language: str = "English", instructions: str | None = None):
        self.store = store
        self.glossary = glossary
        self.workdir = workdir
        self.language = language
        self.instructions = instructions
        system = system_prompt(language, instructions)
        self.runner = runner or (lambda prompt, cwd: run_claude(prompt, cwd, system))
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self.error: str | None = None

    def missing(self, paragraphs: list[Paragraph]) -> list[Paragraph]:
        seen: set[str] = set()
        out = []
        for paragraph in paragraphs:
            if self.store.get(paragraph.key) is None and paragraph.key not in seen:
                seen.add(paragraph.key)
                out.append(paragraph)
        return out

    def busy(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def translate(self, paragraphs: list[Paragraph]) -> None:
        """Translate what is missing now, in the calling thread."""
        with self._lock:
            todo = [p for p in self.missing(paragraphs) if p.key not in self._pending]
            self._pending.update(p.key for p in todo)
        try:
            for start in range(0, len(todo), BATCH):
                batch = todo[start:start + BATCH]
                prompt, ids = build_request(batch, self.glossary)
                found = parse_reply(self.runner(prompt, self.workdir), ids)
                self.store.put_many({p.key: found[pid] for pid, p in zip(ids, batch) if pid in found})
            self.error = None
        except Exception as exc:  # reported to the panel; the next request retries
            logger.warning("translation failed: %s", exc)
            self.error = str(exc)
        finally:
            with self._lock:
                self._pending.difference_update(p.key for p in todo)

    def start(self, paragraphs: list[Paragraph]) -> bool:
        """Start a background job for the missing paragraphs; False when none or busy."""
        if self.busy() or not self.missing(paragraphs):
            return False
        threading.Thread(target=self.translate, args=(paragraphs,), daemon=True).start()
        return True
