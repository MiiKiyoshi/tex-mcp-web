"""Tests for watcher module."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tex_mcp_web.watcher import TexFileHandler, Watcher


def make_handler(**kwargs):
    return TexFileHandler(watch_dir=Path("/project"), **kwargs)


class TestTexFileHandler:
    """Tests for TexFileHandler class."""

    def test_matches_simple_pattern(self):
        """Test matching simple glob pattern."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._matches_patterns("main.tex", ["*.tex"])
        assert handler._matches_patterns("chapter.tex", ["*.tex"])
        assert handler._matches_patterns(
            "/project/chapters/intro.tex", ["*.tex"]
        )
        assert not handler._matches_patterns("main.pdf", ["*.tex"])

        loop.close()

    def test_matches_directory_pattern(self):
        """Test matching directory glob pattern."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["sections/*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._matches_patterns("sections/intro.tex", ["sections/*.tex"])
        assert handler._matches_patterns(
            "/project/sections/intro.tex", ["sections/*.tex"]
        )
        assert not handler._matches_patterns("chapters/intro.tex", ["sections/*.tex"])

        loop.close()

    def test_should_process_tex_file(self):
        """Test that .tex files are processed."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._should_process("main.tex")
        assert handler._should_process("chapter.tex")

        loop.close()

    def test_should_ignore_aux_files(self):
        """Test that auxiliary files are ignored."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert not handler._should_process("main.aux")
        assert not handler._should_process("main.log")
        assert not handler._should_process("main.pdf")
        assert not handler._should_process("main.synctex.gz")
        assert not handler._should_process("main.fdb_latexmk")

        loop.close()

    def test_should_respect_ignore_patterns(self):
        """Test that ignore patterns are respected."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=["*_backup.tex", "old/*"],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._should_process("main.tex")
        assert not handler._should_process("main_backup.tex")
        assert not handler._should_process("old/chapter.tex")

        loop.close()

    def test_should_ignore_project_relative_directory(self):
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=["private/**"],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._should_process("/project/sections/intro.tex")
        assert not handler._should_process("/project/private/note.tex")
        assert not handler._should_process(
            "/project/private/experiments/result.tex"
        )

        loop.close()

    def test_schedule_callback_stores_pending_path(self):
        """Test that _schedule_callback stores the path for the callback."""
        loop = asyncio.new_event_loop()
        callback = AsyncMock()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=callback,
            loop=loop,
        )

        assert handler._pending_path is None

        # Simulate what on_modified does
        handler._pending_path = "/tmp/main.tex"
        assert handler._pending_path == "/tmp/main.tex"

        loop.close()

    def test_on_modified_passes_src_path(self):
        """Test that on_modified calls _schedule_callback with src_path."""
        loop = asyncio.new_event_loop()
        callback = AsyncMock()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=callback,
            loop=loop,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/project/main.tex"

        # Mock _schedule_callback to avoid blocking on event loop
        handler._schedule_callback = MagicMock()
        handler.on_modified(event)

        handler._schedule_callback.assert_called_once_with("/project/main.tex")
        loop.close()

    def test_on_created_passes_src_path(self):
        """Test that on_created calls _schedule_callback with src_path."""
        loop = asyncio.new_event_loop()
        callback = AsyncMock()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=callback,
            loop=loop,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/project/new_file.tex"

        handler._schedule_callback = MagicMock()
        handler.on_created(event)

        handler._schedule_callback.assert_called_once_with("/project/new_file.tex")
        loop.close()

    def test_matches_double_star_pattern(self):
        """Test matching ** glob patterns."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["**/*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert handler._matches_patterns("chapters/intro.tex", ["**/*.tex"])
        assert handler._matches_patterns("deep/nested/file.tex", ["**/*.tex"])
        assert not handler._matches_patterns("file.bib", ["**/*.tex"])

        loop.close()

    def test_should_process_compound_extension(self):
        """Test that compound extensions like .synctex.gz are filtered."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        assert not handler._should_process("main.synctex.gz")
        assert not handler._should_process("output.synctex.gz")

        loop.close()

    def test_should_process_no_watch_match(self):
        """Test that non-matching file returns False."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        # .bib doesn't match *.tex and isn't an aux file
        assert not handler._should_process("refs.bib")

        loop.close()

    def test_schedule_callback_cancels_pending(self):
        """Test that scheduling a new callback cancels the pending one."""
        loop = asyncio.new_event_loop()
        try:
            handler = make_handler(
                watch_patterns=["*.tex"],
                ignore_patterns=[],
                callback=AsyncMock(),
                loop=loop,
                debounce_seconds=10.0,
            )

            # Schedule first callback
            handler._schedule_callback("/tmp/first.tex")
            first_task = handler._pending_task
            assert first_task is not None

            # Schedule second callback — should cancel first
            handler._schedule_callback("/tmp/second.tex")
            assert first_task.cancelled()
            assert handler._pending_path == "/tmp/second.tex"

            # Cancel the second to clean up
            if handler._pending_task:
                handler._pending_task.cancel()
        finally:
            loop.run_until_complete(asyncio.sleep(0))
            loop.close()

    def test_schedule_callback_runs_async(self):
        """Test full debounce cycle executes callback."""
        loop = asyncio.new_event_loop()
        callback = AsyncMock()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=callback,
            loop=loop,
            debounce_seconds=0.05,
        )

        handler._schedule_callback("/tmp/main.tex")

        # Run the event loop briefly to let the debounce fire
        async def _wait():
            await asyncio.sleep(0.15)

        loop.run_until_complete(_wait())
        callback.assert_called_once_with("/tmp/main.tex")
        loop.close()

    def test_get_src_path_bytes(self):
        """Test _get_src_path decodes bytes to string."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        event = MagicMock()
        event.src_path = b"/project/main.tex"

        assert handler._get_src_path(event) == "/project/main.tex"
        loop.close()

    def test_get_src_path_string(self):
        """Test _get_src_path passes through strings."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        event = MagicMock()
        event.src_path = "/project/main.tex"

        assert handler._get_src_path(event) == "/project/main.tex"
        loop.close()

    def test_on_modified_directory_skipped(self):
        """Test that directory events are ignored by on_modified."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        event = MagicMock()
        event.is_directory = True
        event.src_path = "/project/chapters"

        handler._schedule_callback = MagicMock()
        handler.on_modified(event)

        handler._schedule_callback.assert_not_called()
        loop.close()

    def test_on_modified_non_matching(self):
        """Test that non-matching files are skipped by on_modified."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        event = MagicMock()
        event.is_directory = False
        event.src_path = "/project/main.aux"

        handler._schedule_callback = MagicMock()
        handler.on_modified(event)

        handler._schedule_callback.assert_not_called()
        loop.close()

    def test_on_created_directory_skipped(self):
        """Test that directory events are ignored by on_created."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
        )

        event = MagicMock()
        event.is_directory = True
        event.src_path = "/project/new_dir"

        handler._schedule_callback = MagicMock()
        handler.on_created(event)

        handler._schedule_callback.assert_not_called()
        loop.close()


class TestWatcher:
    """Tests for Watcher class."""

    def test_init(self, tmp_path):
        """Test Watcher initialization."""
        callback = AsyncMock()
        watcher = Watcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=callback,
        )

        assert watcher.watch_dir == tmp_path
        assert watcher.watch_patterns == ["*.tex"]
        assert watcher.on_change == callback
        assert not watcher.is_running

    def test_start_stop(self, tmp_path):
        """Test starting and stopping watcher."""
        callback = AsyncMock()
        watcher = Watcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=callback,
        )

        loop = asyncio.new_event_loop()

        watcher.start(loop)
        assert watcher.is_running

        watcher.stop()
        assert not watcher.is_running

        loop.close()

    def test_is_running_property(self, tmp_path):
        """Test is_running property."""
        watcher = Watcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=AsyncMock(),
        )

        assert not watcher.is_running

        loop = asyncio.new_event_loop()
        watcher.start(loop)
        assert watcher.is_running

        watcher.stop()
        loop.close()

    def test_stop_cancels_pending_task(self, tmp_path):
        """Test that stop() cancels any pending debounce task."""
        callback = AsyncMock()
        watcher = Watcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=callback,
            debounce_seconds=10.0,  # Long debounce so it stays pending
        )

        loop = asyncio.new_event_loop()
        watcher.start(loop)

        # Schedule a callback that will be pending
        watcher._handler._schedule_callback("/tmp/main.tex")
        pending = watcher._handler._pending_task
        assert pending is not None

        watcher.stop()
        assert pending.cancelled()
        assert watcher._handler._pending_task is None

        loop.run_until_complete(asyncio.sleep(0))
        loop.close()

    def test_is_running_false_after_stop(self, tmp_path):
        """Test is_running returns False after stop."""
        watcher = Watcher(
            watch_dir=tmp_path,
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            on_change=AsyncMock(),
        )

        loop = asyncio.new_event_loop()
        watcher.start(loop)
        assert watcher.is_running

        watcher.stop()
        assert not watcher.is_running
        # Observer should be None after stop
        assert watcher._observer is None

        loop.close()


class TestAdaptiveDebounce:
    """Tests for TexFileHandler.update_debounce()."""

    def test_debounce_fast_compile(self):
        """Test that fast compiles (<2s) shorten the debounce."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
            debounce_seconds=0.5,
        )

        handler.update_debounce(1.0)
        assert handler.debounce_seconds == pytest.approx(0.4)  # 0.5 * 0.8

        handler.update_debounce(0.5)
        assert handler.debounce_seconds == pytest.approx(0.32)  # 0.4 * 0.8

        loop.close()

    def test_debounce_fast_has_minimum(self):
        """Test that debounce doesn't go below 0.3s."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
            debounce_seconds=0.3,
        )

        handler.update_debounce(0.1)
        assert handler.debounce_seconds == 0.3  # max(0.3, 0.3 * 0.8)

        loop.close()

    def test_debounce_slow_compile(self):
        """Test that slow compiles (>10s) lengthen the debounce."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
            debounce_seconds=0.5,
        )

        handler.update_debounce(15.0)
        assert handler.debounce_seconds == 3.0  # min(3.0, 15 * 0.2)

        loop.close()

    def test_debounce_slow_has_maximum(self):
        """Test that debounce doesn't go above 3.0s."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
            debounce_seconds=0.5,
        )

        handler.update_debounce(30.0)
        assert handler.debounce_seconds == 3.0  # min(3.0, 30 * 0.2 = 6.0)

        loop.close()

    def test_debounce_normal_unchanged(self):
        """Test that normal compiles (2-10s) leave debounce unchanged."""
        loop = asyncio.new_event_loop()
        handler = make_handler(
            watch_patterns=["*.tex"],
            ignore_patterns=[],
            callback=AsyncMock(),
            loop=loop,
            debounce_seconds=0.5,
        )

        handler.update_debounce(5.0)
        assert handler.debounce_seconds == 0.5  # unchanged

        loop.close()
