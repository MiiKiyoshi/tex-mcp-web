"""Server-owned, file-backed comment replies. Only Reply blocks are editable."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


@contextlib.contextmanager
def _directory(root: Path, create: bool = False):
    # Walk directory descriptors, never following a symlink, including ancestors.
    root = root.absolute()
    if ".." in root.parts:
        raise ValueError("invalid draft directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _read(fd: int, name: str) -> str:
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(source, "r", encoding="utf-8", newline="") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("draft must be a regular, unlinked file")
        return stream.read()


def export(root: Path, comments: list[dict]) -> dict:
    ids = [comment["id"] for comment in comments]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("comment_ids must be nonempty and unique")
    key = uuid.uuid4().hex
    segments = ["# Comment replies\n\nEdit only Reply blocks; keep their markers. Copy existing prose into a Reply block to import it.\n"]
    for comment in comments:
        segments[-1] += (
            f"\n## {comment['id']}\nUpdated: {comment['updated']}\n\n"
            + json.dumps(comment, ensure_ascii=False, indent=2)
            + f"\n\n### Reply\n<!-- reply:{key}:{comment['id']} -->\n"
        )
        segments.append(f"\n<!-- /reply:{key}:{comment['id']} -->\n")
    body = "".join(segments)
    snapshot = {"ids": ids, "updated": [c["updated"] for c in comments], "segments": segments}
    root = root.absolute()
    with _directory(root, create=True) as fd:
        for suffix, text in ((".snapshot", json.dumps(snapshot, ensure_ascii=False)), (".md", body)):
            target = os.open(key + suffix, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(target, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
    return {"path": str(root / (key + ".md")), "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "comment_ids": ids}


def load(root: Path, path: str) -> tuple[dict[str, str], dict[str, str]]:
    candidate = Path(path)
    if ".." in candidate.parts or candidate.parent != root.absolute() or not re.fullmatch(r"[0-9a-f]{32}\.md", candidate.name):
        raise ValueError("replies_file must be a server-created draft path")
    try:
        with _directory(root) as fd:
            snapshot = json.loads(_read(fd, candidate.stem + ".snapshot"))
            body = _read(fd, candidate.name)
        # Exact immutable segments protect IDs, snapshot timestamps and thread text.
        match = re.fullmatch("(.*?)".join(re.escape(s) for s in snapshot["segments"]), body, re.DOTALL)
        if match is None or len(match.groups()) != len(snapshot["ids"]):
            raise ValueError("malformed draft: edit only Reply blocks")
        replies = dict(zip(snapshot["ids"], match.groups()))
        if not all(text.strip() for text in replies.values()):
            raise ValueError("every Reply block must be nonempty")
        expected = dict(zip(snapshot["ids"], snapshot["updated"]))
        return replies, expected
    except (OSError, UnicodeError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid or unregistered replies_file") from error
