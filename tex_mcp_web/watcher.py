"""File system watcher for TeX files using watchdog."""

import asyncio
import fnmatch
import logging
from pathlib import Path
from concurrent.futures import Future
from typing import Any, Callable, Coroutine

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


class TexFileHandler(FileSystemEventHandler):
    """Handle file system events for TeX files."""

    def __init__(
        self,
        watch_patterns: list[str],
        ignore_patterns: list[str],
        callback: Callable[[str], Coroutine],
        loop: asyncio.AbstractEventLoop,
        debounce_seconds: float = 0.5,
    ):
        """Initialize handler.

        Args:
            watch_patterns: Glob patterns for files to watch.
            ignore_patterns: Glob patterns for files to ignore.
            callback: Async callback to invoke on changes.
            loop: Event loop to schedule callbacks on.
            debounce_seconds: Minimum time between callbacks.
        """
        super().__init__()
        self.watch_patterns = watch_patterns
        self.ignore_patterns = ignore_patterns
        self.callback = callback
        self.loop = loop
        self.debounce_seconds = debounce_seconds
        self._last_event_time: float = 0
        self._pending_task: Future[Any] | None = None
        self._pending_path: str | None = None

    def _matches_patterns(self, path: str, patterns: list[str]) -> bool:
        """Check if path matches any of the patterns."""
        path_obj = Path(path)
        name = path_obj.name
        relative = str(path_obj)

        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
            if fnmatch.fnmatch(relative, pattern):
                return True

        return False

    def _should_process(self, path: str) -> bool:
        """Check if a file change should trigger recompilation."""
        # Ignore auxiliary files
        path_obj = Path(path)
        name = path_obj.name

        # Simple extensions (checked via suffix)
        aux_extensions = {
            ".aux", ".log", ".out", ".toc", ".lof", ".lot",
            ".bbl", ".blg", ".idx", ".ind", ".ilg",
            ".fls", ".fdb_latexmk", ".synctex",
            ".pdf", ".dvi", ".ps", ".gz",
        }
        if path_obj.suffix in aux_extensions:
            return False

        # Compound extensions (checked via endswith on full name)
        compound_extensions = (".synctex.gz",)
        if name.endswith(compound_extensions):
            return False

        # Check ignore patterns
        if self._matches_patterns(path, self.ignore_patterns):
            logger.debug(f"Ignoring {path} (matches ignore pattern)")
            return False

        # Check watch patterns
        if self._matches_patterns(path, self.watch_patterns):
            return True

        return False

    def _schedule_callback(self, src_path: str):
        """Schedule the callback with debouncing."""
        self._pending_path = src_path

        # Cancel any pending callback
        if self._pending_task and not self._pending_task.done():
            self._pending_task.cancel()

        async def delayed_callback():
            await asyncio.sleep(self.debounce_seconds)
            try:
                await self.callback(self._pending_path)
            except Exception as e:
                logger.error(f"Callback error: {e}")

        # Schedule the coroutine without blocking (don't call .result())
        # The Future is stored but we don't wait for it - the observer thread
        # must not block or it will miss subsequent file system events.
        self._pending_task = asyncio.run_coroutine_threadsafe(
            delayed_callback(), self.loop
        )

    def _get_src_path(self, event: FileSystemEvent) -> str:
        """Extract src_path as string (handles bytes on some platforms)."""
        src_path = event.src_path
        if isinstance(src_path, bytes):
            return src_path.decode("utf-8", errors="replace")
        return src_path

    def on_modified(self, event: FileSystemEvent) -> None:
        """Handle file modification."""
        if event.is_directory:
            return
        src_path = self._get_src_path(event)
        if self._should_process(src_path):
            logger.info(f"File modified: {src_path}")
            self._schedule_callback(src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        """Handle file creation."""
        if event.is_directory:
            return
        src_path = self._get_src_path(event)
        if self._should_process(src_path):
            logger.info(f"File created: {src_path}")
            self._schedule_callback(src_path)

    def update_debounce(self, last_compile_seconds: float) -> None:
        """Adapt debounce interval based on compilation speed.

        Fast compiles (<2s) → shorten debounce for responsiveness.
        Slow compiles (>10s) → lengthen debounce to avoid re-triggering mid-compile.
        """
        if last_compile_seconds < 2.0:
            self.debounce_seconds = max(0.3, self.debounce_seconds * 0.8)
        elif last_compile_seconds > 10.0:
            self.debounce_seconds = min(3.0, last_compile_seconds * 0.2)


class Watcher:
    """Watch TeX files for changes and trigger recompilation."""

    def __init__(
        self,
        watch_dir: Path,
        watch_patterns: list[str],
        ignore_patterns: list[str],
        on_change: Callable[[str], Coroutine],
        debounce_seconds: float = 0.5,
    ):
        """Initialize watcher.

        Args:
            watch_dir: Directory to watch.
            watch_patterns: Glob patterns for files to watch.
            ignore_patterns: Glob patterns for files to ignore.
            on_change: Async callback when files change.
            debounce_seconds: Minimum time between callbacks.
        """
        self.watch_dir = watch_dir
        self.watch_patterns = watch_patterns
        self.ignore_patterns = ignore_patterns
        self.on_change = on_change
        self.debounce_seconds = debounce_seconds
        self._observer: Any = None  # Observer type not well-typed in watchdog stubs
        self._handler: TexFileHandler | None = None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start watching for file changes.

        Args:
            loop: Event loop for scheduling async callbacks.
        """
        self._handler = TexFileHandler(
            watch_patterns=self.watch_patterns,
            ignore_patterns=self.ignore_patterns,
            callback=self.on_change,
            loop=loop,
            debounce_seconds=self.debounce_seconds,
        )

        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            str(self.watch_dir),
            recursive=True,
        )
        self._observer.start()
        logger.info(f"Started watching {self.watch_dir} for {self.watch_patterns}")

    def stop(self) -> None:
        """Stop watching for file changes."""
        if self._handler and self._handler._pending_task:
            self._handler._pending_task.cancel()
            self._handler._pending_task = None
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("Stopped file watcher")

    @property
    def is_running(self) -> bool:
        """Check if watcher is running."""
        return self._observer is not None and self._observer.is_alive()
