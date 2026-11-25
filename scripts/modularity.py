"""Put numbers on how modular the review pages' code is, to compare one state of it with another.

    python scripts/modularity.py <package folder>... [--json out.json] [--commits N] [--sites]
    python scripts/modularity.py --compare old.json new.json

A package folder is a tool's Python package. Its page is the JavaScript in static/: static/*.js,
static/page/*.js and static/shared/*.js (and .mjs). The code the tools share is in static/shared
and in the package's shared/. A shared folder with a MANIFEST.json shares the files it names, as
a copy does; one without, as where the shared code is written, shares all of its files. Every
other file is the tool's own, and each number is given for the tool's own code and for the
shared code apart.

The numbers, and what each misses. The limits they use are the constants after the imports.

Size. Code lines per module: lines that hold code, not blank and not comments only (in Python,
docstrings are left out too). The largest module, the median and the modules over LARGE lines.
It says nothing of how the parts depend on each other: a file split in two halves its size
whatever the halves share.

Imports. The ES imports between the page's modules, with ./, ../ and /static/ paths resolved:
fan-in and fan-out per module, and the cycles (strongly connected sets of more than one module).
A page whose wiring file creates every part and hands each the others imports in a star, so its
import graph shows no cycle however tangled the parts are; the part graph below shows those.

Wiring. A wiring call is a call of an imported factory: a function another module exports whose
name starts with create or attach, or whose first parameter is an object pattern. Its hooks are
the top-level keys of an object argument (a spread of an object literal of the same module counts
as that literal's keys) and each other argument. A hook names a part when its value refers to a
part's variable: a whole part, a part's function, an arrow calling a part, or a top-level value of
the module that does (a facade). Parts are the variables wiring calls are assigned to and the
namespace imports. A handle is `() => part`. A late binding is a reference to a part before the
call that creates it, or inside that call. The part graph has an edge from each part to the parts
its creation names. A call on a part at the top level once the parts exist, `part.method(...)`,
adds edges too: a function handed to it, an arrow or a part's function, listens, and each part it
names gets an edge to the part called, which it follows; a whole part handed to it gets an edge
from the part called, as a hook would. The graph's cycles are the parts that reach each other. It
misses parts reached through the module's own functions (a hook handed a function declared in the
file), and arrow parameters or locals that shadow a part's name.

Encapsulation. (a) Writes into an object a module was handed: `p.field = ...`, `p.field[key] =
...`, `p.field++`, `delete p.field`, or a call that changes a collection, as `p.field.add(...)`.
Here p is a parameter of an exported function or a name its body gives one: `const x = p`, `x =
p.field`, `x = f(p)` (as withDefaults(given)) or `const { a } = p`. (b) Reads of another part's
state: a property of a factory's parameter, or of such a name, read rather than called, as
`p.field` or `p.field.find(...)`. Not counted: a property the module also calls (a function handed
along), a call of a sub-part's function (`p.keys.hint()`), and DOM properties (`el.hidden`). A
module that hands an object literal of its own whole to a wiring call shares it: its own writes
and reads of a field that a module it was handed to touches under the same name are owner sites.
Missed: other aliases, a module taking a handed object under another name than its owner's, and
`Object.assign(p, ...)`. A call `x = f(p)` taken for an alias may make a new object, and a
factory handed plain data counts the reads of its fields.

Duplication. Code lines longer than DUPLICATE_LENGTH characters once trimmed, imports left out,
that are in the own code of two tools, or in two of one tool's own files; with the file pairs that
share the most. Lines repeated inside one file, mostly idioms, are kept apart. It sees copied
lines, not code rewritten to do the same thing.

Reuse. Shared code lines over all of a page's code lines.

Change coupling. Over the last N commits that touch the package, the pairs of own files changed
in the same commits most often, with the ratio of their common commits to the commits changing
either. A commit changing more than SWEEP own files (a move or a sweep) is left out. It is skipped
with a note for a folder outside a git repository.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import statistics
import subprocess
import sys
import tokenize as pytokenize
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

LARGE = 400  # code lines over which a module counts as large
DUPLICATE_LENGTH = 20  # characters a trimmed line must exceed to be compared
SWEEP = 25  # own files a commit may change and still count for change coupling
TOP = 5  # rows the readable report shows of each list
MANIFEST = "MANIFEST.json"

# ----- JavaScript tokens -----

PUNCTUATORS = sorted(
    [">>>=", "...", "===", "!==", "**=", "<<=", ">>=", ">>>", "&&=", "||=", "??=", "=>", "==", "!=", "<=", ">=",
     "&&", "||", "??", "?.", "++", "--", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "**", "<<", ">>"],
    key=len, reverse=True)
PUNCTUATOR = re.compile("|".join(re.escape(p) for p in PUNCTUATORS))
IDENTIFIER = re.compile(r"#?[A-Za-z_$\u0080-￿][\w$\u0080-￿]*")
NUMBER = re.compile(r"0[xXoObB][0-9a-fA-F_]+n?|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?n?")
# After these words an expression starts, so a / there begins a regular expression.
BEFORE_EXPRESSION = {"return", "typeof", "instanceof", "in", "of", "new", "delete", "void", "throw", "case", "do",
                     "else", "yield", "await"}
ASSIGNMENTS = {"=", "+=", "-=", "*=", "/=", "%=", "**=", "<<=", ">>=", ">>>=", "&=", "|=", "^=", "&&=", "||=", "??="}
MUTATORS = {"add", "delete", "clear", "set", "push", "pop", "shift", "unshift", "splice", "sort", "reverse", "fill",
            "copyWithin"}
DATA_METHODS = {"has", "get", "find", "findIndex", "findLast", "findLastIndex", "filter", "map", "flatMap", "flat",
                "some", "every", "forEach", "reduce", "reduceRight", "includes", "indexOf", "lastIndexOf", "slice",
                "at", "join", "keys", "values", "entries", "concat", "startsWith", "endsWith", "trim", "split",
                "toLowerCase", "toUpperCase", "replace", "replaceAll", "match", "test", "padStart", "padEnd"}
DOM_FIELDS = {"classList", "style", "dataset", "textContent", "innerHTML", "innerText", "outerHTML", "hidden",
              "disabled", "checked", "className", "scrollTop", "scrollLeft", "tabIndex", "placeholder",
              "selectionStart", "selectionEnd", "contentEditable", "parentElement", "parentNode", "children",
              "firstChild", "lastChild", "firstElementChild", "nextSibling", "previousSibling", "ownerDocument",
              "offsetWidth", "offsetHeight", "offsetTop", "offsetLeft", "clientWidth", "clientHeight",
              "scrollWidth", "scrollHeight", "isConnected", "onclick", "oninput", "onchange", "onkeydown"}
FACTORY_NAME = re.compile(r"^(create|attach)[A-Z0-9_$]")


@dataclass
class Token:
    kind: str  # "id", "num", "str", "tmpl" (template text), "re", "p" (punctuator)
    value: str
    line: int
    end: int  # the last line the token spans


class Unparsed(Exception):
    """A file whose brackets do not pair up, so that its tokens cannot be trusted."""


def tokenize_js(source: str) -> list[Token]:
    """The tokens of a JavaScript module, without its comments. Template literals give their text
    as tokens of their own, with the tokens of each ${...} between them."""
    tokens: list[Token] = []
    braces: list[str] = []  # "{" for a brace, "`" for a template's ${
    i, n, line = 0, len(source), 1

    def add(kind: str, start: int, stop: int) -> None:
        nonlocal line
        text = source[start:stop]
        tokens.append(Token(kind, text, line, line + text.count("\n")))
        line += text.count("\n")

    def template(start: int, at: int) -> int:
        j = at
        while j < n:
            if source[j] == "\\":
                j += 2
            elif source[j] == "`":
                j += 1
                break
            elif source.startswith("${", j):
                j += 2
                braces.append("`")
                break
            else:
                j += 1
        add("tmpl", start, min(j, n))
        return j

    def regex_may_start() -> bool:
        if not tokens:
            return True
        last = tokens[-1]
        if last.kind == "id":
            return last.value in BEFORE_EXPRESSION
        return last.kind == "p" and last.value not in (")", "]", "}")

    def regex_end(start: int) -> int | None:
        j, in_class = start + 1, False
        while j < n:
            ch = source[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "\n":
                return None
            if in_class:
                in_class = ch != "]"
            elif ch == "[":
                in_class = True
            elif ch == "/":
                j += 1
                while j < n and (source[j].isalnum() or source[j] == "_"):
                    j += 1
                return j
            j += 1
        return None

    while i < n:
        c = source[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace() or c == "﻿":
            i += 1
        elif source.startswith("//", i):
            j = source.find("\n", i)
            i = n if j < 0 else j
        elif source.startswith("/*", i):
            j = source.find("*/", i + 2)
            j = n if j < 0 else j + 2
            line += source.count("\n", i, j)
            i = j
        elif c in "'\"":
            j = i + 1
            while j < n and source[j] != c and source[j] != "\n":
                j += 2 if source[j] == "\\" else 1
            add("str", i, min(j + 1, n))
            i = j + 1
        elif c == "`":
            i = template(i, i + 1)
        elif c == "}" and braces and braces[-1] == "`":
            braces.pop()
            i = template(i, i + 1)
        elif c == "/" and regex_may_start() and (j := regex_end(i)) is not None:
            add("re", i, j)
            i = j
        elif (match := IDENTIFIER.match(source, i)) and not c.isdigit():
            add("id", i, match.end())
            i = match.end()
        elif (c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit())) and (
                match := NUMBER.match(source, i)):
            add("num", i, match.end())
            i = match.end()
        else:
            match = PUNCTUATOR.match(source, i)
            stop = match.end() if match and not (match.group() == "?." and source[i + 2:i + 3].isdigit()) else i + 1
            add("p", i, stop)
            if c == "{":
                braces.append("{")
            elif c == "}" and braces:
                braces.pop()
            i = stop
    return tokens


def pair_brackets(tokens: list[Token]) -> tuple[list[int], list[int]]:
    """For each token, the index of the bracket it pairs with (-1 for none), and its depth."""
    pair = [-1] * len(tokens)
    depth = [0] * len(tokens)
    stack: list[int] = []
    for i, token in enumerate(tokens):
        depth[i] = len(stack)
        if token.kind != "p":
            continue
        if token.value in "([{":
            stack.append(i)
        elif token.value in ")]}":
            if not stack or tokens[stack[-1]].value + token.value not in ("()", "[]", "{}"):
                raise Unparsed(f"line {token.line}: {token.value} closes nothing")
            j = stack.pop()
            pair[i], pair[j] = j, i
            depth[i] = len(stack)
    if stack:
        raise Unparsed(f"line {tokens[stack[-1]].line}: {tokens[stack[-1]].value} is never closed")
    return pair, depth


# ----- Code lines -----

def js_code_lines(tokens: list[Token]) -> set[int]:
    return {number for token in tokens for number in range(token.line, token.end + 1)}


PY_SKIPPED = {pytokenize.COMMENT, pytokenize.NL, pytokenize.NEWLINE, pytokenize.INDENT, pytokenize.DEDENT,
              pytokenize.ENCODING, pytokenize.ENDMARKER}


def py_code_lines(source: str) -> set[int]:
    lines: set[int] = set()
    try:
        for token in pytokenize.generate_tokens(io.StringIO(source).readline):
            if token.type not in PY_SKIPPED:
                lines.update(range(token.start[0], token.end[0] + 1))
        tree = ast.parse(source)
    except (pytokenize.TokenError, SyntaxError):
        return lines
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(
                    first.value.value, str):
                lines.difference_update(range(first.lineno, first.end_lineno + 1))
    return lines


# ----- A JavaScript module -----

@dataclass
class Function:
    name: str
    params: list[str]
    body: tuple[int, int]  # token range, the braces included
    factory: bool


@dataclass
class Module:
    name: str  # its path under static/, with / between folders
    shared: bool
    source: str
    tokens: list[Token] = field(default_factory=list)
    pair: list[int] = field(default_factory=list)
    depth: list[int] = field(default_factory=list)
    unparsed: str = ""
    imports: list[dict] = field(default_factory=list)  # {spec, target, line}
    bindings: dict[str, tuple[str | None, str]] = field(default_factory=dict)  # local -> (target, its name)
    namespaces: dict[str, str | None] = field(default_factory=dict)  # local -> target
    exports: dict[str, Function] = field(default_factory=dict)
    literals: dict[str, tuple[int, int]] = field(default_factory=dict)  # top-level `const x = {...}`
    consts: dict[str, tuple[int, int]] = field(default_factory=dict)  # every top-level initializer

    def __post_init__(self) -> None:
        self.lines = self.source.splitlines()

    def code_lines(self) -> set[int]:
        return js_code_lines(self.tokens)

    def text(self, index: int) -> str:
        """The line the token at *index* starts on, trimmed."""
        return self.lines[self.tokens[index].line - 1].strip()[:140]

    def value(self, index: int) -> str:
        return self.tokens[index].value if 0 <= index < len(self.tokens) else ""

    def is_reference(self, index: int) -> bool:
        """Whether the identifier at *index* names a variable: not a property after a dot, and not
        a key of an object literal."""
        if self.tokens[index].kind != "id" or self.value(index - 1) in (".", "?."):
            return False
        return not (self.value(index + 1) == ":" and self.value(index - 1) in ("{", ","))

    def end_of_expression(self, start: int, stop: int) -> int:
        """Where the expression at *start* ends: at a comma or semicolon at its own depth, before
        *stop*, or at the next statement of the top level."""
        i = start
        while i < stop:
            token = self.tokens[i]
            if token.kind == "p" and token.value in (",", ";"):
                return i
            if token.kind == "p" and token.value in "([{":
                i = self.pair[i] + 1
                continue
            if (i > start and self.depth[i] == 0 and token.kind == "id" and token.line > self.tokens[i - 1].end
                    and token.value in ("const", "let", "var", "function", "class", "export", "import", "async")):
                return i
            i += 1
        return stop

    def split(self, start: int, stop: int) -> list[tuple[int, int]]:
        """The comma-separated items of tokens[start:stop]."""
        items, i, first = [], start, start
        while i < stop:
            if self.tokens[i].kind == "p" and self.tokens[i].value in "([{":
                i = self.pair[i] + 1
                continue
            if self.tokens[i].value == "," and self.tokens[i].kind == "p":
                items.append((first, i))
                first = i + 1
            i += 1
        if first < stop:
            items.append((first, stop))
        return items


def pattern_names(module: Module, start: int, stop: int) -> list[str]:
    """The names a parameter list or a destructuring pattern in tokens[start:stop] binds."""
    names, i = [], start
    while i < stop:
        token = module.tokens[i]
        if token.kind == "p" and token.value in ("{", "["):
            names += pattern_names(module, i + 1, module.pair[i])
            i = module.pair[i] + 1
        elif token.kind == "p" and token.value == "=":
            i = module.end_of_expression(i + 1, stop)
        elif token.kind in ("id", "str") and module.value(i + 1) == ":":
            i += 2
        elif token.kind == "id":
            names.append(token.value)
            i += 1
        else:
            i += 1
    return names


def resolve(spec: str, module: str, names: set[str]) -> str | None:
    """The module an import names, as a name under static/, or None for one outside the page."""
    spec = re.split(r"[?#]", spec)[0]
    if spec.startswith("/static/"):
        parts = spec[len("/static/"):].split("/")
    elif spec.startswith("./") or spec.startswith("../"):
        parts = module.split("/")[:-1] + spec.split("/")
    else:
        return None
    resolved: list[str] = []
    for part in parts:
        if part == "..":
            if resolved:
                resolved.pop()
        elif part not in ("", "."):
            resolved.append(part)
    name = "/".join(resolved)
    return name if name in names else None


def read_imports(module: Module, names: set[str]) -> None:
    tokens = module.tokens
    for i, token in enumerate(tokens):
        if token.kind != "id" or token.value not in ("import", "export") or module.value(i - 1) == ".":
            continue
        if token.value == "import" and module.value(i + 1) == "(":
            if tokens[i + 2].kind == "str":
                spec = tokens[i + 2].value[1:-1]
                module.imports.append({"spec": spec, "target": resolve(spec, module.name, names), "line": token.line})
            continue
        if module.depth[i] != 0:
            continue
        j = i + 1
        while j < len(tokens) and tokens[j].kind != "str" and tokens[j].value != ";" and not (
                tokens[j].kind == "id" and tokens[j].value == "from"):
            if token.value == "export" and tokens[j].kind == "id" and tokens[j].value in (
                    "function", "const", "let", "var", "class", "async", "default"):
                break
            j += 1
        if j < len(tokens) and tokens[j].value == "from":
            j += 1
        if j >= len(tokens) or tokens[j].kind != "str":
            continue
        spec = tokens[j].value[1:-1]
        target = resolve(spec, module.name, names)
        module.imports.append({"spec": spec, "target": target, "line": token.line})
        if token.value != "import":
            continue
        k = i + 1
        while k < j:
            if tokens[k].value == "*" and module.value(k + 1) == "as":
                module.namespaces[tokens[k + 2].value] = target
                k += 3
            elif tokens[k].value == "{":
                for first, last in module.split(k + 1, module.pair[k]):
                    item = [t.value for t in tokens[first:last]]
                    if item:
                        module.bindings[item[-1]] = (target, item[0])
                k = module.pair[k] + 1
            elif tokens[k].kind == "id" and tokens[k].value != "from":
                module.bindings[tokens[k].value] = (target, "default")
                k += 1
            else:
                k += 1


def read_declarations(module: Module) -> None:
    """The module's exported functions and its top-level initializers."""
    tokens, exported = module.tokens, set()
    for i, token in enumerate(tokens):
        if module.depth[i] or token.kind != "id":
            continue
        if token.value == "export" and module.value(i + 1) == "{":
            for first, last in module.split(i + 2, module.pair[i + 1]):
                exported.add(tokens[first].value)
    for i, token in enumerate(tokens):
        if module.depth[i] or token.kind != "id":
            continue
        is_export = module.value(i - 1) == "export" or (module.value(i - 1) == "default"
                                                          and module.value(i - 2) == "export")
        if token.value == "function" or (token.value == "async" and module.value(i + 1) == "function"):
            j = i + (2 if token.value == "async" else 1)
            if module.value(j) == "*":
                j += 1
            name = tokens[j].value if tokens[j].kind == "id" else "default"
            if tokens[j].kind == "id":
                j += 1
            if module.value(j) != "(":
                continue
            close = module.pair[j]
            opener = module.value(close + 1) == "{"
            if (is_export or module.value(i - 2) == "export" or name in exported) and opener:
                module.exports[name] = Function(name, pattern_names(module, j + 1, close),
                                                (close + 1, module.pair[close + 1]),
                                                bool(FACTORY_NAME.match(name)) or module.value(j + 1) == "{")
        elif token.value in ("const", "let", "var") and tokens[i + 1].kind == "id" and module.value(i + 2) == "=":
            name, start = tokens[i + 1].value, i + 3
            stop = module.end_of_expression(start, len(tokens))
            module.consts[name] = (start, stop)
            if module.value(start) == "{" and module.pair[start] == stop - 1:
                module.literals[name] = (start, stop)
            if is_export or name in exported:
                arrow = function_at(module, start)
                if arrow:
                    params, body, first = arrow
                    module.exports[name] = Function(name, params, body,
                                                    bool(FACTORY_NAME.match(name)) or first == "{")


def function_at(module: Module, start: int) -> tuple[list[str], tuple[int, int], str] | None:
    """An arrow or function expression at *start*: its parameters, its body and the first token
    of its parameters."""
    i = start + (1 if module.value(start) == "async" else 0)
    if module.value(i) == "function":
        i += 1
        if module.tokens[i].kind == "id":
            i += 1
    if module.value(i) == "(":
        params, first, close = pattern_names(module, i + 1, module.pair[i]), module.value(i + 1), module.pair[i]
    elif module.tokens[i].kind == "id" and module.value(i + 1) == "=>":
        params, first, close = [module.tokens[i].value], module.tokens[i].value, i
    else:
        return None
    j = close + 1
    if module.value(j) == "=>":
        j += 1
    if module.value(j) == "{":
        return params, (j, module.pair[j]), first
    return params, (j, module.end_of_expression(j, len(module.tokens))), first


def parse(module: Module) -> None:
    try:
        module.tokens = tokenize_js(module.source)
        module.pair, module.depth = pair_brackets(module.tokens)
    except Unparsed as error:
        module.unparsed = str(error)
        module.tokens, module.pair, module.depth = [], [], []


# ----- Wiring -----

@dataclass
class Call:
    module: str
    index: int
    factory: str
    part: list[str]
    binding: int  # the first token of what the call is assigned to
    args: tuple[int, int]
    hooks: list[tuple[str, int, int]]  # (name, start, stop) of each hook's value


def factory_target(modules: dict[str, Module], target: str | None, name: str) -> bool:
    if target is not None and target in modules and name in modules[target].exports:
        return modules[target].exports[name].factory
    return bool(FACTORY_NAME.match(name))


def wiring_calls(modules: dict[str, Module], module: Module) -> list[Call]:
    calls, tokens = [], module.tokens
    for i, token in enumerate(tokens):
        if token.kind != "id" or module.value(i + 1) != "(" or module.value(i - 1) in (".", "?.", "function"):
            if not (token.kind == "id" and module.value(i - 1) == "." and module.value(i + 1) == "("
                    and module.tokens[i - 2].value in module.namespaces and module.is_reference(i - 2)):
                continue
            target, name, callee = module.namespaces[tokens[i - 2].value], token.value, i - 2
            label = f"{tokens[i - 2].value}.{name}"
        elif token.value in module.bindings:
            (target, name), callee, label = module.bindings[token.value], i, token.value
        else:
            continue
        if not factory_target(modules, target, name):
            continue
        before = callee - 1
        if module.value(before) == "await":
            before -= 1
        part: list[str] = []
        binding = callee
        if module.value(before) == "=" and before > 0:
            if tokens[before - 1].kind == "id" and module.value(before - 2) not in (".", "?."):
                part, binding = [tokens[before - 1].value], before - 1
            elif module.value(before - 1) == "}":
                binding = module.pair[before - 1]
                part = pattern_names(module, binding + 1, before - 1)
        args = (i + 2, module.pair[i + 1])
        calls.append(Call(module.name, callee, label, part, binding, args, hooks_of(module, *args)))
    return calls


def hooks_of(module: Module, start: int, stop: int) -> list[tuple[str, int, int]]:
    hooks = []
    for index, (first, last) in enumerate(module.split(start, stop)):
        if module.value(first) == "{" and module.pair[first] == last - 1:
            hooks += entries(module, first + 1, last - 1)
        else:
            hooks.append((f"#{index + 1}", first, last))
    return hooks


def entries(module: Module, start: int, stop: int) -> list[tuple[str, int, int]]:
    found, tokens = [], module.tokens
    for first, last in module.split(start, stop):
        head = tokens[first]
        if head.value == "..." and last - first == 2 and tokens[first + 1].value in module.literals:
            found += entries(module, module.literals[tokens[first + 1].value][0] + 1,
                             module.literals[tokens[first + 1].value][1] - 1)
        elif head.value == "...":
            found.append(("..." + "".join(t.value for t in tokens[first + 1:last]), first + 1, last))
        elif last - first == 1:
            found.append((head.value, first, last))
        elif module.value(first + 1) == ":":
            found.append((head.value.strip("'\""), first + 2, last))
        elif head.value == "[" and module.value(module.pair[first] + 1) == ":":
            found.append(("".join(t.value for t in tokens[first:module.pair[first] + 1]), module.pair[first] + 2,
                          last))
        else:
            name = next((t.value for t in tokens[first:last] if t.kind == "id" and t.value not in (
                "async", "get", "set")), head.value)
            found.append((name, first, last))
    return found


def references(module: Module, start: int, stop: int, names) -> list[int]:
    return [i for i in range(start, stop) if module.tokens[i].value in names and module.is_reference(i)]


def wiring(modules: dict[str, Module], module: Module) -> dict:
    calls = wiring_calls(modules, module)
    created: dict[str, tuple[int, int, tuple[int, int]]] = {}
    for call in calls:
        for name in call.part:
            created.setdefault(name, (call.binding, call.index, call.args))
    parts = set(created) | set(module.namespaces)
    # A top-level initializer that is not a wiring call stands for the parts it names.
    facades = {name: set(module.tokens[i].value for i in references(module, *span, parts))
               for name, span in module.consts.items() if name not in created}
    shown = []
    for call in calls:
        hooks = []
        for name, first, last in call.hooks:
            named = set()
            for i in references(module, first, last, parts | set(facades)):
                value = module.tokens[i].value
                named |= {value} if value in parts else facades[value]
            named -= set(call.part)
            hooks.append({"hook": name, "parts": sorted(named)})
        shown.append({"module": module.name, "line": module.tokens[call.index].line, "factory": call.factory,
                      "part": ", ".join(call.part) or f"{call.factory}()", "hooks": hooks})
    late, handles = [], []
    for name, (binding, index, (first, last)) in created.items():
        for i in references(module, 0, len(module.tokens), {name}):
            if (binding <= i < index or module.value(i - 1) in ("let", "const", "var")
                    or (module.value(i + 1) == "=" and i < index)):
                continue
            site = {"module": module.name, "line": module.tokens[i].line, "part": name,
                    "late": i < index or first <= i < last, "text": module.text(i)}
            if site["late"]:
                late.append(site)
            if (module.value(i - 1) == "=>" and module.value(i - 2) == ")" and module.value(i - 3) == "("
                    and module.value(i + 1) in (",", ")")):
                handles.append(site)
    # A call on a part at the top level after the parts exist, `part.method(...)`, connects it to
    # the parts its arguments name. A function handed so, an arrow or a part's function, is a
    # listener: the parts it names follow the part called, and depend on it. A whole part handed
    # so is the part called taking it, as a hook does.
    subscriptions = []
    for i, token in enumerate(module.tokens):
        if (module.depth[i] or token.value not in created or not module.is_reference(i)
                or module.value(i + 1) not in (".", "?.") or module.tokens[i + 2].kind != "id"
                or module.value(i + 3) != "("):
            continue
        follows, taken = set(), set()
        for first, last in module.split(i + 4, module.pair[i + 3]):
            if last - first == 1 and module.tokens[first].value in parts:
                taken.add(module.tokens[first].value)
                continue
            for j in references(module, first, last, parts | set(facades)):
                value = module.tokens[j].value
                follows |= {value} if value in parts else facades[value]
        follows.discard(token.value)
        taken.discard(token.value)
        if follows or taken:
            subscriptions.append({"module": module.name, "line": token.line, "part": token.value,
                                  "method": module.tokens[i + 2].value, "follows": sorted(follows),
                                  "taken": sorted(taken)})
    return {"calls": shown, "late": late, "handles": handles, "subscriptions": subscriptions,
            "spans": [call.args for call in calls]}


# ----- Encapsulation -----

def chain(module: Module, index: int) -> tuple[list[str], int]:
    """The properties read off the identifier at *index*, as ["field", "[]", "add"], and the
    index of the token after them."""
    props, j = [], index + 1
    while j < len(module.tokens):
        if module.value(j) in (".", "?.") and module.tokens[j + 1].kind == "id":
            props.append(module.tokens[j + 1].value)
            j += 2
        elif module.value(j) == "?." and module.value(j + 1) == "[":
            props.append("[]")
            j = module.pair[j + 1] + 1
        elif module.value(j) == "[" and module.tokens[j].kind == "p":
            props.append("[]")
            j = module.pair[j] + 1
        else:
            break
    if module.value(j) == "?." and module.value(j + 1) == "(":
        j += 1
    return props, j


def accesses(module: Module, name: str, spans: list[tuple[int, int]], skip: list[tuple[int, int]],
             owner: str, reads: bool) -> tuple[list[dict], list[dict]]:
    """The writes into, and the reads of, the fields of the object *name* in token *spans*."""
    found = []
    for i in sorted({i for start, stop in spans for i in range(start, stop)}):
        if (module.tokens[i].value != name or not module.is_reference(i)
                or module.value(i + 1) not in (".", "?.", "[") or any(a <= i < b for a, b in skip)):
            continue
        props, after = chain(module, i)
        if props and props[0] not in DOM_FIELDS:
            found.append((i, props, after))
    called = {tuple(props) for i, props, after in found if module.value(after) == "("}
    writes, read = [], []
    for i, props, after in found:
        site = {"module": module.name, "line": module.tokens[i].line, "object": name, "field": props[0],
                "owner": owner, "text": module.text(i)}
        if module.value(after) in ASSIGNMENTS or module.value(after) in ("++", "--") or module.value(i - 1) in (
                "++", "--", "delete"):
            writes.append({**site, "kind": "assign"})
        elif module.value(after) == "(" and len(props) >= 2 and props[-1] in MUTATORS:
            writes.append({**site, "kind": "change"})
        elif not reads:
            continue
        elif module.value(after) == "(":
            if len(props) >= 2 and props[-1] in DATA_METHODS:
                read.append({**site, "kind": "read"})
        elif tuple(props) not in called:
            read.append({**site, "kind": "read"})
    return writes, read


def aliases(module: Module, function: Function) -> list[tuple[str, tuple[int, int]]]:
    """The parameters of *function* and the names its body gives them (`const x = p`, `const x =
    p.field`, `const x = f(p)`, as withDefaults(given), and `const { a, b } = p`), each with the
    tokens it holds in: a parameter the whole body, a name the rest of the block declaring it."""
    found = [(name, function.body) for name in function.params]
    names = set(function.params)
    start, stop = function.body
    for i in range(start, stop):
        if module.tokens[i].kind != "id" or module.tokens[i].value not in ("const", "let", "var"):
            continue
        target = i + 1
        if module.value(target) in ("{", "["):
            equals = module.pair[target] + 1
        elif module.tokens[target].kind == "id":
            equals = target + 1
        else:
            continue
        if module.value(equals) != "=":
            continue
        first, last = equals + 1, module.end_of_expression(equals + 1, stop)
        words = [t.value for t in module.tokens[first:last]]
        plain = bool(words) and words[0] in names and all(
            w in (".", "?.") if k % 2 == 0 else module.tokens[first + 1 + k].kind == "id"
            for k, w in enumerate(words[1:]))
        wrapped = len(words) == 4 and module.tokens[first].kind == "id" and words[1] == "(" and words[2] in names
        if not (plain or wrapped):
            continue
        block = next((module.pair[j] for j in range(i - 1, start - 1, -1)
                      if module.value(j) == "{" and module.pair[j] > i), stop)
        given = (pattern_names(module, target + 1, module.pair[target]) if module.value(target) in ("{", "[")
                 else [module.tokens[target].value])
        names.update(given)
        found += [(name, (i, block)) for name in given]
    return found


def encapsulation(module: Module, handed: set[str]) -> tuple[list[dict], list[dict]]:
    writes, reads = [], []
    for function in module.exports.values():
        spans = defaultdict(list)
        for name, span in aliases(module, function):
            spans[name].append(span)
        for name, held in spans.items():
            w, r = accesses(module, name, held, [], function.name, function.factory)
            writes += w
            reads += r
    for name in sorted(handed):
        span = module.literals[name]
        skip = [span] + [f.body for f in module.exports.values() if name in f.params]
        w, r = accesses(module, name, [(0, len(module.tokens))], skip, "(owner)", True)
        writes += w
        reads += r
    return writes, reads


def handed_out(module: Module, call_spans: list[tuple[int, int]]) -> set[str]:
    """The module's object literals handed whole to a wiring call, or exported."""
    handed = set()
    for name in module.literals:
        exported = module.value(module.literals[name][0] - 4) == "export"
        whole = [i for a, b in call_spans for i in references(module, a, b, {name})
                 if module.value(i + 1) not in (".", "?.", "[")]
        if whole or exported:
            handed.add(name)
    return handed


# ----- A tool -----

def shared_names(folder: Path) -> set[str] | None:
    """The files a shared folder shares, or None when it has no manifest and shares all of them."""
    manifest = folder / MANIFEST
    if not manifest.is_file():
        return None
    return set(json.loads(manifest.read_text(encoding="utf-8"))["files"])


def is_shared(path: Path, root: Path) -> bool:
    if path.parent.name != "shared" or path.parent.parent != root:
        return False
    names = shared_names(path.parent)
    return names is None or path.name in names


def js_files(package: Path) -> list[Path]:
    static = package / "static"
    found = []
    for folder in (static, static / "page", static / "shared"):
        if folder.is_dir():
            found += sorted(p for p in folder.iterdir() if p.is_file() and p.suffix in (".js", ".mjs"))
    return found


def py_files(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*.py") if "__pycache__" not in p.parts
                  and (package / "static") not in p.parents)


def sizes(lines: dict[str, int]) -> dict:
    if not lines:
        return {"modules": 0, "lines": 0, "largest": ["", 0], "median": 0, "large": [], "per_module": {}}
    largest = max(lines, key=lambda name: lines[name])
    return {"modules": len(lines), "lines": sum(lines.values()), "largest": [largest, lines[largest]],
            "median": statistics.median(lines.values()),
            "large": sorted((name for name, count in lines.items() if count > LARGE), key=lambda n: -lines[n]),
            "per_module": dict(sorted(lines.items()))}


def cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """The strongly connected sets of more than one node (Tarjan's algorithm, without recursion)."""
    index, low, on, stack, found, counter = {}, {}, set(), [], [], [0]
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph.get(root, ()))))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on.add(root)
        while work:
            node, children = work[-1]
            child = next(children, None)
            if child is None:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    members = []
                    while True:
                        member = stack.pop()
                        on.discard(member)
                        members.append(member)
                        if member == node:
                            break
                    if len(members) > 1:
                        found.append(sorted(members))
            elif child not in index:
                index[child] = low[child] = counter[0]
                counter[0] += 1
                stack.append(child)
                on.add(child)
                work.append((child, iter(sorted(graph.get(child, ())))))
            elif child in on:
                low[node] = min(low[node], index[child])
    return sorted(found, key=lambda members: (-len(members), members))


def change_coupling(package: Path, own: set[str], commits: int) -> dict:
    try:
        root = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=package, check=True,
                                   capture_output=True, text=True).stdout.strip())
        log = subprocess.run(["git", "log", f"-n{commits}", "--no-merges", "--name-only", "--format=%x00%H", "--",
                              "."], cwd=package, check=True, capture_output=True, text=True,
                             encoding="utf-8").stdout
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return {"skipped": "the folder is not in a git repository"}
    prefix = package.resolve().relative_to(root.resolve()).as_posix()
    changed, together, counted, swept = Counter(), Counter(), 0, 0
    for entry in log.split("\x00")[1:]:
        names = {line[len(prefix) + 1:] if prefix != "." else line for line in entry.splitlines()[1:]
                 if line.strip()}
        files = sorted(names & own)
        if len(files) > SWEEP:
            swept += 1
            continue
        if files:
            counted += 1
        changed.update(files)
        together.update(combinations(files, 2))
    pairs = [{"files": [a, b], "together": n, "ratio": round(n / (changed[a] + changed[b] - n), 2),
              "changes": [changed[a], changed[b]]} for (a, b), n in together.items() if n >= 2]
    pairs.sort(key=lambda pair: (-pair["together"], -pair["ratio"], pair["files"]))
    return {"commits": counted, "sweeps": swept, "pairs": pairs}


def measure(package: Path, commits: int) -> dict:
    static = package / "static"
    paths = js_files(package)
    modules = {p.relative_to(static).as_posix(): Module(p.relative_to(static).as_posix(), is_shared(p, static),
                                                         p.read_text(encoding="utf-8")) for p in paths}
    for module in modules.values():
        parse(module)
    for module in modules.values():
        if not module.unparsed:
            read_imports(module, set(modules))
            read_declarations(module)

    js = {"own": {}, "shared": {}}
    for module in modules.values():
        js["shared" if module.shared else "own"][module.name] = len(module.code_lines())
    python = {"own": {}, "shared": {}}
    py_text = {}
    for path in py_files(package):
        name = path.relative_to(package).as_posix()
        py_text[name] = path.read_text(encoding="utf-8")
        python["shared" if is_shared(path, package) else "own"][name] = len(py_code_lines(py_text[name]))

    graph = {name: {i["target"] for i in m.imports if i["target"]} for name, m in modules.items()}
    fan_in = Counter(target for targets in graph.values() for target in targets)
    imports = {"edges": sum(len(t) for t in graph.values()),
               "fan_out": {name: len(graph[name]) for name in sorted(graph)},
               "fan_in": {name: fan_in[name] for name in sorted(graph)},
               "cycles": cycles(graph),
               "outside": sorted({i["spec"] for m in modules.values() for i in m.imports if not i["target"]})}

    calls, late, handles, writes, reads, part_graph = [], [], [], [], [], defaultdict(set)
    subscriptions = []
    for module in modules.values():
        if module.unparsed:
            continue
        wired = wiring(modules, module)
        calls += wired["calls"]
        late += [{**site, "shared": module.shared} for site in wired["late"]]
        handles += [{**site, "shared": module.shared} for site in wired["handles"]]
        subscriptions += [{**site, "shared": module.shared} for site in wired["subscriptions"]]
        for site in wired["subscriptions"]:
            for part in site["follows"]:
                part_graph[f"{module.name}:{part}"].add(f"{module.name}:{site['part']}")
            for part in site["taken"]:
                part_graph[f"{module.name}:{site['part']}"].add(f"{module.name}:{part}")
        w, r = encapsulation(module, handed_out(module, wired["spans"]))
        writes += [{**site, "shared": module.shared} for site in w]
        reads += [{**site, "shared": module.shared} for site in r]
        for call in wired["calls"]:
            for hook in call["hooks"]:
                for part in hook["parts"]:
                    part_graph[f"{module.name}:{call['part']}"].add(f"{module.name}:{part}")
    for call in calls:
        call["shared"] = modules[call["module"]].shared
        call["naming_parts"] = sum(1 for hook in call["hooks"] if hook["parts"])
    # An owner's access to its handed-out object crosses into another module when a module it was
    # handed to touches the same field, as far as the names show.
    touched = {(site["object"], site["module"], site["field"]) for site in writes + reads
               if site["owner"] != "(owner)"}
    for site in writes + reads:
        if site["owner"] == "(owner)":
            site["crossing"] = any(o == site["object"] and m != site["module"] and f == site["field"]
                                   for o, m, f in touched)

    git_names = {f"static/{n}" for n in js["own"]} | set(python["own"])
    coupling = change_coupling(package, git_names, commits)

    shared_lines = sum(js["shared"].values())
    page_lines = shared_lines + sum(js["own"].values())
    return {
        "package": package.name,
        "describe": describe(package),
        "unparsed": {name: m.unparsed for name, m in modules.items() if m.unparsed},
        "js": {"own": sizes(js["own"]), "shared": sizes(js["shared"])},
        "python": {"own": sizes(python["own"]), "shared": sizes(python["shared"])},
        "imports": imports,
        "wiring": {"calls": calls, "late": late, "handles": handles, "subscriptions": subscriptions, "part_cycles": [[n.split(":", 1)[1] for n in c]
                                                                for c in cycles(part_graph)]},
        "writes": writes,
        "reads": reads,
        "reuse": {"shared": shared_lines, "page": page_lines,
                  "ratio": round(shared_lines / page_lines, 3) if page_lines else 0},
        "change_coupling": coupling,
        "_lines": own_lines(modules, py_text, python["own"]),
    }


def describe(package: Path) -> str:
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], cwd=package, check=True,
                              capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return ""


def own_lines(modules: dict[str, Module], py_text: dict[str, str], py_own: dict[str, int]) -> list[tuple]:
    """The tool's own code lines worth comparing, as (file, line number, trimmed text)."""
    found = []
    for module in modules.values():
        if module.shared or module.unparsed:
            continue
        lines = module.source.splitlines()
        found += [(f"static/{module.name}", n, lines[n - 1].strip()) for n in sorted(module.code_lines())]
    for name in py_own:
        lines = py_text[name].splitlines()
        found += [(name, n, lines[n - 1].strip()) for n in sorted(py_code_lines(py_text[name]))]
    return [(f, n, text) for f, n, text in found
            if len(text) > DUPLICATE_LENGTH and not re.match(r"(import|from)\s", text)]


def duplication(tools: dict[str, dict]) -> dict:
    where: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for tool, result in tools.items():
        for file, number, text in result.pop("_lines"):
            where[text].append((tool, file, number))
    within = {tool: [] for tool in tools}
    pairs, files = Counter(), Counter()
    for text, places in where.items():
        by_tool = defaultdict(list)
        for tool, file, number in places:
            by_tool[tool].append([file, number])
        for tool, found in by_tool.items():
            if len(found) > 1:
                within[tool].append({"text": text, "at": found})
        for a, b in combinations(sorted(by_tool), 2):
            pairs[f"{a} | {b}"] += 1
        for (t1, f1), (t2, f2) in combinations(sorted({(t, f) for t, f, _ in places}), 2):
            files[(f"{t1}/{f1}", f"{t2}/{f2}")] += 1
    for tool, result in tools.items():
        result["duplication"] = {"lines": len(within[tool]),
                                 "across_files": sum(1 for d in within[tool] if len({f for f, _ in d["at"]}) > 1),
                                 "sites": within[tool]}
    return {"tool_pairs": dict(pairs.most_common()),
            "file_pairs": [{"files": list(k), "lines": n} for k, n in files.most_common(50)]}


# ----- Numbers to compare -----

def summary(result: dict) -> dict:
    own_calls = [c for c in result["wiring"]["calls"] if not c["shared"]]
    own_late = [s for s in result["wiring"]["late"] if not s["shared"]]
    own_handles = [s for s in result["wiring"]["handles"] if not s["shared"]]
    own_subscriptions = [s for s in result["wiring"]["subscriptions"] if not s["shared"]]
    cycles_ = result["imports"]["cycles"]
    coupling = result["change_coupling"]
    return {
        "js own lines": result["js"]["own"]["lines"],
        "js own modules": result["js"]["own"]["modules"],
        "js own largest": result["js"]["own"]["largest"][1],
        "js own median": result["js"]["own"]["median"],
        "js own large": len(result["js"]["own"]["large"]),
        "js shared lines": result["js"]["shared"]["lines"],
        "py own lines": result["python"]["own"]["lines"],
        "py own largest": result["python"]["own"]["largest"][1],
        "py own large": len(result["python"]["own"]["large"]),
        "import edges": result["imports"]["edges"],
        "import cycles": len(cycles_),
        "modules in import cycles": sum(len(c) for c in cycles_),
        "wiring calls": len(own_calls),
        "hooks": sum(len(c["hooks"]) for c in own_calls),
        "hooks naming a part": sum(c["naming_parts"] for c in own_calls),
        "late bindings": len(own_late),
        "subscriptions": len(own_subscriptions),
        "handles () => part": len(own_handles),
        "late handles": sum(1 for s in own_handles if s["late"]),
        "largest part cycle": max((len(c) for c in result["wiring"]["part_cycles"]), default=0),
        "writes into handed objects": sum(1 for s in result["writes"] if not s["shared"] and s["owner"] != "(owner)"),
        "owner writes of handed-out objects": sum(1 for s in result["writes"]
                                                  if not s["shared"] and s["owner"] == "(owner)" and s["crossing"]),
        "reads of handed state": sum(1 for s in result["reads"] if not s["shared"] and s["owner"] != "(owner)"),
        "owner reads of handed-out objects": sum(1 for s in result["reads"]
                                                 if not s["shared"] and s["owner"] == "(owner)" and s["crossing"]),
        "shared writes into handed objects": sum(1 for s in result["writes"] if s["shared"] and s.get("crossing", True)),
        "shared reads of handed state": sum(1 for s in result["reads"] if s["shared"] and s.get("crossing", True)),
        "duplicate lines within": result["duplication"]["lines"],
        "duplicate lines across own files": result["duplication"]["across_files"],
        "reuse ratio": result["reuse"]["ratio"],
        "co-changing pairs": len(coupling.get("pairs", [])),
    }


# ----- Output -----

def table(rows: list[list], header: list[str]) -> str:
    cells = [header] + [[str(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths)))
             for row in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def grouped(sites: list[dict], key) -> str:
    counts = Counter(key(site) for site in sites)
    return ", ".join(f"{name} {n}" for name, n in counts.most_common(TOP)) or "none"


def report(result: dict, everything: bool) -> str:
    out = [f"== {result['package']}" + (f" ({result['describe']})" if result["describe"] else "")]
    for name, why in result["unparsed"].items():
        out.append(f"  not parsed: {name}: {why}")
    rows = []
    for label, part in (("own JS", result["js"]["own"]), ("shared JS", result["js"]["shared"]),
                        ("own Python", result["python"]["own"]), ("shared Python", result["python"]["shared"])):
        rows.append([label, part["modules"], part["lines"], f"{part['largest'][0]} {part['largest'][1]}",
                     part["median"], len(part["large"])])
    out.append(table(rows, ["Size", "modules", "code lines", "largest", "median", f"over {LARGE}"]))
    large = result["js"]["own"]["large"] + result["python"]["own"]["large"]
    if large:
        out.append(f"  own modules over {LARGE}: {', '.join(large)}")
    imports = result["imports"]
    top_in = sorted(imports["fan_in"].items(), key=lambda kv: -kv[1])[:3]
    top_out = sorted(imports["fan_out"].items(), key=lambda kv: -kv[1])[:3]
    out.append(f"Imports: {imports['edges']} edges; most imported {', '.join(f'{n} {c}' for n, c in top_in)}; "
               f"most importing {', '.join(f'{n} {c}' for n, c in top_out)}")
    out.append("  cycles: " + ("; ".join(" ".join(c) for c in imports["cycles"]) or "none"))
    s = summary(result)
    out.append(f"Wiring: {s['wiring calls']} calls with {s['hooks']} hooks, {s['hooks naming a part']} naming "
               f"another part; {s['handles () => part']} handles () => part, {s['late handles']} of them late; "
               f"{s['late bindings']} late bindings in all; {s['subscriptions']} calls on parts after they exist")
    own_calls = sorted((c for c in result["wiring"]["calls"] if not c["shared"]), key=lambda c: -len(c["hooks"]))
    for call in own_calls[:TOP]:
        out.append(f"  {call['module']}:{call['line']} {call['part']} = {call['factory']}: {len(call['hooks'])} "
                   f"hooks, {call['naming_parts']} naming parts")
    for members in result["wiring"]["part_cycles"]:
        out.append(f"  parts reaching each other ({len(members)}): {' '.join(members)}")
    writes = [w for w in result["writes"] if not w["shared"]]
    reads = [r for r in result["reads"] if not r["shared"]]
    out.append("Encapsulation (own code):")
    for label, sites in (("writes into handed objects", [w for w in writes if w["owner"] != "(owner)"]),
                         ("reads of handed state", [r for r in reads if r["owner"] != "(owner)"]),
                         ("owner writes of handed-out objects", [w for w in writes if w["owner"] == "(owner)"
                                                                 and w["crossing"]]),
                         ("owner reads of handed-out objects", [r for r in reads if r["owner"] == "(owner)"
                                                                and r["crossing"]])):
        out.append(f"  {label}: {len(sites)}" + (f" ({grouped(sites, lambda x: x['module'])}; "
                                                  f"{grouped(sites, lambda x: x['object'] + '.' + x['field'])})"
                                                  if sites else ""))
        if everything:
            out += [f"    {x['module']}:{x['line']} {x['text']}" for x in sites]
    out.append(f"  shared code: {s['shared writes into handed objects']} writes, {s['shared reads of handed state']} "
               "reads")
    duplicate = result["duplication"]
    out.append(f"Duplication within: {duplicate['across_files']} lines in two own files or more, "
               f"{duplicate['lines']} twice or more in all")
    out.append(f"Reuse: {result['reuse']['shared']} shared of {result['reuse']['page']} page code lines "
               f"({result['reuse']['ratio']:.0%})")
    coupling = result["change_coupling"]
    if "skipped" in coupling:
        out.append(f"Change coupling: skipped, {coupling['skipped']}")
    else:
        out.append(f"Change coupling: {coupling['commits']} commits ({coupling['sweeps']} sweeps left out)")
        for pair in coupling["pairs"][:TOP]:
            out.append(f"  {pair['files'][0]} + {pair['files'][1]}: {pair['together']} together, "
                       f"ratio {pair['ratio']}")
    return "\n".join(out)


def totals(results: dict[str, dict], shared: dict) -> str:
    rows = []
    for name, result in results.items():
        s = summary(result)
        rows.append([name, s["js own lines"], s["js own modules"], s["js own largest"], s["js own large"],
                     s["import cycles"], f"{s['hooks']}/{s['hooks naming a part']}",
                     f"{s['late bindings']}/{s['handles () => part']}", s["largest part cycle"],
                     s["writes into handed objects"] + s["owner writes of handed-out objects"],
                     s["reads of handed state"] + s["owner reads of handed-out objects"],
                     s["duplicate lines across own files"], f"{s['reuse ratio']:.0%}"])
    out = [table(rows, ["Tool", "own JS", "modules", "largest", f">{LARGE}", "import cycles", "hooks/naming",
                        "late/handles", "part cycle", "writes", "reads", "dup", "reuse"])]
    if shared["tool_pairs"]:
        out.append("Lines in two tools' own code: " + ", ".join(f"{k} {v}" for k, v in shared["tool_pairs"].items()))
        out.append("Files sharing the most lines:")
        out += [f"  {p['lines']:>4}  {p['files'][0]}  {p['files'][1]}" for p in shared["file_pairs"][:TOP * 2]]
    return "\n".join(out)


def compare(old: dict, new: dict) -> str:
    out = []
    for name in sorted(set(old["tools"]) | set(new["tools"])):
        before = old["tools"].get(name, {}).get("summary", {})
        after = new["tools"].get(name, {}).get("summary", {})
        out.append(f"== {name}")
        width = max(map(len, list(before) + list(after)), default=0)
        for metric in list(dict.fromkeys(list(before) + list(after))):
            a, b = before.get(metric, "-"), after.get(metric, "-")
            mark = "" if a == b else "   *"
            out.append(f"  {metric.ljust(width)}  {a} -> {b}{mark}")
    pairs = sorted(set(old["duplication"]["tool_pairs"]) | set(new["duplication"]["tool_pairs"]))
    if pairs:
        out.append("== lines in two tools' own code")
        out += [f"  {p}  {old['duplication']['tool_pairs'].get(p, 0)} -> {new['duplication']['tool_pairs'].get(p, 0)}"
                for p in pairs]
    return "\n".join(out)


def run(packages: list[Path], commits: int) -> tuple[dict[str, dict], dict]:
    """Every number of each package, by the package's name, and the duplication between them."""
    results = {}
    for package in packages:
        result = measure(package.resolve(), commits)
        name = result["package"]
        while name in results:
            name += "'"
        results[name] = result
    shared = duplication(results)
    for result in results.values():
        result["summary"] = summary(result)
    return results, shared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("packages", nargs="*", type=Path, help="the tools' package folders")
    parser.add_argument("--json", type=Path, help="also write every number and site here")
    parser.add_argument("--commits", type=int, default=200, help="commits read for change coupling")
    parser.add_argument("--sites", action="store_true", help="list every encapsulation site")
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("OLD", "NEW"), help="two --json files")
    args = parser.parse_args(argv)
    if args.compare:
        old, new = (json.loads(p.read_text(encoding="utf-8")) for p in args.compare)
        print(compare(old, new))
        return 0
    if not args.packages:
        parser.error("name at least one package folder, or --compare two runs")
    results, shared = run(args.packages, args.commits)
    print("\n\n".join(report(r, args.sites) for r in results.values()))
    print()
    print(totals(results, shared))
    if args.json:
        args.json.write_text(json.dumps({"commits": args.commits, "tools": results, "duplication": shared},
                                        indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
