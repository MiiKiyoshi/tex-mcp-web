"""Copy the files the review pages share into another review page, with a manifest.

The shared files are written in tex_mcp_web/static/shared/ alone. This script copies them into
another page's static/shared/ folder and writes MANIFEST.json there, with each file's SHA-256
and the tex-mcp-web commit they came from. That page's tests check its copies against the
manifest, so a copy edited by hand fails there. Files the folder holds that tex-mcp-web no
longer shares are removed.

    python scripts/share_page.py <the other page's static folder>
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "tex_mcp_web" / "static" / "shared"
MANIFEST = "MANIFEST.json"


def share(static: Path) -> dict:
    """Copy the shared files into static/shared and return the manifest written beside them."""
    target = static / "shared"
    target.mkdir(exist_ok=True)
    names = sorted(path.name for path in SOURCE.iterdir() if path.is_file())
    for path in target.iterdir():
        if path.name not in names and path.name != MANIFEST:
            path.unlink()
    for name in names:
        shutil.copyfile(SOURCE / name, target / name)
    commit = subprocess.run(["git", "describe", "--always", "--dirty"], cwd=SOURCE, check=True,
                            capture_output=True, text=True).stdout.strip()
    manifest = {"source": f"tex-mcp-web {commit}",
                "files": {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() for name in names}}
    (target / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        raise SystemExit("usage: python scripts/share_page.py <the other page's static folder>")
    print(share(Path(sys.argv[1]))["source"])
