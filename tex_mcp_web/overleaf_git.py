"""The paper's folder as a git clone of its Overleaf project.

Overleaf serves a project as a git repository whose address ends in the project's id
(``https://git.overleaf.com/<id>``). A folder cloned from it follows Overleaf by pull and
push, and the two are compared by commit: the folder's HEAD against the commit Overleaf's
branch is at, read by a fetch. A fetch changes no file in the folder, only what the folder
knows of Overleaf's branch.

Pull takes Overleaf's commits in, as a fast-forward or, when both sides have commits, as a
merge git can make without conflicts. Push sends the folder's commits when Overleaf has
none the folder lacks, and never forces. Neither touches a change that is not committed:
git refuses a pull that would write over one, and its message says which file.

Remote addresses can carry a token, and git repeats them in its messages, so every message
is passed on with the credentials taken out.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path

# Seconds a git command may take; a fetch or push to Overleaf is the slow one.
TIMEOUT = 60


class GitError(Exception):
    """A git command that failed or that the state of the folder refuses, with why."""


@dataclass(frozen=True)
class Link:
    folder: Path
    remote: str


def _scrub(text: str) -> str:
    return re.sub(r"(\w+://)[^@/\s]+@", r"\1", text)


async def _git(folder: Path, *args: str, check: bool = True) -> tuple[int, str]:
    """Run git in *folder* with no prompt for credentials; its output, or GitError."""
    process = await asyncio.create_subprocess_exec(
        "git", "-C", str(folder), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
    try:
        out, err = await asyncio.wait_for(process.communicate(), TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise GitError(f"git {args[0]} took longer than {TIMEOUT} s") from None
    if check and process.returncode != 0:
        said = _scrub(err.decode(errors="replace") or out.decode(errors="replace")).strip()
        raise GitError(said or f"git {args[0]} failed")
    return process.returncode, _scrub(out.decode(errors="replace"))


async def link_of(folder: Path, project_id: str) -> Link | None:
    """The remote of the git repository *folder* is in whose address ends in the project's
    id, or None: no repository, or none of its remotes is that project."""
    code, out = await _git(folder, "config", "--local", "--get-regexp", r"^remote\..*\.url$", check=False)
    if code != 0:
        return None
    for line in out.splitlines():
        key, _, url = line.partition(" ")
        if url.strip().rstrip("/").removesuffix(".git").rsplit("/", 1)[-1] == project_id:
            return Link(folder, key.removeprefix("remote.").removesuffix(".url"))
    return None


async def fetch(link: Link) -> None:
    await _git(link.folder, "fetch", "--quiet", link.remote)


async def _tracking(link: Link) -> str:
    """Overleaf's branch as this folder knows it, like ``overleaf/main``: the current
    branch's upstream when it is on that remote, else the remote's one branch."""
    code, out = await _git(link.folder, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}",
                           check=False)
    if code == 0 and out.strip().startswith(f"{link.remote}/"):
        return out.strip()
    _, out = await _git(link.folder, "for-each-ref", "--format=%(refname)", f"refs/remotes/{link.remote}/")
    branches = [ref.removeprefix("refs/remotes/") for ref in out.split() if not ref.endswith("/HEAD")]
    if len(branches) != 1:
        raise GitError(f"{link.remote} has {len(branches)} branches here. Set the current branch's "
                       f"upstream to the one Overleaf uses (git branch -u {link.remote}/<branch>).")
    return branches[0]


async def state(link: Link) -> dict:
    """Where the folder stands against Overleaf's branch as last fetched: the two commits,
    how many each has that the other lacks, and the files with changes not committed."""
    branch = await _tracking(link)
    _, here = await _git(link.folder, "rev-parse", "--short", "HEAD")
    _, there = await _git(link.folder, "rev-parse", "--short", branch)
    _, counts = await _git(link.folder, "rev-list", "--left-right", "--count", f"HEAD...{branch}")
    ahead, behind = (int(count) for count in counts.split())
    _, porcelain = await _git(link.folder, "status", "--porcelain", "--untracked-files=no", "--", ".")
    return {"remote": link.remote, "branch": branch, "here": here.strip(), "there": there.strip(),
            "ahead": ahead, "behind": behind,
            "state": "same" if not ahead and not behind else "ahead" if not behind
            else "behind" if not ahead else "diverged",
            "uncommitted": [line[3:] for line in porcelain.splitlines()]}


async def pull(link: Link) -> dict:
    """Take Overleaf's commits in; the state after, or GitError saying why not."""
    await fetch(link)
    now = await state(link)
    if now["state"] == "behind":
        await _git(link.folder, "merge", "--ff-only", "--quiet", now["branch"])
    elif now["state"] == "diverged":
        code, out = await _git(link.folder, "merge-tree", "--write-tree", "--name-only", "HEAD", now["branch"],
                               check=False)
        if code != 0:
            # The first line is the tree; the files that conflict follow, up to a blank line.
            files = out.split("\n\n", 1)[0].splitlines()[1:]
            raise GitError(f"Overleaf and this folder changed the same lines in {', '.join(files) or 'a file'}. "
                           "Merge them by hand with git.")
        await _git(link.folder, "merge", "--no-edit", "--quiet", now["branch"])
    else:
        raise GitError("Overleaf has no commit this folder lacks")
    return await state(link)


async def push(link: Link) -> dict:
    """Send the folder's commits to Overleaf; the state after, or GitError saying why not."""
    await fetch(link)
    now = await state(link)
    if now["state"] in ("behind", "diverged"):
        raise GitError("Overleaf has commits this folder lacks. Pull first.")
    if now["state"] == "same":
        raise GitError("this folder has no commit Overleaf lacks")
    await _git(link.folder, "push", "--quiet", link.remote, f"HEAD:{now['branch'].removeprefix(link.remote + '/')}")
    await fetch(link)
    return await state(link)


async def holds(link: Link, revision: str, path: str, text: str) -> bool:
    """Whether the file at *path*, relative to the folder, holds *text* at *revision*."""
    code, out = await _git(link.folder, "show", f"{revision}:./{path}", check=False)
    return code == 0 and text in out
