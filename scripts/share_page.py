"""Copy what the review tools share into another tool, with a manifest per folder.

Three folders are shared, each written in tex-mcp-web alone. Two are shared whole:
tex_mcp_web/static/shared, the files the review pages load, and tex_mcp_web/shared, the server
modules. The third, plugin/hooks, holds each tool's own hook beside the files they share,
guide_record.py and python.sh, which runs a hook with a Python that runs, and only those files
are shared, to a tool whose plugin has hooks. This script
copies each into the same place in another tool and writes MANIFEST.json there, with each
shared file's SHA-256 and the tex-mcp-web commit they came from. That tool's tests check its
copies against the manifests, so a copy edited by hand fails there. A file tex-mcp-web no longer
shares is removed: in a folder shared whole, any file but the manifest, and in plugin/hooks, a
file the last manifest named. Folders in them, as Python's __pycache__, are left. Each file is
put in place whole, since a running Claude Code may run a hook from the folder at any moment.

    python scripts/share_page.py <the other tool's package folder>
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "tex_mcp_web"
FOLDERS = (Path("static") / "shared", Path("shared"))
HOOKS = Path("plugin") / "hooks"
HOOK_FILES = ("guide_record.py", "python.sh")
MANIFEST = "MANIFEST.json"


def put(content: bytes, path: Path) -> None:
    """Write *path* whole: beside it first, then in its place."""
    staging = path.with_name(f".{path.name}.new")
    staging.write_bytes(content)
    os.replace(staging, path)


def share(source: Path, target: Path, names: tuple[str, ...] | None = None) -> dict:
    """Copy the files of *source* into *target* and return the manifest written beside them:
    every file, or only *names* in a folder that holds the tool's own files beside them."""
    target.mkdir(exist_ok=True)
    shared = sorted(path.name for path in source.iterdir() if path.is_file()) if names is None else sorted(names)
    last = target / MANIFEST
    named = set(json.loads(last.read_text(encoding="utf-8"))["files"]) if last.is_file() else set()
    for path in target.iterdir():
        if (path.is_file() and path.name not in shared and path.name != MANIFEST
                and (names is None or path.name in named)):
            path.unlink()
    for name in shared:
        put((source / name).read_bytes(), target / name)
    commit = subprocess.run(["git", "describe", "--always", "--dirty"], cwd=source, check=True,
                            capture_output=True, text=True).stdout.strip()
    manifest = {"source": f"tex-mcp-web {commit}",
                "files": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in shared}}
    put((json.dumps(manifest, indent=2) + "\n").encode("utf-8"), last)
    return manifest


def share_all(package: Path) -> list[dict]:
    """Copy the shared folders into *package*, another tool's package folder, and the shared
    hook file into the plugin beside it, when that plugin has hooks."""
    manifests = [share(PACKAGE / folder, package / folder) for folder in FOLDERS]
    if (package.parent / HOOKS).is_dir():
        manifests.append(share(PACKAGE.parent / HOOKS, package.parent / HOOKS, HOOK_FILES))
    return manifests


if __name__ == "__main__":
    if len(sys.argv) != 2 or not (Path(sys.argv[1]) / "static").is_dir():
        raise SystemExit("usage: python scripts/share_page.py <the other tool's package folder>")
    for manifest in share_all(Path(sys.argv[1])):
        print(manifest["source"])
