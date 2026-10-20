"""The terms a paper's reference papers use, listed for the agent that writes the paper.

An agent writing in a field tends to describe a concept in a phrase of its own, or coin a
name for it, where the papers of that field use one established term. The reference papers,
converted to Markdown, show which terms those are: a phrase several of them use for the same
thing. guide() brings the list at ``.tex-mcp-web/terms.md`` beside the configuration up to
date and names it among the files to read. It is rebuilt only when the Markdown files change.

What counts as a term:

- A run of two to five words between stopwords and punctuation, with at most one inner
  "of". Single words are left out: they are words an agent already uses.
- Used by at least a tenth of the papers, and by two at the least. A phrase one paper
  repeats is that paper's own name for something.
- Not a fragment of a longer term ("point spread" in "point spread function"), not a
  phrase every paper writes whatever its field ("proposed method", "number of stars"),
  and not a name from a paper's front matter, funding line or setup ("Intel Xeon").

Forms that differ in plural, hyphen, case, spacing ("light curve", "lightcurve") or the order
of "A of B" are one term, shown in the form most papers use. An acronym the papers define as "long form (ACR)" is attached
when two of them define it and two thirds of those that do mean the same long form.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from .config import Config, get_references_dir

TERMS_FILE = "terms.md"
STAMP_FILE = "terms.json"
LIMIT = 200
# Part of the stamp: a list built by an earlier rule is rebuilt. Raise it with each change
# to what counts.
VERSION = 3

STOP = set("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its itself just
me more most my no nor not now off on once only or other our ours out over own same she
should so some such than that the their theirs them themselves then there these they this
those through to too under until up very was we were what when where which while who whom
why will with would you your however thus therefore hence moreover furthermore since whereas
although though via per using used use uses based shown show shows showed propose proposes
proposed et al ie eg fig figure figures table tables section sections eq equation equations
respectively may might must shall one two three four five six seven eight nine ten first
second third etc whether either neither within without upon among across along around toward
towards onto let lets given make makes made thereby whose another every many much several
us due like unlike able
demonstrate demonstrates demonstrated achieve achieves achieved outperform outperforms
indicate indicates reveal reveals illustrate illustrates present presents introduce introduces
provide provides require requires enable enables allow allows obtain obtains perform performs
denote denotes represent represents describe describes consider considers employ employs
""".split())
# A phrase that starts with one of these names an instance ("existing approach"), not a concept.
LEAD_DROP = set("""
well existing novel various different previous following above original typical overall
corresponding respective specific certain particular current new similar further additional
main major key important significant better best good large small high low simple efficient
effective accurate fast traditional conventional prior recent state future same
""".split())
# "number of stars" counts something rather than naming it.
QUANTITY = {"number", "amount", "variety", "majority", "portion", "fraction", "lot", "kind", "sort"}
# Words every paper writes about its own work, whatever the field.
GENERIC = set("""
method approach result paper work experiment experimental performance problem solution
model technique framework study analysis case number time data set process step value
example way part total average evaluation evaluate evaluated comparison compared compare
algorithm system task input output function improvement large state art fact order term
previous related section baseline setting dataset impact effect level type kind application
range
""".split())
# A phrase ending in one of these names a kind of thing, and the term is the rest of it.
GENERIC_HEAD = set("""
technique method result approach problem information nature framework task scheme strategy
issue aspect purpose manner study setting setup metric decision solution process mechanism
value data
""".split())
# Front matter, funding and publisher lines differ from paper to paper, so the test for a
# line repeated across papers does not catch them.
FRONT = set("""
manuscript received accepted published revised recommended editor copyright permission
licensed downloaded grant funded supported acknowledgment acknowledgement university
institute department laboratory email author authors article journal conference proceedings
volume doi isbn issn arxiv preprint foundation
""".split())
# Nouns that end like adverbs.
LY_NOUNS = {"assembly", "supply", "family", "anomaly", "monopoly", "poly", "reply", "butterfly"}

WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*")
TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*|\S")
LONGEST = 5


def markdown_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*.md") if path.is_file())


def fingerprint(folder: Path) -> str:
    digest = hashlib.sha256(f"{VERSION}\n".encode())
    for path in markdown_files(folder):
        stat = path.stat()
        digest.update(f"{path.relative_to(folder)}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


def _key_line(line: str) -> str:
    return " ".join(line.lower().split())


def _body(text: str) -> list[str]:
    """The prose lines of one converted paper, headings marked with a leading '#'.

    The front matter before the abstract, the reference list, displayed math, tables,
    images and keyword lines are left out.
    """
    lines = text.splitlines()
    for index, line in enumerate(lines[:80]):
        if re.search(r"\babstract\b", line, re.I):
            lines = lines[index:]
            break
    kept, in_references, in_math = [], False, False
    for line in lines:
        stripped = line.strip()
        if in_math:
            in_math = not stripped.endswith("$$")
            continue
        if stripped.startswith("$$"):
            in_math = stripped == "$$" or not stripped.endswith("$$")
            continue
        if stripped.startswith("#"):
            in_references = bool(re.search(r"references|bibliography|acknowledg", stripped, re.I))
            kept.append("#" + stripped.lstrip("#").strip())
            continue
        if in_references or not stripped or stripped.startswith(("|", "![")):
            continue
        if re.match(r"^[-*]?\s*\[\d+\]", stripped) or re.match(r"^\W*(index terms|keywords|key words)\b", stripped, re.I):
            continue
        kept.append(stripped)
    return kept


def _papers(folder: Path) -> list[list[str]]:
    """One body per paper. A file with the same text as one already kept, or sharing half
    its lines and ten at the least with it, is another copy of that paper, under another
    name or in another version. The ten keep a short file whose few lines include a venue's
    standard line from passing for a copy.

    A file written mostly in another script, such as a translation or notes kept beside a
    paper, is not one: it shares no lines with the paper and would count its terms again.
    """
    papers, seen = [], []
    for path in markdown_files(folder):
        text = path.read_text(encoding="utf-8", errors="replace")
        letters = [letter for letter in text if letter.isalpha()]
        if sum(letter.isascii() for letter in letters) < 0.8 * len(letters):
            continue
        body = _body(text)
        own = {_key_line(line) for line in body if len(line.split()) >= 3}
        if not own or body in papers or any(
                len(own & other) >= max(10, 0.5 * min(len(own), len(other))) for other in seen):
            continue
        papers.append(body)
        seen.append(own)
    return papers


def _scrub(line: str) -> str:
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"\$[^$]*\$", " , ", line)
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"https?://\S+", " , ", line)
    line = re.sub(r"\[\d+(?:\s*[,–-]\s*\d+)*\]", " ", line)
    return re.sub(r"[*_`#>]", " ", line)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _key(words: list[str]) -> tuple[str, ...]:
    parts = []
    for word in words:
        parts.extend(word.lower().replace("'", "").split("-"))
    parts[-1] = _singular(parts[-1])
    return tuple(parts)


def _surface(words: list[str], sentence_start: bool) -> str:
    shown = list(words)
    first = shown[0]
    if sentence_start and not (first.isupper() and len(first) > 1):
        shown[0] = first[0].lower() + first[1:]
    return " ".join(shown)


def _runs(sentence: str):
    """Runs of words between stopwords and punctuation, kept together across one 'of'.
    Each comes with whether it starts the sentence and whether a number precedes it."""
    run, starts, at_start, previous, after_number = [], False, True, "", False
    for token in TOKEN.findall(sentence):
        lower = token.lower()
        if WORD.fullmatch(token) and (lower not in STOP or (lower == "of" and run)):
            if not run:
                starts, after_number = at_start, previous[:1].isdigit()
            run.append(token)
        else:
            if run:
                yield run, starts, after_number
            run = []
        at_start = False
        previous = token
    if run:
        yield run, starts, after_number


def _phrases(run: list[str], after_number: bool):
    # A phrase right after a number starts with its unit ("8 m telescope").
    for start in range(1 if after_number else 0, len(run)):
        for size in range(1, LONGEST + 1):
            words = run[start:start + size]
            if len(words) < size:
                break
            lower = [word.lower() for word in words]
            if lower[0] == "of" or lower[-1] == "of" or lower.count("of") > 1:
                continue
            if any(word.isdigit() or len(word) < 2 for word in words):
                continue
            if lower[0] in LEAD_DROP or (lower[0] in QUANTITY and len(lower) > 1 and lower[1] == "of"):
                continue
            if all(word in GENERIC or word == "of" for word in lower):
                continue
            if _singular(lower[-1]) in GENERIC_HEAD or any(word in FRONT for word in lower):
                continue
            if any(word.endswith("ly") and len(word) > 4 and word not in LY_NOUNS for word in lower):
                continue
            if lower[-1].endswith("ed") and len(lower[-1]) > 5:
                continue
            yield start, words


def _long_form(short: str, words: str) -> str | None:
    """Schwartz and Hearst (2003): the shortest tail of *words* whose letters spell
    *short* in order, the first letter starting a word."""
    s, w = len(short) - 1, len(words) - 1
    while s >= 0:
        letter = short[s].lower()
        if not letter.isalnum():
            s -= 1
            continue
        while (w >= 0 and words[w].lower() != letter) or (s == 0 and w > 0 and words[w - 1].isalnum()):
            w -= 1
        if w < 0:
            return None
        w -= 1
        s -= 1
    return words[words.rfind(" ", 0, w + 1) + 1:]


def _acronyms(sentence: str):
    for found in re.finditer(r"\(([A-Za-z0-9][A-Za-z0-9\-/]{1,9})\)", sentence):
        short = found.group(1)
        if len(short) > 2 and short.endswith("s") and short[-2].isupper():
            short = short[:-1]
        if sum(letter.isupper() for letter in short) < 2:
            continue
        before = WORD.findall(sentence[:found.start()])
        long = _long_form(short, " ".join(before[-min(len(short) + 5, len(short) * 2):]))
        if long and len(long.split()) >= 2 and short.lower() not in long.lower().split():
            yield short, long


class _Counts:
    def __init__(self, papers: list[list[str]]):
        self.freq: Counter = Counter()
        self.papers: dict = defaultdict(set)
        self.forms: dict = defaultdict(lambda: defaultdict(set))
        self.words_per_use: dict = defaultdict(Counter)
        self.case: dict = defaultdict(Counter)
        self.defined: dict = defaultdict(lambda: defaultdict(set))
        self.long_forms: dict = defaultdict(lambda: defaultdict(set))
        self.acronym_papers: dict = defaultdict(set)
        line_papers = defaultdict(set)
        for number, body in enumerate(papers):
            for line in body:
                if len(line.split()) >= 3:
                    line_papers[_key_line(line)].add(number)
        # A line several papers carry word for word is a running head, a footer, a
        # template heading or a shared formula.
        shared = {line for line, owners in line_papers.items() if len(owners) >= 2}
        for number, body in enumerate(papers):
            for line in body:
                if line.startswith("#") or _key_line(line) in shared:
                    continue
                for sentence in re.split(r"(?<=[.!?])\s+", _scrub(line)):
                    self._sentence(number, sentence)

    def _sentence(self, paper: int, sentence: str) -> None:
        defining = set()
        for short, long in _acronyms(sentence):
            self.defined[short][_key(long.split())].add(paper)
            self.long_forms[_key(long.split())][long].add(paper)
            defining |= set(long.split())
        for position, token in enumerate(WORD.findall(sentence)):
            if token.isupper() and len(token) > 1:
                plural = token.endswith("s") and token[:-1].isupper()
                self.acronym_papers[token[:-1] if plural else token].add(paper)
            # Case is learned from running text: a definition capitalizes its long form.
            if position > 0 and token not in defining:
                self.case[token.lower()]["lower" if token == token.lower() else "upper"] += 1
        for run, starts, after_number in _runs(sentence):
            for start, words in _phrases(run, after_number):
                key = _key(words)
                self.freq[key] += 1
                self.papers[key].add(paper)
                self.forms[key][_surface(words, starts and start == 0)].add(paper)
                self.words_per_use[key][len(words) >= 2] += 1

    def shown(self, forms: dict) -> str:
        """The form most papers write, lower case wherever running text uses lower case.
        Between forms as many papers write, the one with its words apart, then the shorter."""
        best = max(forms, key=lambda form: (len(forms[form]), form.count(" "), -len(form)))
        words = []
        for word in best.split():
            seen = self.case[word.lower()]
            if word[:1].isupper() and word[1:] == word[1:].lower() and seen["lower"] >= seen["upper"]:
                word = word.lower()
            words.append(word)
        return " ".join(words)


def build(folder: Path, limit: int = LIMIT) -> str:
    """The glossary of the papers under *folder*, as Markdown."""
    files = markdown_files(folder)
    if not files:
        raise ValueError(f"no Markdown files under {folder}")
    papers = _papers(folder)
    counts = _Counts(papers)
    total = len(papers)
    floor = max(2, math.ceil(0.1 * total))
    freq, owners = counts.freq, counts.papers
    kept = {key for key in freq if len(owners[key]) >= floor}
    longer = defaultdict(list)
    for key in kept:
        for size in range(1, len(key)):
            for start in range(len(key) - size + 1):
                part = key[start:start + size]
                if part in kept:
                    longer[part].append(key)

    def continued(key) -> int:
        """How often a named thing follows the phrase, as "function" follows "point spread"."""
        return sum(freq[other] for other in longer[key] if len(other) == len(key) + 1
                   and other[:len(key)] == key and other[-1] not in GENERIC and other[-1] not in GENERIC_HEAD)

    def proper(key) -> bool:
        return all(word[:1].isupper() for word in counts.shown(counts.forms[key]).split())

    terms = [key for key in kept
             if counts.words_per_use[key][True] >= counts.words_per_use[key][False]
             and not any(freq[other] >= 0.7 * freq[key] for other in longer[key])
             and continued(key) < 0.7 * freq[key] and not proper(key)]

    # Spellings that differ only in where the words are joined ("light curve", "lightcurve")
    # are one term, named as more papers write it. A single word joins a term this way,
    # though it is no term alone.
    spellings = defaultdict(list)
    for key in kept:
        spellings["".join(key)].append(key)
    listed = set(terms)
    joined = {key: max((other for other in spellings["".join(key)] if other in listed),
                       key=lambda other: (len(owners[other]), freq[other], other))
              for key in terms}
    forms, papers_of, spelled = {}, {}, {}
    for lead in set(joined.values()):
        group = spellings["".join(lead)]
        merged = defaultdict(set)
        for key in group:
            for surface, users_of in counts.forms[key].items():
                merged[surface] |= users_of
        forms[lead] = merged
        papers_of[lead] = set().union(*(owners[key] for key in group))
        shown = counts.shown(merged)
        spelled[lead] = sorted({counts.shown(counts.forms[key]) for key in group} - {shown})

    # "A of B" and "B A" are one term, named in the form more papers use.
    canonical = {lead: lead for lead in papers_of}
    for lead in papers_of:
        if "of" in lead:
            at = lead.index("of")
            other = _key(list(lead[at + 1:]) + list(lead[:at]))
            other = joined.get(other, other)
            if other in canonical and other != lead:
                kept_key, merged_key = (lead, other) if len(papers_of[lead]) >= len(papers_of[other]) else (other, lead)
                canonical[merged_key] = kept_key
    users = defaultdict(set)
    also = defaultdict(list)
    for lead in papers_of:
        users[canonical[lead]] |= papers_of[lead]
        also[canonical[lead]].extend(spelled[lead])
        if canonical[lead] != lead:
            also[canonical[lead]].append(counts.shown(forms[lead]))

    acronym_of = {}
    for short, meanings in counts.defined.items():
        long = max(meanings, key=lambda meaning: len(meanings[meaning]))
        defining = set().union(*meanings.values())
        using = counts.acronym_papers[short] | defining
        if len(defining) >= 2 and len(using) >= floor and len(meanings[long]) >= 2 / 3 * len(defining):
            key = canonical[joined[long]] if long in joined else long
            acronym_of[key] = short
            users[key] |= using
            if key not in forms:
                forms[key] = counts.long_forms[long]

    ranked = sorted(users, key=lambda key: (-len(users[key]), -freq[key], counts.shown(forms[key])))[:limit]
    lines = [
        "# Terms the reference papers use",
        "",
        f"From {total} papers in {folder}. Each line is a term, its acronym when the papers define one, "
        "how many of the papers use it, and other forms they also write. Name these concepts with "
        "these terms rather than describing them or coining new names.",
        "",
    ]
    for key in ranked:
        acronym = f", {acronym_of[key]}" if key in acronym_of else ""
        other = f", also {', '.join(also[key][:2])}" if also[key] else ""
        lines.append(f"- {counts.shown(forms[key])}{acronym} ({len(users[key])}){other}")
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> None:
    """Written whole and then put in place, so a reader never sees half a list."""
    written = path.with_name(f"{path.name}.{os.getpid()}")
    written.write_text(text, encoding="utf-8")
    os.replace(written, path)


def refresh(config: Config) -> Path | None:
    """Bring ``.tex-mcp-web/terms.md`` in line with the configured reference papers.

    Returns the list's path, or None when no references are configured, in which case an
    earlier list is removed. A folder that cannot be read gives a list that says why, so
    the agent made to read it learns of the problem.
    """
    if config.config_path is None:
        raise ValueError("configuration has no file path")
    state = config.config_path.resolve().parent / ".tex-mcp-web"
    terms, stamp = state / TERMS_FILE, state / STAMP_FILE
    folder = get_references_dir(config)
    if folder is None:
        terms.unlink(missing_ok=True)
        stamp.unlink(missing_ok=True)
        return None
    state.mkdir(exist_ok=True)
    try:
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a folder")
        mark = {"references": str(folder), "fingerprint": fingerprint(folder)}
        try:
            if json.loads(stamp.read_text(encoding="utf-8")) == mark and terms.is_file():
                return terms
        except (OSError, ValueError):
            pass
        _write(terms, build(folder))
        _write(stamp, json.dumps(mark))
    except (OSError, ValueError) as error:
        stamp.unlink(missing_ok=True)
        _write(terms, "# Terms the reference papers use\n\n"
                      f"The list could not be built from `references` in {config.config_path}: {error}. "
                      "Tell the user, who sets `references` to a folder of the papers converted to Markdown.\n")
    return terms
