"""The page token, for a machine other people log in to: the page answers only a browser that
opened its link, and the tools on this machine that send the token."""

import asyncio
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tex_mcp_web import page_token
from tex_mcp_web.config import Config, load_config
from tex_mcp_web.server import TexMcpWebServer

PAPER = "\\documentclass{article}\n\\begin{document}\nHello.\n\\end{document}\n"


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    return home


@pytest.fixture
async def locked(tmp_path: Path, config_home: Path):
    (tmp_path / "paper.tex").write_text(PAPER)
    server = TexMcpWebServer(Config(main="paper.tex", page_token=True, config_path=tmp_path / ".tex-mcp-web.yaml"))
    client = TestClient(TestServer(server.app))
    await client.start_server()
    try:
        yield client, server
    finally:
        await client.close()


def test_the_token_is_the_reviewers_own_and_readable_by_them_alone(config_home: Path) -> None:
    assert page_token.read_token() is None and page_token.headers() == {}
    old = os.umask(0)
    try:
        token = page_token.ensure_token()
    finally:
        os.umask(old)
    path = config_home / "tex-mcp-web" / "page-token"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert page_token.ensure_token() == token and len(token) >= 40
    assert page_token.headers() == {page_token.HEADER: token}
    assert page_token.link(8765, True) == f"http://127.0.0.1:8765/?token={token}"
    assert page_token.link(8765, False) == "http://127.0.0.1:8765/"


async def test_the_page_answers_only_with_its_token(locked) -> None:
    client, server = locked
    assert (await client.get("/paper")).status == 403
    assert (await client.get("/?token=wrong")).status == 403
    token = page_token.read_token()
    # Its link sets the cookie and goes on to the same page, the token left out of the address.
    opened = await client.get(f"/paper?token={token}&x=1", allow_redirects=False)
    assert opened.status == 303 and opened.headers["Location"] == "/paper?x=1"
    cookie = opened.headers["Set-Cookie"]
    assert f"{page_token.COOKIE}={token}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert (await client.get("/paper")).status == 200
    client.session.cookie_jar.clear()
    assert (await client.get("/paper", headers={page_token.HEADER: token})).status == 200
    # Only a page opened sets the cookie, not a change sent with the token in its address.
    assert (await client.post(f"/review-request?token={token}")).status == 403
    assert (await client.post("/review-request", headers={page_token.HEADER: token})).status == 200
    # Turned off, the page answers as before.
    server.config.page_token = False
    assert (await client.get("/paper")).status == 200


async def test_the_agents_tools_and_the_waiter_send_the_token(locked, tmp_path: Path) -> None:
    """The waiter is a shell script with curl, which gets the token on its input: a command
    line on this machine is anyone's to read."""
    from tex_mcp_web.mcp_server import _write_waiter

    client, server = locked
    token = page_token.ensure_token()
    port = client.server.port
    listen = await client.post("/wait-review/listen?listener=one", headers=page_token.headers())
    assert listen.status == 200
    waiter = _write_waiter(tmp_path, port)
    assert token not in waiter.read_text()
    run = await asyncio.create_subprocess_exec("sh", str(waiter), "one", stdout=asyncio.subprocess.PIPE,
                                               env={**os.environ, "XDG_CONFIG_HOME": os.environ["XDG_CONFIG_HOME"]})
    try:
        await client.post("/review-request", headers=page_token.headers())
        line = await asyncio.wait_for(run.stdout.readline(), 20)
        assert line.decode().startswith("[review] reviewer called (press #1)")
    finally:
        run.kill()
        await run.wait()


def test_the_cli_sets_the_switch_and_prints_the_link(tmp_path: Path, config_home: Path) -> None:
    (tmp_path / "paper.tex").write_text(PAPER)
    command = [sys.executable, "-m", "tex_mcp_web.cli"]
    env = {**os.environ, "XDG_CONFIG_HOME": str(config_home)}
    run = lambda *args: subprocess.run([*command, *args], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert run("--port", "8790", "init", "--main", "paper.tex").returncode == 0
    assert run("link").stdout.strip() == "http://127.0.0.1:8790/"
    assert run("config", "page_token", "yes").returncode == 1
    assert run("config", "page_token", "true").returncode == 0
    assert load_config(tmp_path / ".tex-mcp-web.yaml").page_token is True
    assert run("link").stdout.strip() == f"http://127.0.0.1:8790/?token={page_token.read_token()}"


async def test_the_agent_finds_its_page_running_when_the_page_asks_for_the_token(locked) -> None:
    """The agent's tools look for the page already serving the paper by asking it which config
    it serves. Turned away, they would take the port for free and fail to serve on it."""
    from tex_mcp_web.mcp_client import SharedProjectServer

    client, server = locked
    config = server.config
    config.port = client.server.port
    shared = SharedProjectServer(config)
    assert await asyncio.to_thread(shared._remote_identity) == str(config.config_path)
