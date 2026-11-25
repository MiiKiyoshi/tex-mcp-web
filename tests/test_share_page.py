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
    package = tmp_path / "pkg"
    (package / "static" / "shared").mkdir(parents=True)
    (package / "static" / "shared" / "gone.js").write_text("", encoding="utf-8")
    (package / "shared" / "__pycache__").mkdir(parents=True)
    (package / "shared" / "gone.py").write_text("", encoding="utf-8")
    manifests = share_page.share_all(package)
    expected = {
        "static/shared": {"look.css", "look-early.js", "settings.js", "dom.js", "comment-api.js", "comment-search.js",
                          "comment-panel.js", "comment-state.js", "comment-markup.js", "comment-list.js",
                          "comment-views.js", "comment-row.js", "comment-status.js", "comment-card.js",
                          "comment-compose.js", "comment-panel.css", "comment-keys.js", "agent-bell.js",
                          "agent-bell.css", "help.js", "help.css", "tab-strip.js", "tab-strip.css", "markdown.js",
                          "update.js", "update.css", "page-zoom.js", "panel-grips.js", "panel-grips.css",
                          "socket.js", "sidebar.js", "page-keys.js", "pdf-touch.js", "pdf-theme.js",
                          "pdf-view.js", "pdf-marks.js", "listeners.js"},
        "shared": {"__init__.py", "tool.py", "shared_file.py", "filelock.py", "agents.py", "calls.py",
                   "plugin_monitor.py", "monitor.py", "waiter.py", "update.py"},
    }
    # A tool whose plugin has no hooks, and that has no tests beside it, gets the two folders alone.
    assert len(manifests) == len(expected)
    for (folder, wanted), manifest in zip(expected.items(), manifests):
        source, copy = ROOT / "tex_mcp_web" / folder, package / folder
        names = sorted(path.name for path in source.iterdir() if path.is_file())
        assert wanted <= set(names)
        assert sorted(manifest["files"]) == names and manifest["source"].startswith("tex-mcp-web ")
        assert json.loads((copy / "MANIFEST.json").read_text(encoding="utf-8")) == manifest
        assert sorted(path.name for path in copy.iterdir() if path.is_file()) == sorted([*names, "MANIFEST.json"])
        for name, digest in manifest["files"].items():
            copied = (copy / name).read_bytes()
            assert copied == (source / name).read_bytes()
            assert hashlib.sha256(copied).hexdigest() == digest
    assert (package / "shared" / "__pycache__").is_dir()


def test_the_shared_hook_file_goes_beside_the_tools_own_hook(tmp_path: Path) -> None:
    """A tool whose plugin has hooks gets the file the guide gates share in its hooks folder,
    with a manifest of that file alone. The tool's own hook and its wiring stay as they are,
    and a file the last manifest named that tex-mcp-web no longer shares leaves."""
    share_page = load_script()
    package = tmp_path / "pkg"
    (package / "static").mkdir(parents=True)
    hooks = tmp_path / "plugin" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "guide_gate.py").write_text("# the tool's own hook\n", encoding="utf-8")
    (hooks / "hooks.json").write_text("{}\n", encoding="utf-8")
    (hooks / "old_record.py").write_text("", encoding="utf-8")
    (hooks / "MANIFEST.json").write_text(json.dumps({"source": "tex-mcp-web 0", "files": {"old_record.py": ""}}),
                                         encoding="utf-8")
    manifest = share_page.share_all(package)[-1]
    shared = ("guide_record.py", "python.sh")
    sources = {name: ROOT / "plugin" / "hooks" / name for name in shared}
    assert manifest["files"] == {name: hashlib.sha256(source.read_bytes()).hexdigest() for name, source in sources.items()}
    assert all((hooks / name).read_bytes() == source.read_bytes() for name, source in sources.items())
    assert json.loads((hooks / "MANIFEST.json").read_text(encoding="utf-8")) == manifest
    assert sorted(path.name for path in hooks.iterdir()) == ["MANIFEST.json", "guide_gate.py", "guide_record.py", "hooks.json",
                                                             "python.sh"]
    assert (hooks / "guide_gate.py").read_text(encoding="utf-8") == "# the tool's own hook\n"


def test_the_browser_the_tests_drive_goes_beside_the_tools_own_tests(tmp_path: Path) -> None:
    """A tool with tests beside its package gets the browser the tests drive, tests/chrome.py,
    with a manifest of that file alone. The tool's own tests stay as they are, and a file the
    last manifest named that tex-mcp-web no longer shares leaves."""
    share_page = load_script()
    package = tmp_path / "pkg"
    (package / "static").mkdir(parents=True)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_page.py").write_text("# the tool's own test\n", encoding="utf-8")
    (tests / "old_driver.py").write_text("", encoding="utf-8")
    (tests / "MANIFEST.json").write_text(json.dumps({"source": "tex-mcp-web 0", "files": {"old_driver.py": ""}}),
                                         encoding="utf-8")
    manifest = share_page.share_all(package)[-1]
    source = ROOT / "tests" / "chrome.py"
    assert manifest["files"] == {"chrome.py": hashlib.sha256(source.read_bytes()).hexdigest()}
    assert (tests / "chrome.py").read_bytes() == source.read_bytes()
    assert json.loads((tests / "MANIFEST.json").read_text(encoding="utf-8")) == manifest
    assert sorted(path.name for path in tests.iterdir()) == ["MANIFEST.json", "chrome.py", "test_page.py"]
    assert (tests / "test_page.py").read_text(encoding="utf-8") == "# the tool's own test\n"


def test_the_browser_the_tests_drive_imports_nothing_of_a_tool() -> None:
    """tests/chrome.py is the same bytes in every tool's tests, so what one tool's tests need
    more of they add themselves: it imports the standard library, pytest, Selenium and psutil
    alone, and nothing beside it."""
    for node in ast.walk(ast.parse((ROOT / "tests" / "chrome.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            assert not node.level, f"tests/chrome.py imports {node.module} from beside it"
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in [node.module] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names]:
                top = name.split(".")[0]
                assert top in sys.stdlib_module_names | {"pytest", "selenium", "psutil"}, f"tests/chrome.py imports {name}"


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
