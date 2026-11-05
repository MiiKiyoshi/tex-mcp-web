"""Edits to plain text in the form Overleaf's editor sends them.

An edit is a list of steps applied in turn, each an insert ``{"p": at, "i": text}`` or a
delete ``{"p": at, "d": text}`` that names the text it removes. A comment step
``{"p", "c", "t"}`` changes no text and is left out here. Positions count as JavaScript
does, in UTF-16 units, so a character outside the Basic Multilingual Plane counts two.

Two edits made at once on the same text are brought together by ``transform``: each is
moved past the other, so that applying either one and then the other's moved form gives
the same text. When both insert at one place, the left one's text comes first. Overleaf's
server moves an edit it receives the same way, with the edit received as the left one.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

Op = list[dict[str, Any]]


class Mismatch(ValueError):
    """An edit does not fit the text: a delete names text that is not there."""


# ----- Positions -----

def js_len(text: str) -> int:
    """The length JavaScript gives: characters beyond U+FFFF count two."""
    return len(text) + sum(1 for ch in text if ord(ch) > 0xFFFF)


def py_index(text: str, js_index: int) -> int:
    """The Python index of a JavaScript position in *text*."""
    if js_index < 0:
        raise Mismatch(f"position {js_index} is before the text")
    if not any(ord(ch) > 0xFFFF for ch in text):
        if js_index > len(text):
            raise Mismatch(f"position {js_index} is past the end of the text")
        return js_index
    units = 0
    for index, ch in enumerate(text):
        if units == js_index:
            return index
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > js_index:
            raise Mismatch(f"position {js_index} splits a character")
    if units == js_index:
        return len(text)
    raise Mismatch(f"position {js_index} is past the end of the text")


# ----- Applying -----

def text_only(op: Op) -> Op:
    """The steps that change text: comment steps dropped, each checked for shape."""
    steps = []
    for step in op:
        if not isinstance(step, dict) or not isinstance(step.get("p"), int):
            raise Mismatch(f"not an edit step: {step!r}")
        if "i" in step and isinstance(step["i"], str):
            steps.append({"p": step["p"], "i": step["i"]})
        elif "d" in step and isinstance(step["d"], str):
            steps.append({"p": step["p"], "d": step["d"]})
        elif "c" not in step:
            raise Mismatch(f"not an edit step: {step!r}")
    return steps


def apply(text: str, op: Op) -> str:
    for step in op:
        at = py_index(text, step["p"])
        if "i" in step:
            text = text[:at] + step["i"] + text[at:]
        elif "d" in step:
            end = at + len(step["d"])
            if text[at:end] != step["d"]:
                raise Mismatch(f"a delete at {step['p']} names {step['d'][:30]!r}, "
                               f"but the text there is {text[at:end][:30]!r}")
            text = text[:at] + text[end:]
    return text


# ----- Two edits at once -----

def _length(step: dict) -> int:
    return js_len(step["i"] if "i" in step else step["d"])


def _units(text: str, start: int, end: int | None = None) -> str:
    """text[start:end] with start and end in JavaScript units."""
    first = py_index(text, start)
    return text[first:] if end is None else text[first:py_index(text, end)]


def _push(dest: Op, step: dict) -> None:
    if ("i" in step and step["i"]) or ("d" in step and step["d"]):
        dest.append(step)


def _move(dest: Op, step: dict, other: dict, side: str) -> None:
    """Move one step past one step of another edit made at the same time."""
    p = step["p"]
    if "i" in step:
        if "i" in other:
            if p < other["p"] or (p == other["p"] and side == "left"):
                _push(dest, dict(step))
            else:
                _push(dest, {"p": p + _length(other), "i": step["i"]})
        else:
            end = other["p"] + _length(other)
            if p <= other["p"]:
                _push(dest, dict(step))
            elif p >= end:
                _push(dest, {"p": p - _length(other), "i": step["i"]})
            else:  # inside text the other removed: it goes where that text was
                _push(dest, {"p": other["p"], "i": step["i"]})
        return
    removed = step["d"]
    end = p + js_len(removed)
    if "i" in other:
        if other["p"] <= p:
            _push(dest, {"p": p + _length(other), "d": removed})
        elif other["p"] < end:  # the other inserted inside: delete around it
            cut = other["p"] - p
            _push(dest, {"p": p, "d": _units(removed, 0, cut)})
            _push(dest, {"p": p + _length(other), "d": _units(removed, cut)})
        else:
            _push(dest, dict(step))
        return
    other_end = other["p"] + _length(other)
    if p >= other_end:
        _push(dest, {"p": p - _length(other), "d": removed})
    elif end <= other["p"]:
        _push(dest, dict(step))
    else:  # both remove some of the same text: only what the other left is removed
        start, stop = max(p, other["p"]), min(end, other_end)
        if _units(removed, start - p, stop - p) != _units(other["d"], start - other["p"], stop - other["p"]):
            raise Mismatch("two deletes of the same place name different text")
        kept = (_units(removed, 0, other["p"] - p) if p < other["p"] else "") + (
            _units(removed, other_end - p) if end > other_end else "")
        _push(dest, {"p": min(p, other["p"]), "d": kept})


def transform_x(left: Op, right: Op) -> tuple[Op, Op]:
    """Two edits made at once on the same text, each moved past the other: applying
    ``left`` then the moved ``right``, or ``right`` then the moved ``left``, gives the
    same text. Where both insert at one place, ``left``'s text comes first."""
    moved_right: Op = []
    for step in right:
        moved_left: Op = []
        current: dict | None = step
        for index, mine in enumerate(left):
            if current is None:
                moved_left.extend(left[index:])
                break
            parts: Op = []
            _move(moved_left, mine, current, "left")
            _move(parts, current, mine, "right")
            if len(parts) == 1:
                current = parts[0]
            elif not parts:
                current = None
            else:  # the step split around this one: what is left of left meets both parts
                rest_left, rest_right = transform_x(left[index + 1:], parts)
                moved_left.extend(rest_left)
                moved_right.extend(rest_right)
                current = None
                break
        if current is not None:
            moved_right.append(current)
        left = moved_left
    return left, moved_right


# ----- An edit from two texts -----

_TOKEN = re.compile(r"\w+|\s+|.", re.S)


def _pieces(text: str) -> list[str]:
    return _TOKEN.findall(text)


def diff(old: str, new: str) -> Op:
    """An edit turning *old* into *new* that leaves as much in place as it can: the
    lines that are the same, and within lines that changed, the words that are. A delete
    removes only what changed, so a comment on the words around a change keeps them."""
    if old == new:
        return []
    start = 0
    limit = min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end = 0
    while end < limit - start and old[len(old) - 1 - end] == new[len(new) - 1 - end]:
        end += 1
    a, b = old[start:len(old) - end], new[start:len(new) - end]
    pairs: list[tuple[str, str, str]] = []   # (kind, removed, inserted), in order
    lines_a, lines_b = a.splitlines(keepends=True), b.splitlines(keepends=True)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False).get_opcodes():
        was, now = "".join(lines_a[i1:i2]), "".join(lines_b[j1:j2])
        if tag == "equal":
            pairs.append(("=", was, now))
        elif tag == "replace" and len(was) + len(now) <= 40000:
            words_a, words_b = _pieces(was), _pieces(now)
            for tag2, k1, k2, m1, m2 in difflib.SequenceMatcher(
                    None, words_a, words_b, autojunk=False).get_opcodes():
                pairs.append(("=" if tag2 == "equal" else "~", "".join(words_a[k1:k2]), "".join(words_b[m1:m2])))
        else:
            pairs.append(("~", was, now))
    op: Op = []
    at = js_len(new[:start])
    for kind, was, now in pairs:
        if kind == "=":
            at += js_len(now)
            continue
        if was:
            op.append({"p": at, "d": was})
        if now:
            op.append({"p": at, "i": now})
            at += js_len(now)
    return op


# ----- Comments on the text -----

def move_comments(comments: list[dict], op: Op) -> list[dict]:
    """Comment ranges ``{"t", "p", "n"}`` (thread, start, length in JavaScript units)
    after an edit, as Overleaf moves them: text inserted at or before a comment's start
    moves it, inside it widens it, and a delete takes away what it removes of it. A
    comment step ``{"p", "c", "t"}`` in the edit adds a comment."""
    moved = [dict(comment) for comment in comments]
    for step in op:
        if "c" in step and "t" in step:
            moved.append({"t": str(step["t"]), "p": step["p"], "n": js_len(step["c"])})
            continue
        if "i" in step:
            size = js_len(step["i"])
            for comment in moved:
                if step["p"] <= comment["p"]:
                    comment["p"] += size
                elif step["p"] < comment["p"] + comment["n"]:
                    comment["n"] += size
        elif "d" in step:
            start, size = step["p"], js_len(step["d"])
            stop = start + size
            for comment in moved:
                first, last = comment["p"], comment["p"] + comment["n"]
                if stop <= first:
                    comment["p"] -= size
                elif start < last:
                    comment["n"] -= min(stop, last) - max(start, first)
                    comment["p"] = min(first, start)
    return moved


def comments_removed(comments: list[dict], op: Op) -> list[str]:
    """Threads whose every commented character the edit deletes."""
    after = {comment["t"]: comment["n"] for comment in move_comments(comments, op)}
    return [comment["t"] for comment in comments if comment["n"] > 0 and after.get(comment["t"], 0) <= 0]
