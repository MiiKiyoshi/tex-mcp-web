"""Copy what the review tools share into another tool's package, with a manifest per folder.

Two folders are shared, each written in tex-mcp-web alone: tex_mcp_web/static/shared, the files
the review pages load, and tex_mcp_web/shared, the server modules. This script copies each into
the same place in another tool's package and writes MANIFEST.json there, with each file's
SHA-256 and the tex-mcp-web commit they came from. That tool's tests check its copies against
the manifests, so a copy edited by hand fails there. Files a folder holds that tex-mcp-web no
longer shares are removed; folders in it, as Python's __pycache__, are left.

    python scripts/share_page.py <the other tool's package folder>
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "tex_mcp_web"
FOLDERS = (Path("static") / "shared", Path("shared"))
MANIFEST = "MANIFEST.json"


def share(source: Path, target: Path) -> dict:
    """Copy the files of *source* into *target* and return the manifest written beside them."""
    target.mkdir(exist_ok=True)
    names = sorted(path.name for path in source.iterdir() if path.is_file())
    for path in target.iterdir():
        if path.is_file() and path.name not in names and path.name != MANIFEST:
            path.unlink()
    for name in names:
        shutil.copyfile(source / name, target / name)
    commit = subprocess.run(["git", "describe", "--always", "--dirty"], cwd=source, check=True,
                            capture_output=True, text=True).stdout.strip()
    manifest = {"source": f"tex-mcp-web {commit}",
                "files": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names}}
    (target / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def share_all(package: Path) -> list[dict]:
    """Copy both shared folders into *package*, another tool's package folder."""
    return [share(PACKAGE / folder, package / folder) for folder in FOLDERS]


if __name__ == "__main__":
    if len(sys.argv) != 2 or not (Path(sys.argv[1]) / "static").is_dir():
        raise SystemExit("usage: python scripts/share_page.py <the other tool's package folder>")
    for manifest in share_all(Path(sys.argv[1])):
        print(manifest["source"])
