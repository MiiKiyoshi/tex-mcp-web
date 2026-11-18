"""Reading aid: source paragraphs translated once and kept by their text.

The language is the one ``translation: {language: ...}`` names in the configuration, and
the command that translates is ``translation: {command: ...}``: the command line of any LLM
tool that reads the request on its standard input, or where ``{prompt}`` stands in its
words, and prints the answer. A paragraph is translated only when its text has not been
translated into that language before, so a compile after an edit sends just the paragraphs
that changed.
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

from . import shared_file

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


def _title_of_preamble(lines: list[str]) -> list[str]:
    """*lines* with those before \\begin{document} made comment lines, all but the
    \\title's: packages and definitions are no prose to translate."""
    begin = next((i for i, line in enumerate(lines) if "\\begin{document}" in line), None)
    if begin is None:
        return lines
    kept, depth = [], 0
    for line in lines[:begin]:
        if depth == 0 and not line.lstrip().startswith("\\title"):
            kept.append("%" if line.strip() else line)
            continue
        depth += line.count("{") - line.count("}")
        kept.append(line)
    return kept + lines[begin:]


def split_paragraphs(text: str, file: str) -> list[Paragraph]:
    """Blank-line separated paragraphs with % comments removed.

    Lines that are only a comment are dropped, so notes kept in comments are never
    translated. Of a figure or table only the caption is read: a drawing's labels and a
    table's cells are not prose. Of the preamble, the lines before \\begin{document}, only
    the \\title is read. A paragraph without at least three words of prose (environment
    lines, \\label) is skipped.
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
                            for raw in _title_of_preamble(text.split("\n")))
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
            self._data: dict[str, dict] = json.loads(shared_file.read_text(path))
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
            shared_file.replace(tmp, self.path)


def load_glossary(terms_md: Path) -> list[str]:
    """Term names from the reference term list (``- term (count), also ...``)."""
    try:
        lines = shared_file.read_text(terms_md).splitlines()
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
    """The ``{"t": {...}}`` object in the reply, restricted to *ids*. The object is read from
    the reply's first ``{`` to its last ``}``, since a tool may wrap it in a code fence or
    a line of its own."""
    start, end = reply.find("{"), reply.rfind("}")
    try:
        translated = json.loads(reply[start:end + 1]).get("t", {}) if 0 <= start < end else None
    except ValueError:
        translated = None
    if translated is None:
        raise ValueError("no JSON object in the reply: " + (reply.strip()[:300] or "it printed nothing"))
    return {pid: str(translated[pid]).strip() for pid in ids if pid in translated and str(translated[pid]).strip()}


# Environment the review server inherits from the Claude Code session that started
# it; a child must not read the session's own effort or nesting markers.
_SESSION_ENV = ("CLAUDE_EFFORT", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")

# The word in a command's arguments that the request takes the place of.
PROMPT = "{prompt}"


def _windows_path(program: str, windows: bool = os.name == "nt") -> str:
    """*program* as Windows names it. Git Bash, the shell an agent works in there, gives a
    path as ``/c/Users/...``, which Windows itself does not know: its drive letter is put
    back as ``C:/Users/...``. Elsewhere, and for any other name, the program as given."""
    found = re.match(r"/([A-Za-z])/(.+)$", program) if windows else None
    return f"{found.group(1).upper()}:/{found.group(2)}" if found else program


def find_program(command: list[str]) -> str:
    """The program *command* names, as a path that can be run. Raises when there is none."""
    executable = shutil.which(_windows_path(command[0]))
    if executable is None:
        # A program named by its path is looked for there alone, and one named by a bare
        # word on the PATH the server was registered with, which is shorter than a shell's.
        if re.search(r"[/\\]", command[0]):
            raise RuntimeError(f"{command[0]} does not exist, or is not a program that can be run")
        raise RuntimeError(f"{command[0]} was not found on the server's PATH, "
                           "which is the shorter PATH it was registered with")
    return executable


def run_command(command: list[str], prompt: str, cwd: Path, system: str) -> str:
    """What *command* prints for one request: *system*, then *prompt*. The request goes to
    the command's standard input, or in place of ``{prompt}`` when its arguments hold it."""
    executable = find_program(command)
    request = f"{system}\n\n--- Request ---\n\n{prompt}"
    in_arguments = any(PROMPT in word for word in command[1:])
    env = {key: value for key, value in os.environ.items() if key not in _SESSION_ENV}
    completed = subprocess.run(
        [executable, *(word.replace(PROMPT, request) for word in command[1:])],
        cwd=cwd, env=env, input=None if in_arguments else request,
        stdin=subprocess.DEVNULL if in_arguments else None, capture_output=True, encoding="utf-8",
        timeout=TIMEOUT, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip()[:300]
                           or f"{command[0]} exited with {completed.returncode}")
    return completed.stdout


def command_label(command: list[str]) -> str:
    """The command's program by its name, without its folder or Windows' ``.exe``, and the
    model it names with ``--model X``, ``--model=X`` or ``-m X``."""
    name = re.split(r"[/\\]", command[0])[-1]
    name = name[:-4] if name.lower().endswith(".exe") else name
    for index, word in enumerate(command[1:], start=1):
        if word.startswith("--model="):
            return f"{name} · {word.removeprefix('--model=')}"
        if word in ("--model", "-m") and index + 1 < len(command):
            return f"{name} · {command[index + 1]}"
    return name


class Translator:
    """Translates missing paragraphs in the background, one job at a time, with *command*,
    or with *runner* when one is given."""

    def __init__(self, store: TranslationStore, glossary: list[str], workdir: Path,
                 runner: Callable[[str, Path], str] | None = None, *,
                 language: str = "English", instructions: str | None = None,
                 command: list[str] | None = None):
        self.store = store
        self.glossary = glossary
        self.workdir = workdir
        self.language = language
        self.instructions = instructions
        self.command = command
        system = system_prompt(language, instructions)
        self.runner = runner or (lambda prompt, cwd: run_command(command, prompt, cwd, system))
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self.error: str | None = None
        # Whether *error* is check()'s, that the command's program is missing.
        self._no_program = False

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

    def check(self) -> None:
        """Look for the command's program, without running it: while every paragraph shown was
        translated before, nothing runs the command, and this is how a program that is gone
        is found. *error* then says so, until a later check finds the program."""
        if self.command is None or self._pending:
            return
        try:
            find_program(self.command)
        except RuntimeError as exc:
            self.error, self._no_program = str(exc), True
        else:
            if self._no_program:
                self.error, self._no_program = None, False
