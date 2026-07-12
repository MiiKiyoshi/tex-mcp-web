"""tex-mcp-web command-line interface (v0.5.0).

Subcommands:
    serve      run the daemon (default if no subcommand)
    init       scaffold .tex-mcp-web.yaml
    config     get/set values in .tex-mcp-web.yaml
    compile    one-shot compile, print structured errors
    goto       tell a running daemon to scroll the viewer
    mcp        run the MCP server (stdio transport)

Comment management lives in the browser (for humans) and the MCP tools
(for the agent).  The CLI deliberately does not expose comment commands
because nobody types ``tex-mcp-web comment add ...`` from a shell.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .config import DEFAULT_PORT, create_config, find_config, load_config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import run as run_server

    cfg = load_config(main_file=getattr(args, "main", None))
    port = args.port or cfg.port
    print(f"tex-mcp-web v0.5.0  serving {cfg.main} at http://127.0.0.1:{port}", file=sys.stderr)
    run_server(cfg, port=port)
    return 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    existing = find_config()
    if existing and not args.force:
        print(f"Config already exists: {existing}", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        return 1
    path = create_config(main=args.main, port=args.port or DEFAULT_PORT)
    print(f"Wrote {path}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------


_CONFIG_KEYS = ("main", "port", "compiler", "watch", "ignore", "page_limit")


def cmd_config(args: argparse.Namespace) -> int:
    import yaml

    from .config import DEFAULT_CONFIG_NAME

    path = find_config()
    if path is None:
        print(f"No {DEFAULT_CONFIG_NAME} found; run `tex-mcp-web init` first.", file=sys.stderr)
        return 1
    with open(path) as f:
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
    if args.key in ("port", "page_limit"):
        value = int(args.value)
    elif args.key in ("watch", "ignore"):
        value = [p.strip() for p in args.value.split(",") if p.strip()]
    elif args.key == "compiler":
        from .compiler import ALLOWED_COMPILERS

        if args.value not in ALLOWED_COMPILERS:
            print(
                f"unknown compiler {args.value!r}; one of: {', '.join(sorted(ALLOWED_COMPILERS))}",
                file=sys.stderr,
            )
            return 1
    data[args.key] = value
    with open(path, "w") as f:
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
# goto (requires running daemon)
# ---------------------------------------------------------------------------


def cmd_goto(args: argparse.Namespace) -> int:
    import urllib.request

    from .mcp_server import parse_goto_target

    cfg = load_config()
    port = args.port or cfg.port
    body = parse_goto_target(args.target, default_file=cfg.main)

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/goto",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
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
    from .mcp_server import main as mcp_main

    cfg = load_config()
    port = args.port or cfg.port
    mcp_main(port=port)
    return 0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tex-mcp-web",
        description=(
            "Live PDF preview + review-style commenting for LaTeX papers, "
            "designed for Claude Code as the author. Run with no arguments to "
            "start the server."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    parser.add_argument("--port", type=int, help="HTTP port (default: from config)")

    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="run the daemon (default)")
    p.add_argument("--main", help="main .tex file (overrides config)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("init", help="scaffold .tex-mcp-web.yaml")
    p.add_argument("--main", default="paper.tex")
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

    p = sub.add_parser("goto", help="tell the daemon to scroll the viewer")
    p.add_argument("target", help="section title, line number, file:line, or pN")
    p.set_defaults(func=cmd_goto)

    p = sub.add_parser("mcp", help="run the MCP server (stdio)")
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
        if a in ("-v", "--verbose") or a.startswith("--port="):
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
