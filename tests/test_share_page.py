import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_script():
    spec = importlib.util.spec_from_file_location("share_page", ROOT / "scripts" / "share_page.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shared_files_go_to_another_page_with_a_manifest_of_their_digests(tmp_path: Path) -> None:
    """Every shared file is copied byte for byte, the manifest names each with its SHA-256, and
    a file tex-mcp-web no longer shares leaves the copy."""
    share_page = load_script()
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "gone.js").write_text("", encoding="utf-8")
    manifest = share_page.share(tmp_path)
    names = sorted(path.name for path in (ROOT / "tex_mcp_web" / "static" / "shared").iterdir())
    assert {"look.css", "look-early.js", "settings.js", "dom.js", "comment-search.js", "comment-panel.js",
            "comment-panel.css", "comment-keys.js", "help.js", "help.css"} <= set(names)
    assert sorted(manifest["files"]) == names and manifest["source"].startswith("tex-mcp-web ")
    assert json.loads((tmp_path / "shared" / "MANIFEST.json").read_text(encoding="utf-8")) == manifest
    assert sorted(path.name for path in (tmp_path / "shared").iterdir()) == sorted([*names, "MANIFEST.json"])
    for name, digest in manifest["files"].items():
        copied = (tmp_path / "shared" / name).read_bytes()
        assert copied == (ROOT / "tex_mcp_web" / "static" / "shared" / name).read_bytes()
        assert hashlib.sha256(copied).hexdigest() == digest
