"""Shared ownership of the review server, bound to the MCP process."""

import asyncio
import fcntl
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from .config import DEFAULT_CONFIG_NAME, Config, find_config, get_watch_dir, load_config

# How a folder is set up for review, read only when it is: an agent follows it with its shell.
INIT_GUIDE = Path(__file__).resolve().parent / "agent" / "init.md"
CLI = Path(sys.executable).parent / "tex-mcp-web"


class ProjectSetupError(RuntimeError):
    def __init__(self, config_path: Path, error: Exception):
        super().__init__(f"{config_path}: {error}")
        self.config_path = config_path


class SharedProjectServer:
    """One MCP process serves; peers using the same project share its listener."""

    # The start reads the paper's sources; against a cold filesystem that is seconds,
    # and nothing waits on it any more except the tool call that asked for it.
    START_TIMEOUT = 30

    def __init__(self, config: Config):
        if config.config_path is None:
            raise ValueError("configuration has no file path")
        self.config_path = config.config_path.resolve()
        self.watch_dir = get_watch_dir(config).resolve()
        self.port = config.port
        self.lock_handle = None
        self.lock_guard = threading.Lock()
        self.thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.start_error: BaseException | None = None
        self.start_task: asyncio.Task | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.server: Any = None

    def _remote_identity(self) -> str | None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/paper", timeout=0.5) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None
        return str(data["watch_dir"])

    def _serve(self) -> None:
        from .server import TexMcpWebServer

        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)

        async def start() -> None:
            try:
                self.server = TexMcpWebServer(load_config(self.config_path))
                await self.server.setup(self.port)
            except BaseException as error:
                # A cancelled start lands here too, once the synchronous setup returns.
                self.start_error = error
                if self.server is not None:
                    # Whatever setup started (the watcher, the listener) is released
                    # here, or a failed start leaks its watches and its port.
                    await self.server.cleanup()
                    self.server = None
            finally:
                self.ready.set()

        try:
            self.start_task = loop.create_task(start())
            loop.run_until_complete(self.start_task)
            if self.start_error is None:
                loop.run_forever()
                loop.run_until_complete(self.server.cleanup())
        finally:
            loop.close()
            # The lock is tied to this thread's life: a start that was given up on still
            # frees it when its cleanup is done.
            self._release_lock()

    def ensure(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            if self.ready.is_set() and self.start_error is None:
                return
            # A start given up on earlier is still winding down; a second thread on top
            # of it would take another set of watches.
            if not self.ready.wait(timeout=self.START_TIMEOUT):
                raise RuntimeError(
                    "review server is still starting; its earlier start was cancelled and "
                    "is cleaning up, try again")
            self.thread.join(timeout=self.START_TIMEOUT)
            if self.thread.is_alive():
                raise RuntimeError("review server is still cleaning up a cancelled start; try again")
            self.thread = None
        identity = self._remote_identity()
        if identity is not None:
            if Path(identity).resolve() != self.watch_dir:
                raise RuntimeError(
                    f"port {self.port} serves {identity}, not {self.watch_dir}; change one project's port")
            return
        lock_path = self.watch_dir / ".tex-mcp-web" / "server.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            for _ in range(50):
                time.sleep(0.1)
                identity = self._remote_identity()
                if identity is not None:
                    if Path(identity).resolve() != self.watch_dir:
                        raise RuntimeError(f"port {self.port} is used by another project: {identity}")
                    return
            raise RuntimeError(f"review server lock is held but port {self.port} is not reachable")
        self.lock_handle = handle
        self.ready.clear()
        self.start_error = None
        self.start_task = None
        self.thread = threading.Thread(target=self._serve, name="tex-mcp-web", daemon=True)
        self.thread.start()
        if not self.ready.wait(timeout=self.START_TIMEOUT):
            # Setup runs synchronously on the loop, so the cancel takes effect only once
            # it returns; the thread then cleans up what it started and frees the lock.
            if self.loop is not None and self.start_task is not None:
                self.loop.call_soon_threadsafe(self.start_task.cancel)
            raise RuntimeError(
                f"review server did not start within {self.START_TIMEOUT} seconds; "
                "the start is cancelled and cleans up in the background, try again")
        if self.start_error is not None:
            error = self.start_error
            self.stop()
            raise RuntimeError(f"review server failed to start: {error}")

    def stop(self) -> None:
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=10)
        self.thread = None
        self.loop = None
        self.server = None
        self._release_lock()

    def _release_lock(self) -> None:
        with self.lock_guard:
            if self.lock_handle is not None:
                fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_UN)
                self.lock_handle.close()
                self.lock_handle = None


class ProjectBinding:
    """Bind one MCP process to the project discovered from its startup directory."""

    def __init__(self, start_dir: Path):
        self.start_dir = start_dir.resolve()
        self._lock = threading.Lock()
        self._shared: SharedProjectServer | None = None

    def describe(self) -> Path | None:
        """Read the project's configuration without starting anything.

        Enough to say at startup that a paper is misconfigured, and no more: the server
        it would serve is started by the first tool call that needs one.
        """
        config_path = find_config(self.start_dir)
        if config_path is None:
            return None
        try:
            load_config(config_path)
        except (OSError, TypeError, ValueError, yaml.YAMLError) as error:
            raise ProjectSetupError(config_path, error) from error
        return config_path

    def connect(self) -> SharedProjectServer | None:
        with self._lock:
            if self._shared is not None:
                self._shared.ensure()
                return self._shared
            config_path = find_config(self.start_dir)
            if config_path is None:
                return None
            try:
                shared = SharedProjectServer(load_config(config_path))
                shared.ensure()
            except (OSError, RuntimeError, TypeError, ValueError, yaml.YAMLError) as error:
                raise ProjectSetupError(config_path, error) from error
            self._shared = shared
            return shared

    def require_shared(self) -> SharedProjectServer:
        shared = self.connect()
        if shared is None:
            raise RuntimeError(
                f"this folder is not set up for review: {DEFAULT_CONFIG_NAME} was not found "
                f"from {self.start_dir}. Ask the user whether to set it up now; if they agree, "
                f"follow {INIT_GUIDE} with {CLI}."
            )
        return shared

    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.require_shared().port}"

    def stop(self) -> None:
        with self._lock:
            if self._shared is not None:
                self._shared.stop()
            self._shared = None
