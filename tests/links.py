"""Symlinks in tests, which Windows lets a user make only with a privilege or Developer Mode."""
import os

import pytest


def symlink(link, target, **kwargs):
    """Make a symlink; on Windows without the privilege to, skip the test."""
    try:
        link.symlink_to(target, **kwargs)
    except OSError as error:
        if os.name == "nt":
            pytest.skip(f"symlinks need a privilege on Windows: {error}")
        raise
