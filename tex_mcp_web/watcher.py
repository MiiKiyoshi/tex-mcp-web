"""File system watcher for TeX files using watchdog."""

import asyncio
import fnmatch
import logging
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Coroutine

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

_AUX_EXTENSIONS = {
    ".aux", ".log", ".out", ".toc", ".lof", ".lot",
    ".bbl", ".blg", ".idx", ".ind", ".ilg",
    ".fls", ".fdb_latexmk", ".synctex",
    ".pdf", ".dvi", ".ps", ".gz",
}
_COMPOUND_AUX_EXTENSIONS = (".synctex.gz",)


def matches_patterns(path: str | Path, watch_dir: Path, patterns: list[str]) -> bool:
    """Return whether *path* matches a basename or project-relative glob."""
    path_obj = Path(path)
    name = path_obj.name
    if path_obj.is_absolute():
        try:
            relative = path_obj.relative_to(watch_dir.resolve()).as_posix()
        except ValueError:
            return False
    else:
        relative = path_obj.as_posix()
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern)
        for pattern in patterns
    )


def is_watched_source(
    path: str | Path,
    watch_dir: Path,
    watch_patterns: list[str],
    ignore_patterns: list[str],
) -> bool:
    """Apply the watcher rules used to decide which project files are source."""
    path_obj = Path(path)
    if path_obj.is_absolute():
        try:
            relative = path_obj.relative_to(watch_dir.resolve()).as_posix()
        except ValueError:
            return False
    else:
        relative = path_obj.as_posix()

    if relative == ".tex-mcp-web.yaml" or relative.startswith(".tex-mcp-web/"):
        return False
    if path_obj.suffix in _AUX_EXTENSIONS or path_obj.name.endswith(_COMPOUND_AUX_EXTENSIONS):
        return False
    if matches_patterns(path_obj, watch_dir, ignore_patterns):
        return False
    return matches_patterns(path_obj, watch_dir, watch_patterns)


class TexFileHandler(FileSystemEventHandler):
    """Handle file system events for TeX files."""

    def __init__(
        self,
        watch_dir: Path,
        watch_patterns: list[str],
        ignore_patterns: list[str],
        callback: Callable[[str], Coroutine],
        loop: asyncio.AbstractEventLoop,
        debounce_seconds: float = 0.5,
    ):
        """Initialize handler.

        Args:
            watch_dir: Project root used to make event paths relative.
            watch_patterns: Glob patterns for files to watch.
            ignore_patterns: Glob patterns for files to ignore.
            callback: Async callback to invoke on changes.
            loop: Event loop to schedule callbacks on.
            debounce_seconds: Minimum time between callbacks.
        """
        super().__init__()
        self.watch_dir = watch_dir.resolve()
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
        return matches_patterns(path, self.watch_dir, patterns)

    def _should_process(self, path: str) -> bool:
        """Check if a file change should trigger recompilation."""
        return is_watched_source(
            path,
            self.watch_dir,
            self.watch_patterns,
            self.ignore_patterns,
        )

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

    def on_moved(self, event: FileSystemEvent) -> None:
        """Handle file move.

        Editors and agent tools that save atomically (write to a temp file,
        then rename over the target) surface as moved events, not modified.
        """
        if event.is_directory:
            return
        dest_path = getattr(event, "dest_path", None)
        if not dest_path:
            return
        if isinstance(dest_path, bytes):
            dest_path = dest_path.decode("utf-8", errors="replace")
        if self._should_process(dest_path):
            logger.info(f"File moved into place: {dest_path}")
            self._schedule_callback(dest_path)

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
    """Watch the directories that hold the paper's sources and trigger recompilation."""

    def __init__(
        self,
        watch_dir: Path,
        watch_patterns: list[str],
        ignore_patterns: list[str],
        on_change: Callable[[str], Coroutine],
        roots: list[Path],
        debounce_seconds: float = 0.5,
    ):
        """Initialize watcher.

        Args:
            watch_dir: Project directory, watched flat for the config and top-level files.
            watch_patterns: Glob patterns for files to watch.
            ignore_patterns: Glob patterns for files to ignore.
            on_change: Async callback when files change.
            roots: Directories below watch_dir that hold sources, watched recursively.
                Nothing else under the project is enumerated: a watch goes on every
                directory under a recursive root, and the inotify limit is the login's.
            debounce_seconds: Minimum time between callbacks.
        """
        self.watch_dir = watch_dir
        self.watch_patterns = watch_patterns
        self.ignore_patterns = ignore_patterns
        self.on_change = on_change
        self.roots: list[Path] = []
        self.debounce_seconds = debounce_seconds
        self._observer: Any = None  # Observer type not well-typed in watchdog stubs
        self._handler: TexFileHandler | None = None
        self._watches: dict[Path, Any] = {}
        self.set_roots(roots)

    def _merge(self, roots: list[Path]) -> list[Path]:
        """Roots below the project, an ancestor standing in for the roots under it.

        The project directory itself is never one: it is watched flat, and watching it
        recursively would walk every unrelated tree beside the sources."""
        project = self.watch_dir.resolve()
        inside = sorted({root.resolve() for root in roots if project in root.resolve().parents})
        merged: list[Path] = []
        for root in inside:
            if not any(kept == root or kept in root.parents for kept in merged):
                merged.append(root)
        return merged

    def set_roots(self, roots: list[Path]) -> tuple[list[Path], list[Path]]:
        """Make these the source directories under watch; return (added, dropped).

        The set is exact: a directory no source is read from any more gives its
        watches back, and one a new source lives in takes its own.
        """
        wanted = self._merge(roots)
        added = [root for root in wanted if root not in self.roots]
        dropped = [root for root in self.roots if root not in wanted]
        self.roots = wanted
        if self._observer is not None:
            for root in dropped:
                watch = self._watches.pop(root, None)
                if watch is not None:
                    self._observer.unschedule(watch)
            for root in added:
                self._schedule(root)
        return added, dropped

    def _schedule(self, root: Path) -> None:
        if not root.is_dir():
            return
        try:
            self._watches[root] = self._observer.schedule(self._handler, str(root), recursive=True)
        except OSError as error:
            logger.error("Cannot watch %s: %s", root, error)

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start watching for file changes.

        Args:
            loop: Event loop for scheduling async callbacks.
        """
        self._handler = TexFileHandler(
            watch_dir=self.watch_dir,
            watch_patterns=self.watch_patterns,
            ignore_patterns=self.ignore_patterns,
            callback=self.on_change,
            loop=loop,
            debounce_seconds=self.debounce_seconds,
        )

        self._observer = Observer()
        self._watches = {}
        try:
            self._observer.schedule(self._handler, str(self.watch_dir), recursive=False)
            for root in self.roots:
                if root.is_dir():
                    self._watches[root] = self._observer.schedule(self._handler, str(root), recursive=True)
            self._observer.start()
        except BaseException:
            # A partially scheduled observer keeps its inotify descriptor and watches
            # until it is stopped; leaking it on every failed start is how a process
            # reaches the limit on its own.
            self._observer.stop()
            self._observer = None
            raise
        logger.info("Started watching %s (flat) and %s for %s",
                    self.watch_dir, [str(root) for root in self.roots], self.watch_patterns)

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
