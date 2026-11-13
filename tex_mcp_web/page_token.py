"""The token that opens the review page on a machine other people log in to.

With ``page_token: true`` the page answers only a browser that opened its link, the page's
address with ``?token=`` and the token, and the tools on this machine that send the token.
The token is the reviewer's own, one for every paper, kept like the Overleaf cookie: in their
config folder, readable by them alone, so another account on the machine can reach the
page's port and still not open it. The browser keeps it as a cookie after the first visit,
and a bookmark of the page's plain address goes on working there.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

HEADER = "X-Tex-Mcp-Web-Token"
COOKIE = "tex-mcp-web-token"


def token_path() -> Path:
    from .config import user_config_dir

    return user_config_dir() / "page-token"


def read_token() -> str | None:
    """The reviewer's page token, or None when none was made."""
    try:
        return token_path().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def ensure_token() -> str:
    """The reviewer's page token, made once, readable by them alone."""
    token = read_token()
    if token is not None:
        return token
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    token = secrets.token_urlsafe(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, f"{token}\n".encode())
    finally:
        os.close(descriptor)
    return token


def headers() -> dict[str, str]:
    """What a tool on this machine sends the page: the token, when one was made."""
    token = read_token()
    return {HEADER: token} if token else {}


def link(port: int, on: bool) -> str:
    """The page's address, with the token when the page asks for it."""
    base = f"http://127.0.0.1:{port}/"
    return f"{base}?token={ensure_token()}" if on else base
