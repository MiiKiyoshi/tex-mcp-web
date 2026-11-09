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

While the files are also kept the same with Overleaf as they change (``followed``), those
files already hold Overleaf's text when its commit arrives. Pull then stages each one
rather than writing it, and the merge of a folder with its own commits takes Overleaf's
text for them, the folder's commits adding what else they changed, such as figures and new
files. Push first takes Overleaf's commits in that way, so what it sends holds Overleaf's
text for those files, and sends no commit whose text for one of them is not Overleaf's
now: Overleaf's git server applies the files that differ from its last commit, and turns a
push away once the project has moved past it.

Remote addresses can carry a token, and git repeats them in its messages, so every message
is passed on with the credentials taken out.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Collection
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


# The review page's own records beside the paper, which never go to Overleaf.
RECORDS = ".tex-mcp-web/"


async def _git(folder: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run git in *folder* with no prompt for credentials; its output, or GitError."""
    process = await asyncio.create_subprocess_exec(
        "git", "-C", str(folder), "-c", "core.quotepath=off", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C", **(env or {})})
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


async def keep_records_out(folder: Path) -> None:
    """Name the review page's own records, .tex-mcp-web/, in the local exclude file of the
    git repository *folder* is in, a clone of Overleaf or one of the reviewer's own, so a
    commit of every file leaves them out: they hold the comments, copies of the text and the
    page's locks, which a checkout of an older commit would set back under an open page.
    The exclude file is the repository's own and is never committed. A folder in no git
    repository, or on a machine without git, is left as it is."""
    try:
        code, out = await _git(folder, "rev-parse", "--git-path", "info/exclude", check=False)
    except FileNotFoundError:
        return
    if code != 0:
        return
    exclude = (folder / out.strip()).resolve()
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if RECORDS not in text.splitlines():
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write(("\n" if text and not text.endswith("\n") else "") + f"{RECORDS}\n")


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


async def state(link: Link, followed: Collection[str] = ()) -> dict:
    """Where the folder stands against Overleaf's branch as last fetched: the two commits,
    how many each has that the other lacks, and the files with changes not committed,
    leaving out the *followed* ones, which are on Overleaf as they are here."""
    branch = await _tracking(link)
    _, here = await _git(link.folder, "rev-parse", "--short", "HEAD")
    _, there = await _git(link.folder, "rev-parse", "--short", branch)
    _, counts = await _git(link.folder, "rev-list", "--left-right", "--count", f"HEAD...{branch}")
    ahead, behind = (int(count) for count in counts.split())
    _, porcelain = await _git(link.folder, "status", "--porcelain", "-z", "--untracked-files=no", "--", ".")
    uncommitted, records = [], iter(_paths(porcelain))
    for record in records:
        if {"R", "C"} & set(record[:2]):     # a rename or copy names where it came from next
            next(records, None)
        if record[3:] not in followed:
            uncommitted.append(record[3:])
    return {"remote": link.remote, "branch": branch, "here": here.strip(), "there": there.strip(),
            "ahead": ahead, "behind": behind,
            "state": "same" if not ahead and not behind else "ahead" if not behind
            else "behind" if not ahead else "diverged",
            "uncommitted": uncommitted}


async def prefix(link: Link) -> str:
    """Where the folder is in its repository: "" at the top, else like ``paper/``."""
    _, out = await _git(link.folder, "rev-parse", "--show-prefix")
    return out.strip()


# The functions below take the folder as the top of its repository, which is where Overleaf's
# files are, and name files from there, as git and Overleaf both do.

def _paths(out: str) -> list[str]:
    """The names a git command printed one after another with ``-z``."""
    return [path for path in out.split("\0") if path]


async def _blob(link: Link, revision: str, path: str) -> str | None:
    """The object *path* has at *revision*, or None."""
    code, out = await _git(link.folder, "rev-parse", "--verify", "--quiet", f"{revision}:{path}", check=False)
    return out.strip() if code == 0 else None


async def _in_folder(link: Link, path: str) -> str | None:
    """The object the file at *path* in the folder would be, or None."""
    if not (link.folder / path).is_file():
        return None
    _, out = await _git(link.folder, "hash-object", "--", path)
    return out.strip()


async def _modes(link: Link, revision: str, paths: Collection[str]) -> dict[str, str]:
    _, out = await _git(link.folder, "ls-tree", "-z", revision, "--", *paths)
    return {record.partition("\t")[2]: record.split()[0] for record in _paths(out)}


async def _take(link: Link, target: str, followed: Collection[str]) -> None:
    """Move HEAD forward to *target*. A *followed* file target changes is already in the
    folder as target has it, so target's copy is put in the index rather than written, and
    one that is not, an edit made since Overleaf's commit was read, stops the move. Every
    other file moves as a fast-forward moves it: git refuses to write over a change not
    committed, and then the index is put back as it was."""
    _, unmerged = await _git(link.folder, "ls-files", "-u")
    if unmerged:
        raise GitError("A merge in this folder is not finished. Finish it with git first.")
    _, out = await _git(link.folder, "diff", "--name-only", "-z", "--no-renames", "HEAD", target)
    taken, later = {}, []
    for path in _paths(out):
        wanted = await _blob(link, target, path) if path in followed else None
        if wanted is None:
            continue
        if await _in_folder(link, path) == wanted:
            taken[path] = wanted
        else:
            later.append(path)
    if later:
        raise GitError(f"{', '.join(later)} changed after Overleaf's commit was read. Press git pull again in a moment.")
    if not taken:
        await _git(link.folder, "merge", "--ff-only", "--quiet", target)
        return
    _, listed = await _git(link.folder, "ls-files", "-s", "-z", "--", *taken)
    before = {}
    for record in _paths(listed):
        info, _, path = record.partition("\t")
        mode, blob, stage = info.split()
        if stage == "0":
            before[path] = f"{mode},{blob},{path}"
    modes = await _modes(link, target, taken)
    await _git(link.folder, "update-index", "--add",
               *[arg for path, blob in taken.items() for arg in ("--cacheinfo", f"{modes[path]},{blob},{path}")])
    try:
        await _git(link.folder, "merge", "--ff-only", "--quiet", target)
    except GitError:
        if before:
            await _git(link.folder, "update-index", *[arg for entry in before.values() for arg in ("--cacheinfo", entry)])
        if new := [path for path in taken if path not in before]:
            await _git(link.folder, "update-index", "--force-remove", "--", *new)
        raise


async def _merge_followed(link: Link, branch: str, followed: Collection[str]) -> str:
    """A merge commit of HEAD and Overleaf's *branch*, made without touching the folder or
    its index: every *followed* file as Overleaf has it, and every other file the folder's
    own commits changed as they left it. A file both sides changed differently stops it."""
    _, base = await _git(link.folder, "merge-base", "HEAD", branch)
    base = base.strip()
    _, changed = await _git(link.folder, "diff", "--name-only", "-z", "--no-renames", base, "HEAD")
    scratch = tempfile.TemporaryDirectory(prefix="tex-mcp-web-index-")
    env = {"GIT_INDEX_FILE": str(Path(scratch.name) / "index")}
    try:
        await _git(link.folder, "read-tree", branch, env=env)
        clashes = []
        for path in _paths(changed):
            if path in followed:
                continue
            ours, theirs = await _blob(link, "HEAD", path), await _blob(link, branch, path)
            if theirs != await _blob(link, base, path) and theirs != ours:
                clashes.append(path)
            elif ours is None:
                await _git(link.folder, "update-index", "--force-remove", "--", path, env=env)
            else:
                mode = (await _modes(link, "HEAD", [path]))[path]
                await _git(link.folder, "update-index", "--add", "--cacheinfo", f"{mode},{ours},{path}", env=env)
        if clashes:
            raise GitError(f"Overleaf and this folder changed {', '.join(clashes)} differently. "
                           "Merge them by hand with git.")
        _, tree = await _git(link.folder, "write-tree", env=env)
    finally:
        scratch.cleanup()
    _, commit = await _git(link.folder, "commit-tree", tree.strip(), "-p", "HEAD", "-p", branch,
                           "-m", f"Merge remote-tracking branch '{branch}'")
    return commit.strip()


async def _take_in(link: Link, now: dict, followed: Collection[str]) -> None:
    """Take Overleaf's commits in while *followed* files are kept the same with Overleaf."""
    target = now["branch"] if now["state"] == "behind" else await _merge_followed(link, now["branch"], followed)
    await _take(link, target, followed)


async def _replaces(link: Link, target: str, followed: Collection[str]) -> bool:
    """Whether taking *target* in would write over a file here that git does not track,
    such as one it ignores, with other content: only a pull asked for does that."""
    _, out = await _git(link.folder, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=A", "HEAD", target)
    for path in _paths(out):
        here = None if path in followed else await _in_folder(link, path)
        if here is not None and here != await _blob(link, target, path):
            return True
    return False


async def follow(link: Link, followed: Collection[str]) -> dict:
    """Read Overleaf's branch, and when it is only ahead, take its commits in: they hold
    the *followed* files as they are here already, and what else they change, such as a
    figure added on Overleaf, comes with them. The state after; a pull git refuses, or one
    that would write over a file git does not track, waits for Pull, which says why."""
    await fetch(link)
    now = await state(link, followed)
    if now["state"] == "behind" and not await _replaces(link, now["branch"], followed):
        try:
            await _take_in(link, now, followed)
        except GitError:
            return now
        now = await state(link, followed)
    return now


async def _not_overleafs(link: Link, branch: str, followed: Collection[str]) -> list[str]:
    """The *followed* files a push would send whose committed text is not the folder's,
    which is Overleaf's text now: sending one would put older text over Overleaf's."""
    _, out = await _git(link.folder, "diff", "--name-only", "-z", "--no-renames", branch, "HEAD")
    return [path for path in _paths(out)
            if path in followed and await _blob(link, "HEAD", path) != await _in_folder(link, path)]


async def pull(link: Link, followed: Collection[str] | None = None) -> dict:
    """Take Overleaf's commits in; the state after, or GitError saying why not. With
    *followed* files kept the same with Overleaf, see _take_in."""
    await fetch(link)
    now = await state(link, followed or ())
    if followed is not None and now["state"] in ("behind", "diverged"):
        await _take_in(link, now, followed)
    elif now["state"] == "behind":
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
    return await state(link, followed or ())


async def push(link: Link, followed: Collection[str] | None = None) -> dict:
    """Send the folder's commits to Overleaf; the state after, or GitError saying why not.
    With *followed* files kept the same with Overleaf, Overleaf's commits are taken in
    first (see _take_in), and once more if Overleaf moved on while the push was made, and
    a commit whose text for one of them is not Overleaf's now is not sent."""
    for attempt in range(2 if followed is not None else 1):
        await fetch(link)
        now = await state(link, followed or ())
        if followed is not None and now["state"] in ("behind", "diverged"):
            await _take_in(link, now, followed)
            now = await state(link, followed)
        if attempt and now["state"] == "same":      # the first push went through after all
            break
        if now["state"] in ("behind", "diverged"):
            raise GitError("Overleaf has commits this folder lacks. Press git pull first.")
        if now["state"] == "same":
            raise GitError("this folder has no commit Overleaf lacks")
        _, records = await _git(link.folder, "ls-files", "--", RECORDS)
        if records.strip():
            raise GitError("The commits hold .tex-mcp-web/, the review page's own records with its comments. "
                           "Untrack it with git rm -r --cached .tex-mcp-web and commit, then press git push.")
        if followed is not None and (older := await _not_overleafs(link, now["branch"], followed)):
            raise GitError(f"The commits hold text for {', '.join(older)} that is not Overleaf's now. "
                           "Commit those files as they are, then press git push.")
        code, _ = await _git(link.folder, "push", "--quiet", link.remote,
                             f"HEAD:{now['branch'].removeprefix(link.remote + '/')}", check=attempt > 0 or followed is None)
        if code == 0:
            break
    await fetch(link)
    return await state(link, followed or ())


async def holds(link: Link, revision: str, path: str, text: str) -> bool:
    """Whether the file at *path*, relative to the folder, holds *text* at *revision*."""
    code, out = await _git(link.folder, "show", f"{revision}:./{path}", check=False)
    return code == 0 and text in out
