import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_script():
    spec = importlib.util.spec_from_file_location("share_page", ROOT / "scripts" / "share_page.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shared_files_go_to_another_package_with_a_manifest_of_their_digests(tmp_path: Path) -> None:
    """Every file of both shared folders, the page's and the server's, is copied byte for byte
    into the same place in the other package, each folder's manifest names each with its
    SHA-256, a file tex-mcp-web no longer shares leaves the copy, and a folder Python made
    there stays."""
    share_page = load_script()
    (tmp_path / "static" / "shared").mkdir(parents=True)
    (tmp_path / "static" / "shared" / "gone.js").write_text("", encoding="utf-8")
    (tmp_path / "shared" / "__pycache__").mkdir(parents=True)
    (tmp_path / "shared" / "gone.py").write_text("", encoding="utf-8")
    manifests = share_page.share_all(tmp_path)
    expected = {
        "static/shared": {"look.css", "look-early.js", "settings.js", "dom.js", "comment-api.js", "comment-search.js",
                          "comment-panel.js", "comment-panel.css", "comment-keys.js", "agent-bell.js",
                          "agent-bell.css", "help.js", "help.css", "tab-strip.js", "tab-strip.css", "markdown.js"},
        "shared": {"__init__.py", "tool.py", "shared_file.py", "filelock.py", "agents.py", "calls.py",
                   "plugin_monitor.py", "monitor.py", "waiter.py"},
    }
    for (folder, wanted), manifest in zip(expected.items(), manifests):
        source, copy = ROOT / "tex_mcp_web" / folder, tmp_path / folder
        names = sorted(path.name for path in source.iterdir() if path.is_file())
        assert wanted <= set(names)
        assert sorted(manifest["files"]) == names and manifest["source"].startswith("tex-mcp-web ")
        assert json.loads((copy / "MANIFEST.json").read_text(encoding="utf-8")) == manifest
        assert sorted(path.name for path in copy.iterdir() if path.is_file()) == sorted([*names, "MANIFEST.json"])
        for name, digest in manifest["files"].items():
            copied = (copy / name).read_bytes()
            assert copied == (source / name).read_bytes()
            assert hashlib.sha256(copied).hexdigest() == digest
    assert (tmp_path / "shared" / "__pycache__").is_dir()


def test_the_shared_server_modules_import_nothing_of_a_tool() -> None:
    """The server modules are the same bytes in every tool's package, so what sets a tool apart
    comes to them as a Tool or as arguments: they import the standard library, aiohttp, psutil
    and each other alone."""
    shared = ROOT / "tex_mcp_web" / "shared"
    modules = {path.stem for path in shared.glob("*.py")}
    for path in shared.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level:
                named = {node.module} if node.module else {alias.name for alias in node.names}
                assert node.level == 1 and named <= modules, f"shared/{path.name} imports {named} from outside shared"
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]
                for name in names:
                    top = name.split(".")[0]
                    assert top in sys.stdlib_module_names | {"aiohttp", "psutil"}, f"shared/{path.name} imports {name}"
