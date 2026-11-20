"""tex-mcp-web command-line interface.

Subcommands:
    serve      run the viewer server on its own (default if no subcommand)
    link       print the page's address, with its token when page_token is on
    init       scaffold .tex-mcp-web.yaml
    config     get/set values in .tex-mcp-web.yaml
    compile    one-shot compile, print structured errors
    goto       scroll a running viewer
    terms      build the term list and print the files to read before editing
    mcp        run the MCP server (stdio transport) and serve the viewer

Comment management lives in the browser (for humans) and the MCP tools
(for the agent).  The CLI deliberately does not expose comment commands
because nobody types ``tex-mcp-web comment add ...`` from a shell.
"""

from __future__ import annotations

import argparse
import os
import asyncio
import codecs
import json
import logging
import sys
import unicodedata
from pathlib import Path
from typing import TextIO

from .config import DEFAULT_PORT, create_config, find_config, load_config
from . import __version__


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


# Plain stand-ins for the punctuation the page's own texts use.
_STAND_INS = {"—": "-", "–": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "..."}


def _stand_in(error: UnicodeEncodeError) -> tuple[str, int]:
    """For characters an encoding cannot hold: a dash or quote as its plain form, a letter
    without its accent, and anything else as "?"."""
    def plain(character: str) -> str:
        return (_STAND_INS.get(character)
                or unicodedata.normalize("NFKD", character).encode("ascii", "ignore").decode("ascii") or "?")

    return "".join(map(plain, error.object[error.start:error.end])), error.end


codecs.register_error("tex_mcp_web.stand_in", _stand_in)


class _LegibleHandler(logging.StreamHandler):
    """Writes a log line's characters as they are where the stream's encoding holds them,
    and a plain stand-in for each it cannot hold. A console's legacy code page, Korean
    Windows' cp949 for one, holds the letters of a paper folder's name but no em dash,
    which the stream itself would write as an escape such as \\u2014."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        encoding = getattr(self.stream, "encoding", None) or "utf-8"
        return line.encode(encoding, "tex_mcp_web.stand_in").decode(encoding)


def _log_handler(stream: TextIO) -> logging.Handler:
    handler = _LegibleHandler(stream)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s — %(message)s", "%H:%M:%S"))
    return handler


def _configure_logging(verbose: bool) -> None:
    # A log that goes to a file or to the program that started the server is UTF-8 on every
    # system. Windows would write it in the system's own encoding, which whoever reads the
    # file does not expect. A terminal keeps its own.
    if not sys.stderr.isatty() and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=[_log_handler(sys.stderr)])


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def _port_taken(port: int) -> bool:
    """Whether something already listens on the page's port, asked before the server says
    that it serves there."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # As the server binds: a port just closed can be taken again at once. Windows would
        # let this option take a port that is in use, so it is not set there.
        if os.name == "posix":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run as run_server

    cfg = load_config(main_file=getattr(args, "main", None))
    port = args.port or cfg.port
    if _port_taken(port):
        print(f"Port {port} is already in use, by another tex-mcp-web server or by another program. "
              "Choose another with `tex-mcp-web config port <number>`, or with --port for this run.",
              file=sys.stderr)
        return 1
    print(
        f"tex-mcp-web v{__version__}  serving {cfg.main} at http://127.0.0.1:{port}"
        + ("  (open it from the link `tex-mcp-web link` prints)" if cfg.page_token else ""),
        file=sys.stderr,
    )
    run_server(cfg, port=port)
    return 0


# ---------------------------------------------------------------------------
# link
# ---------------------------------------------------------------------------


def cmd_link(args: argparse.Namespace) -> int:
    """The page's address, to open or bookmark: with page_token on, the link with the token,
    which a browser opens once and then keeps."""
    from .page_token import link

    cfg = load_config()
    print(link(args.port or cfg.port, cfg.page_token))
    return 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def _main_here() -> str:
    """The folder's main file: the one .tex file here that has \\documentclass, or paper.tex
    when there is none or more than one to choose from."""
    mains = [path.name for path in sorted(Path.cwd().glob("*.tex"))
             if "\\documentclass" in path.read_text(encoding="utf-8", errors="replace")]
    return mains[0] if len(mains) == 1 else "paper.tex"


def cmd_init(args: argparse.Namespace) -> int:
    existing = find_config()
    if existing and not args.force:
        # init writes here. A config found higher up is left alone by --force.
        if existing.parent.resolve() == Path.cwd().resolve():
            print(f"Config already exists here: {existing}", file=sys.stderr)
            print("--force overwrites it.", file=sys.stderr)
        else:
            print(f"Config found in a parent folder: {existing}", file=sys.stderr)
            print("--force writes a new config here, which takes precedence for this folder. "
                  "The parent is not changed.", file=sys.stderr)
        return 1
    path = create_config(main=args.main or _main_here(), port=args.port or DEFAULT_PORT)
    print(f"Wrote {path}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


_CONFIG_KEYS = ("main", "dir", "port", "compiler", "watch", "ignore", "references", "guideline", "page_token",
                "overleaf", "overleaf_sync")


def cmd_config(args: argparse.Namespace) -> int:
    import yaml

    from .config import DEFAULT_CONFIG_NAME

    path = find_config()
    if path is None:
        print(f"No {DEFAULT_CONFIG_NAME} found. `tex-mcp-web init` writes one here, "
              "and `tex-mcp-web config dir <folder>` then names the paper's folder anywhere.", file=sys.stderr)
        return 1
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if args.key is None:
        print(f"# {path}")
        print(yaml.dump(data, default_flow_style=False, sort_keys=False), end="")
        return 0

    if args.key not in _CONFIG_KEYS:
        print(f"unknown key {args.key!r}; one of: {', '.join(_CONFIG_KEYS)}", file=sys.stderr)
        return 1

    if args.value is None:
        if args.key not in data:
            print(f"{args.key} is not set in {path}", file=sys.stderr)
            return 1
        v = data[args.key]
        print(",".join(v) if isinstance(v, list) else v)
        return 0

    value: object = args.value
    if args.key == "port":
        value = int(args.value)
    elif args.key in ("page_token", "overleaf_sync"):
        if args.value not in ("true", "false"):
            print(f"{args.key} is true or false", file=sys.stderr)
            return 1
        value = args.value == "true"
    elif args.key == "overleaf":
        from .overleaf import config_link

        try:
            value = config_link(args.value)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
    elif args.key in ("watch", "ignore"):
        value = [p.strip() for p in args.value.split(",") if p.strip()]
    elif args.key == "guideline":
        from .config import Config, get_guideline_files

        try:
            get_guideline_files(Config.from_dict({"main": "main.tex", "guideline": args.value}))
        except (OSError, ValueError) as error:
            print(f"{error}. setup_info() lists the guidelines to choose from.", file=sys.stderr)
            return 1
    elif args.key == "compiler":
        from .compiler import ALLOWED_COMPILERS

        if args.value not in ALLOWED_COMPILERS:
            print(
                f"unknown compiler {args.value!r}; one of: {', '.join(sorted(ALLOWED_COMPILERS))}",
                file=sys.stderr,
            )
            return 1
    data[args.key] = value
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    print(f"{args.key} = {value}  ({path})", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# compile (one-shot)
# ---------------------------------------------------------------------------


def cmd_compile(args: argparse.Namespace) -> int:
    from .compiler import compile_tex
    from .config import get_main_file, get_watch_dir

    cfg = load_config(main_file=args.main)
    main = get_main_file(cfg)
    watch_dir = get_watch_dir(cfg)

    result = asyncio.run(compile_tex(main, compiler=cfg.compiler, work_dir=watch_dir))

    if args.json:
        print(json.dumps(_compile_result_dict(result), indent=2))
    else:
        if result.success:
            print(f"compile succeeded in {result.duration_seconds:.2f}s")
        else:
            print(f"compile FAILED ({len(result.errors)} errors)", file=sys.stderr)
        for err in result.errors:
            loc = f"{err.file}:{err.line}" if err.line else err.file
            print(f"  ERROR  {loc}  {err.message}", file=sys.stderr)
            if err.context:
                for line in err.context:
                    print(f"           {line}", file=sys.stderr)
        for w in result.warnings:
            loc = f"{w.file}:{w.line}" if w.line else w.file
            print(f"  warn   {loc}  {w.message}", file=sys.stderr)
    return 0 if result.success else 1


def _compile_result_dict(result) -> dict:
    import dataclasses

    return {
        "success": result.success,
        "errors": [dataclasses.asdict(e) for e in result.errors],
        "warnings": [dataclasses.asdict(w) for w in result.warnings],
        "output_file": str(result.output_file) if result.output_file else None,
        "duration_seconds": result.duration_seconds,
    }


# ---------------------------------------------------------------------------
# terms
# ---------------------------------------------------------------------------


def cmd_terms(args: argparse.Namespace) -> int:
    """What guide() answers, for an agent that edits the paper without tex-mcp's tools."""
    from .config import get_guideline_files
    from .terms import refresh

    cfg = load_config()
    try:
        guides = get_guideline_files(cfg)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    terms = refresh(cfg)
    # The same list, in the same order, as guide() answers.
    for path in guides + ([] if terms is None else [terms]):
        print(path)
    return 0


# ---------------------------------------------------------------------------
# goto (requires running daemon)
# ---------------------------------------------------------------------------


def cmd_goto(args: argparse.Namespace) -> int:
    import urllib.request

    from .mcp_server import parse_goto_target

    cfg = load_config()
    port = args.port or cfg.port
    body = parse_goto_target(args.target, default_file=cfg.main)

    from .page_token import headers

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/goto",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers()},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(resp.read().decode())
        return 0
    except Exception as exc:
        print(f"could not reach daemon at {port}: {exc}", file=sys.stderr)
        return 2


# ---------------------------------------------------------------------------
# mcp
# ---------------------------------------------------------------------------


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.check:
        from .mcp_server import check

        tools = check(Path.cwd())
        print(f"tex-mcp: {len(tools)} tools ({', '.join(tools)})")
        return 0
    from .mcp_server import main as mcp_main

    mcp_main()
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tex-mcp-web",
        description=(
            "Live PDF preview + review-style commenting for LaTeX papers, "
            "designed for a coding agent as the author. Run with no arguments to "
            "start the server."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    parser.add_argument("--version", action="version", version=f"tex-mcp-web {__version__}")
    parser.add_argument("--port", type=int, help="HTTP port (default: from config)")

    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="run the viewer server on its own (default)")
    p.add_argument("--main", help="main .tex file (overrides config)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("link", help="print the page's address, with its token when page_token is on")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser("init", help="scaffold .tex-mcp-web.yaml")
    p.add_argument("--main", help="main .tex file (default: the one here with \\documentclass, else paper.tex)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("config", help="get/set values in .tex-mcp-web.yaml")
    p.add_argument("key", nargs="?", help=f"one of: {', '.join(_CONFIG_KEYS)}")
    p.add_argument("value", nargs="?", help="new value (lists comma-separated); omit to print")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("compile", help="one-shot compile")
    p.add_argument("--main", help="main .tex file (overrides config)")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("goto", help="scroll a running viewer")
    p.add_argument("target", help="section title, line number, file:line, or pN")
    p.set_defaults(func=cmd_goto)

    p = sub.add_parser("terms", help="build the term list and print the files to read before editing")
    p.set_defaults(func=cmd_terms)

    p = sub.add_parser("mcp", help="run the MCP server (stdio); serves the viewer too")
    p.add_argument("--check", action="store_true",
                   help="build the server and list its tools instead of serving")
    p.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "verbose", False))

    if not getattr(args, "func", None):
        args.main = None
        return cmd_serve(args)
    return args.func(args)


def _split_root_args(argv: list[str]) -> tuple[list[str], list[str]]:
    # Root-parser flags must precede the subcommand in argparse.
    root: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-v", "--verbose", "--version") or a.startswith("--port="):
            root.append(a)
        elif a == "--port":
            root.extend(argv[i : i + 2])
            i += 1
        else:
            rest.append(a)
        i += 1
    return root, rest


def main_mcp(argv: list[str] | None = None) -> int:
    """`tex-mcp` entry point: MCP stdio server."""
    root, rest = _split_root_args(sys.argv[1:] if argv is None else argv)
    return main([*root, "mcp", *rest])


def main_serve(argv: list[str] | None = None) -> int:
    """`tex-web` entry point: web server."""
    root, rest = _split_root_args(sys.argv[1:] if argv is None else argv)
    return main([*root, "serve", *rest])


if __name__ == "__main__":
    sys.exit(main())
