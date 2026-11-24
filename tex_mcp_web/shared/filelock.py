"""A lock on an open file that other processes honour, on POSIX and on Windows alike."""

from __future__ import annotations

import os
import time
from typing import IO

if os.name == "nt":
    import msvcrt

    def lock(handle: IO, wait: bool = True) -> None:
        """Take the file's lock. Without wait, BlockingIOError when another process holds it."""
        while True:
            handle.seek(0)
            try:
                # The first byte stands for the file: a lock may lie past the end of an empty one.
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as error:
                if not wait:
                    raise BlockingIOError(str(error)) from error
                time.sleep(0.05)

    def unlock(handle: IO) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def lock(handle: IO, wait: bool = True) -> None:
        """Take the file's lock. Without wait, BlockingIOError when another process holds it."""
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock(handle: IO) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
