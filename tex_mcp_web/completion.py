"""What the source editor offers to complete, read from the paper's own files.

Labels for \\ref and its kin, bib entries for \\cite and its kin, and the commands and
environments the paper defines. Text after an unescaped % is a comment and offers
nothing; each label and entry keeps its line so the list can say where it is.
"""

from __future__ import annotations

import re

_COMMENT = re.compile(r"(?<!\\)%.*$")
_LABEL = re.compile(r"\\label\s*\{([^{}]*)\}")


def _uncommented_lines(text: str) -> list[str]:
    return [_COMMENT.sub("", line) for line in text.split("\n")]


def labels(text: str, file: str) -> list[dict]:
    """Every ``\\label{name}`` outside a comment, in the order written."""
    found = []
    for number, line in enumerate(_uncommented_lines(text), start=1):
        for match in _LABEL.finditer(line):
            name = match.group(1).strip()
            if name:
                found.append({"name": name, "file": file, "line": number})
    return found


# ----- BibTeX -----

_ENTRY = re.compile(r"@\s*([A-Za-z]+)\s*[{(]")
# IEEEtranBSTCTL is a style switch for \bstctlcite, not a work to cite.
_NOT_ENTRIES = {"string", "comment", "preamble", "ieeetranbstctl"}


def _group_end(text: str, start: int, close: str) -> int:
    """Index just past the brace or quote that closes the group opening before *start*."""
    depth = 1
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if close == '"' and char == '"' and depth == 1:
            return index + 1
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and close == "}":
                return index + 1
        index += 1
    return len(text)


def _fields(body: str) -> dict[str, str]:
    """``name = {value}``, ``"value"`` or a bare word, by lower-cased name."""
    fields: dict[str, str] = {}
    index = 0
    pattern = re.compile(r"\s*,?\s*([A-Za-z][\w-]*)\s*=\s*")
    while True:
        match = pattern.match(body, index)
        if match is None:
            break
        name = match.group(1).lower()
        start = match.end()
        if start < len(body) and body[start] in "{\"":
            end = _group_end(body, start + 1, "}" if body[start] == "{" else '"')
            value = body[start + 1:end - 1]
        else:
            bare = re.match(r"[^,}\s]*", body[start:])
            end = start + (bare.end() if bare else 0)
            value = body[start:end]
        fields[name] = value
        index = end
    return fields


def _plain(value: str) -> str:
    value = re.sub(r'\\(["{}%&$_#])', r"\1", value)
    value = re.sub(r"\\[A-Za-z]+\s*", "", value)
    return re.sub(r"\s+", " ", value.replace("{", "").replace("}", "")).strip()


def _first_author(value: str) -> str:
    names = [name.strip() for name in re.split(r"\s+and\s+", _plain(value)) if name.strip()]
    if not names:
        return ""
    first = names[0]
    last = first.split(",")[0].strip() if "," in first else first.split()[-1]
    return last + (" et al." if len(names) > 1 else "")


def bib_entries(text: str, file: str) -> list[dict]:
    """Each entry's key with its title, first author and year, in the order written."""
    entries = []
    for match in _ENTRY.finditer(text):
        if match.group(1).lower() in _NOT_ENTRIES:
            continue
        close = "}" if text[match.end() - 1] == "{" else ")"
        end = _group_end(text, match.end(), "}") if close == "}" else text.find(")", match.end())
        head, _, body = text[match.end():end].partition(",")
        key = head.strip()
        if not key or re.search(r"\s", key):
            continue
        fields = _fields(body)
        entries.append({
            "key": key,
            "title": _plain(fields.get("title", "")),
            "author": _first_author(fields.get("author", "")),
            "year": _plain(fields.get("year", "")),
            "file": file,
            "line": text.count("\n", 0, match.start()) + 1,
        })
    return entries


# ----- What the paper defines -----

_NEW_COMMAND = re.compile(
    r"\\(?:new|renew|provide)command\*?\s*\{?\s*\\([A-Za-z@]+)\s*\}?"
    r"\s*(?:\[(\d)\])?\s*(\[[^\]]*\])?")
_MATH_OPERATOR = re.compile(r"\\DeclareMathOperator\*?\s*\{\s*\\([A-Za-z@]+)\s*\}")
_DEF = re.compile(r"\\def\s*\\([A-Za-z@]+)")
_NEW_ENVIRONMENT = re.compile(r"\\(?:new|renew)environment\s*\{([^{}]+)\}|\\newtheorem\*?\s*\{([^{}]+)\}")


def defined_commands(text: str) -> list[dict]:
    """Commands the source defines, with how many arguments must be given."""
    found: list[tuple[int, dict]] = []
    seen: set[str] = set()
    source = "\n".join(_uncommented_lines(text))
    for match in _NEW_COMMAND.finditer(source):
        count = int(match.group(2) or 0) - (1 if match.group(3) else 0)
        found.append((match.start(), {"name": match.group(1), "args": max(0, count)}))
    for pattern in (_MATH_OPERATOR, _DEF):
        for match in pattern.finditer(source):
            found.append((match.start(), {"name": match.group(1), "args": 0}))
    commands = []
    for _, command in sorted(found, key=lambda item: item[0]):
        if command["name"] not in seen:
            seen.add(command["name"])
            commands.append(command)
    return commands


def defined_environments(text: str) -> list[str]:
    """Environments the source defines, theorem-like ones included."""
    source = "\n".join(_uncommented_lines(text))
    names = []
    for match in _NEW_ENVIRONMENT.finditer(source):
        name = (match.group(1) or match.group(2)).strip()
        if name and name not in names:
            names.append(name)
    return names
