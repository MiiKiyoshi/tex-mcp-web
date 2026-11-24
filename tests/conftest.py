import pytest

from tex_mcp_web import config, update
from tex_mcp_web.tool import TOOL


def pytest_addoption(parser):
    parser.addoption("--part", default="", help="run one part of the tests, as 2/3 for the second of three")


def pytest_collection_modifyitems(config, items):
    """With --part i/n, keep every nth test from the ith on. The CI jobs of a slow system each
    run one part, side by side."""
    if not config.getoption("--part"):
        return
    part, parts = (int(number) for number in config.getoption("--part").split("/"))
    kept = items[part - 1::parts]
    config.hook.pytest_deselected(items=[item for item in items if item not in kept])
    items[:] = kept


@pytest.fixture(autouse=True)
def no_user_translation(tmp_path, monkeypatch):
    """No test runs the translation command in the user's own config file, whatever that
    file holds on the machine running the tests. A test of that file writes it here."""
    monkeypatch.setattr(config, "user_config_file", lambda: tmp_path / "user-config" / "config.yaml")


@pytest.fixture(autouse=True)
def no_plugin_monitor(tmp_path, monkeypatch):
    """No plugin monitor runs for a test's session, whatever runs for the one running the
    tests. A server a test starts as its own process reads XDG_CACHE_HOME instead."""
    monkeypatch.setattr(TOOL, "monitors", tmp_path / "no-monitor")


@pytest.fixture(autouse=True)
def short_server_waits(monkeypatch):
    """The server waits a few seconds after news from Overleaf for its writes to land and
    before it connects again, and looks for changes on disk every second. A test's fake
    Overleaf needs no such waits, and a test sets its own where it tests one."""
    from tex_mcp_web import overleaf_sync
    from tex_mcp_web.overleaf_live import LivePresence
    from tex_mcp_web.server import TexMcpWebServer

    monkeypatch.setattr(TexMcpWebServer, "OVERLEAF_COMMENT_SETTLE", 0.01)
    monkeypatch.setattr(TexMcpWebServer, "OVERLEAF_TRACKED_SETTLE", 0.01)
    # A connection the fake drops is made again at once.
    monkeypatch.setattr(LivePresence, "RETRY_DELAYS", (0.05,))
    # What a test writes to the store or the folder itself is seen at once.
    monkeypatch.setattr(TexMcpWebServer, "COMMENT_STORE_POLL", 0.05)
    monkeypatch.setattr(overleaf_sync, "DISK_POLL", 0.05)
    # The page's waits too, in every browser.
    monkeypatch.setattr(TexMcpWebServer, "PAGE_WAITS", {
        "reconnect": 100, "overleafGit": 200, "gitAfterEdit": 50, "calledWord": 500, "translation": 200})


@pytest.fixture(autouse=True)
def no_update(monkeypatch):
    """No test asks the package index for the newest release, and none updates the install
    it runs from. A test of the page's Update puts its own answers in their place."""
    async def unknown():
        return None

    async def refused(*command):
        raise AssertionError("a test ran a real update")

    monkeypatch.setattr(update, "latest", unknown)
    monkeypatch.setattr(update, "_run", refused)
