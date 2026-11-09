"""The paper's folder compared with its Overleaf project by commit, with a bare repository
on disk standing in for Overleaf's."""

import subprocess
from pathlib import Path

import pytest

from tex_mcp_web import overleaf_git

PROJECT = "0123456789abcdef01234567"


def git(folder: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True, text=True).stdout


def commit(folder: Path, name: str, text: str, message: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")
    git(folder, "add", name)
    git(folder, "commit", "-q", "-m", message)


@pytest.fixture
def sides(tmp_path: Path, monkeypatch):
    """The paper's folder, cloned from Overleaf, and a co-author's clone that commits on
    Overleaf's side."""
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "Someone")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "someone@example.com")
    overleaf = tmp_path / "git" / PROJECT
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(overleaf)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(overleaf), str(seed)], check=True, capture_output=True)
    commit(seed, "paper.tex", "one\ntwo\nthree\n", "first")
    git(seed, "push", "-q", "origin", "HEAD:main")
    paper = tmp_path / "paper"
    subprocess.run(["git", "clone", "-q", "-o", "overleaf", str(overleaf), str(paper)], check=True)
    return paper, seed


async def test_the_remote_whose_address_ends_in_the_project_is_overleaf(sides, tmp_path: Path) -> None:
    paper, _ = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    assert link == overleaf_git.Link(paper, "overleaf")
    assert await overleaf_git.link_of(paper, "f" * 24) is None
    (tmp_path / "plain").mkdir()
    assert await overleaf_git.link_of(tmp_path / "plain", PROJECT) is None


async def test_the_state_counts_commits_each_side_lacks_and_names_uncommitted_files(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    await overleaf_git.fetch(link)
    now = await overleaf_git.state(link)
    assert (now["state"], now["ahead"], now["behind"], now["uncommitted"]) == ("same", 0, 0, [])
    assert now["here"] == now["there"] and now["branch"] == "overleaf/main"

    commit(paper, "paper.tex", "one\ntwo\nthree!\n", "here")
    (paper / "paper.tex").write_text("one\ntwo\nthree!!\n", encoding="utf-8")
    now = await overleaf_git.state(link)
    assert (now["state"], now["ahead"], now["uncommitted"]) == ("ahead", 1, ["paper.tex"])

    commit(coauthor, "notes.tex", "a note\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    # What Overleaf has is known only once it is fetched.
    assert (await overleaf_git.state(link))["state"] == "ahead"
    await overleaf_git.fetch(link)
    now = await overleaf_git.state(link)
    assert (now["state"], now["ahead"], now["behind"]) == ("diverged", 1, 1)


async def test_pull_fast_forwards_and_refuses_to_write_over_an_uncommitted_change(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    with pytest.raises(overleaf_git.GitError, match="no commit this folder lacks"):
        await overleaf_git.pull(link)
    commit(coauthor, "paper.tex", "ONE\ntwo\nthree\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "paper.tex").write_text("one\ntwo\nthree, edited here\n", encoding="utf-8")
    with pytest.raises(overleaf_git.GitError, match="paper.tex"):
        await overleaf_git.pull(link)
    assert (paper / "paper.tex").read_text() == "one\ntwo\nthree, edited here\n"
    git(paper, "commit", "-q", "-am", "here")
    # Both sides now have a commit, apart in the file: git merges them.
    now = await overleaf_git.pull(link)
    assert (now["state"], now["ahead"]) == ("ahead", 2)
    assert (paper / "paper.tex").read_text() == "ONE\ntwo\nthree, edited here\n"


async def test_pull_leaves_a_conflict_to_be_merged_by_hand_and_changes_nothing(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(coauthor, "paper.tex", "one\nTWO there\nthree\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    commit(paper, "paper.tex", "one\nTWO here\nthree\n", "here")
    before = git(paper, "rev-parse", "HEAD")
    with pytest.raises(overleaf_git.GitError, match="same lines in paper.tex"):
        await overleaf_git.pull(link)
    assert git(paper, "rev-parse", "HEAD") == before
    assert (paper / "paper.tex").read_text() == "one\nTWO here\nthree\n"
    assert not (paper / ".git" / "MERGE_HEAD").exists()


async def test_push_sends_commits_only_when_overleaf_has_nothing_this_lacks(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    with pytest.raises(overleaf_git.GitError, match="no commit Overleaf lacks"):
        await overleaf_git.push(link)
    commit(paper, "paper.tex", "one\ntwo\nthree!\n", "here")
    (paper / "paper.tex").write_text("one\ntwo\nnot committed\n", encoding="utf-8")
    now = await overleaf_git.push(link)
    assert (now["state"], now["uncommitted"]) == ("same", ["paper.tex"])
    assert await overleaf_git.holds(link, now["branch"], "paper.tex", "three!")
    assert not await overleaf_git.holds(link, now["branch"], "paper.tex", "not committed")

    git(coauthor, "pull", "-q", "origin", "main")
    commit(coauthor, "paper.tex", "one\ntwo\nthree!?\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "paper.tex").write_text("one\ntwo\nthree!\n", encoding="utf-8")
    commit(paper, "notes.tex", "here\n", "here again")
    with pytest.raises(overleaf_git.GitError, match="git pull first"):
        await overleaf_git.push(link)


async def test_a_token_in_the_remote_address_never_reaches_a_message(sides) -> None:
    paper, _ = sides
    git(paper, "remote", "set-url", "overleaf", f"https://someone:secret-token@127.0.0.1:9/{PROJECT}")
    link = await overleaf_git.link_of(paper, PROJECT)
    with pytest.raises(overleaf_git.GitError) as raised:
        await overleaf_git.fetch(link)
    assert "secret-token" not in str(raised.value)


# ----- With the files also kept the same with Overleaf as they change -----

def figure(folder: Path, name: str, data: bytes, message: str) -> None:
    (folder / name).write_bytes(data)
    git(folder, "add", name)
    git(folder, "commit", "-q", "-m", message)


async def test_with_files_followed_a_pull_stages_overleafs_text_and_brings_the_rest(sides) -> None:
    """A file kept the same with Overleaf holds the text of Overleaf's commit already, which
    git alone refuses to write over. Taken as it is, the pull goes on, and a figure added on
    Overleaf comes in with it."""
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(coauthor, "paper.tex", "ONE\ntwo\nthree\n", "there")
    figure(coauthor, "fig.png", b"\x89PNG figure", "figure")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "paper.tex").write_text("ONE\ntwo\nthree\n", encoding="utf-8")
    with pytest.raises(overleaf_git.GitError):
        await overleaf_git.pull(link)
    now = await overleaf_git.pull(link, ["paper.tex"])
    assert now["state"] == "same" and now["uncommitted"] == []
    assert (paper / "fig.png").read_bytes() == b"\x89PNG figure"
    assert git(paper, "status", "--porcelain") == ""


async def test_a_followed_file_edited_since_overleafs_commit_stops_the_pull(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(coauthor, "paper.tex", "ONE\ntwo\nthree\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "paper.tex").write_text("ONE\ntwo\nthree\nfour\n", encoding="utf-8")
    head = git(paper, "rev-parse", "HEAD")
    with pytest.raises(overleaf_git.GitError, match="changed after Overleaf's commit was read"):
        await overleaf_git.pull(link, ["paper.tex"])
    assert git(paper, "rev-parse", "HEAD") == head
    assert (paper / "paper.tex").read_text(encoding="utf-8") == "ONE\ntwo\nthree\nfour\n"


async def test_a_file_new_on_overleaf_and_written_here_live_is_taken_as_it_is(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(coauthor, "new.tex", "new\n", "new on Overleaf")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "new.tex").write_text("new\n", encoding="utf-8")
    now = await overleaf_git.pull(link, ["paper.tex", "new.tex"])
    assert now["state"] == "same" and git(paper, "status", "--porcelain") == ""


async def test_with_files_followed_push_sends_overleafs_text_and_this_folders_figure(sides) -> None:
    """The folder's own commits hold text that went to Overleaf live and was written over
    there since. git alone sees two changes to one line. The merge takes Overleaf's text for
    the followed file and the folder's figure, so the push sends the figure and leaves
    Overleaf's text as it is."""
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(paper, "paper.tex", "one\nTWO\nthree\n", "here, sent live at the time")
    figure(paper, "fig.png", b"figure", "figure")
    commit(coauthor, "paper.tex", "one\nTWO, said again\nthree\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "paper.tex").write_text("one\nTWO, said again\nthree\n", encoding="utf-8")
    now = await overleaf_git.push(link, ["paper.tex"])
    assert now["state"] == "same" and git(paper, "status", "--porcelain") == ""
    assert len(git(paper, "log", "-1", "--format=%P").split()) == 2
    git(coauthor, "pull", "-q", "--ff-only", "origin", "main")
    assert (coauthor / "paper.tex").read_text(encoding="utf-8") == "one\nTWO, said again\nthree\n"
    assert (coauthor / "fig.png").read_bytes() == b"figure"


async def test_a_figure_changed_on_both_sides_stops_the_push(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    figure(paper, "fig.png", b"here", "figure here")
    figure(coauthor, "fig.png", b"there", "figure there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    head = git(paper, "rev-parse", "HEAD")
    with pytest.raises(overleaf_git.GitError, match="changed fig.png differently"):
        await overleaf_git.push(link, ["paper.tex"])
    assert git(paper, "rev-parse", "HEAD") == head and (paper / "fig.png").read_bytes() == b"here"


async def test_following_takes_overleafs_commits_in_only_when_it_moves_forward(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    figure(coauthor, "fig.png", b"figure", "figure")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "paper.tex").write_text("one\ntwo\nthree\nlive\n", encoding="utf-8")
    now = await overleaf_git.follow(link, ["paper.tex"])
    assert now["state"] == "same" and (paper / "fig.png").exists()
    # Changed here live, the file is on Overleaf as it is here, so it is not named.
    assert now["uncommitted"] == [] and (await overleaf_git.state(link))["uncommitted"] == ["paper.tex"]
    # With commits on both sides, it is left for Pull.
    commit(paper, "notes.tex", "here\n", "here")
    commit(coauthor, "more.tex", "there\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    now = await overleaf_git.follow(link, ["paper.tex"])
    assert now["state"] == "diverged" and not (paper / "more.tex").exists()


async def test_files_named_in_korean_are_kept_through_the_merge(sides) -> None:
    """git quotes such names unless told not to; a figure named so is kept in the merge, and
    a followed file named so is taken as Live wrote it."""
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    figure(paper, "그림.png", b"figure", "figure")
    commit(coauthor, "본문.tex", "본문\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "본문.tex").write_text("본문\n", encoding="utf-8")
    now = await overleaf_git.pull(link, ["paper.tex", "본문.tex"])
    assert now["state"] == "ahead" and (paper / "그림.png").read_bytes() == b"figure"
    assert "그림.png" in git(paper, "ls-tree", "--name-only", "-z", "HEAD").split("\0")
    assert git(paper, "status", "--porcelain") == ""


async def test_a_refused_pull_leaves_the_index_as_it_was(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(paper, "notes.txt", "notes\n", "notes")
    git(paper, "push", "-q", "overleaf", "HEAD:main")
    git(coauthor, "pull", "-q", "origin", "main")
    commit(coauthor, "paper.tex", "ONE\ntwo\nthree\n", "there")
    commit(coauthor, "notes.txt", "notes, there\n", "there too")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "paper.tex").write_text("ONE\ntwo\nthree\n", encoding="utf-8")
    (paper / "notes.txt").write_text("notes, here\n", encoding="utf-8")
    with pytest.raises(overleaf_git.GitError):
        await overleaf_git.pull(link, ["paper.tex"])
    assert git(paper, "diff", "--cached", "--name-only") == ""
    now = await overleaf_git.follow(link, ["paper.tex"])
    assert now["state"] == "behind" and git(paper, "diff", "--cached", "--name-only") == ""


async def test_a_followed_file_this_folder_ignores_is_taken_all_the_same(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    (paper / ".git" / "info" / "exclude").write_text("*.bbl\n", encoding="utf-8")
    commit(coauthor, "main.bbl", "bibliography\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    (paper / "main.bbl").write_text("bibliography\n", encoding="utf-8")
    now = await overleaf_git.pull(link, ["paper.tex", "main.bbl"])
    assert now["state"] == "same"


async def test_following_leaves_a_file_git_does_not_track_for_pull(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    (paper / ".git" / "info" / "exclude").write_text("*.pdf\n", encoding="utf-8")
    (paper / "plot.pdf").write_bytes(b"mine")
    figure(coauthor, "plot.pdf", b"theirs", "plot")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    now = await overleaf_git.follow(link, ["paper.tex"])
    assert now["state"] == "behind" and (paper / "plot.pdf").read_bytes() == b"mine"


async def test_a_commit_with_text_overleaf_has_moved_past_is_not_pushed(sides) -> None:
    """Live keeps the file as Overleaf has it, and a commit that holds something else for it
    would put that over Overleaf's text."""
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(paper, "paper.tex", "one\nolder\nthree\n", "here")
    (paper / "paper.tex").write_text("one\nnow\nthree\n", encoding="utf-8")
    on_overleaf = git(coauthor, "ls-remote", "origin", "main")
    with pytest.raises(overleaf_git.GitError, match="not Overleaf's now"):
        await overleaf_git.push(link, ["paper.tex"])
    assert git(coauthor, "ls-remote", "origin", "main") == on_overleaf


async def test_a_rename_not_yet_staged_is_named_once(sides) -> None:
    paper, _ = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(paper, "a b.tex", "words\n", "spaced")
    git(paper, "mv", "a b.tex", "c d.tex")
    git(paper, "commit", "-q", "-m", "renamed")
    (paper / "c d.tex").rename(paper / "e.tex")
    git(paper, "add", "-N", "e.tex")
    assert (await overleaf_git.state(link))["uncommitted"] == ["e.tex"]


async def test_a_merge_left_unfinished_stops_the_pull_and_keeps_its_conflicts(sides) -> None:
    paper, coauthor = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    commit(paper, "paper.tex", "one\nTWO here\nthree\n", "here")
    commit(coauthor, "paper.tex", "one\nTWO there\nthree\n", "there")
    git(coauthor, "push", "-q", "origin", "HEAD:main")
    git(paper, "fetch", "-q", "overleaf")
    subprocess.run(["git", "-C", str(paper), "merge", "-q", "overleaf/main"], capture_output=True)
    conflicts = git(paper, "ls-files", "-u")
    assert conflicts
    (paper / "paper.tex").write_text("one\nTWO there\nthree\n", encoding="utf-8")
    with pytest.raises(overleaf_git.GitError, match="not finished"):
        await overleaf_git.pull(link, ["paper.tex"])
    assert git(paper, "ls-files", "-u") == conflicts


async def test_live_is_refused_in_a_folder_inside_a_git_clone(sides, monkeypatch) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from tex_mcp_web import overleaf
    from tex_mcp_web.config import load_config
    from tex_mcp_web.server import TexMcpWebServer

    paper, _ = sides
    monkeypatch.setenv(overleaf.COOKIE_ENV, "s%3Asigned-in")
    inner = paper / "sub"
    inner.mkdir()
    (inner / "paper.tex").write_text("inner\n", encoding="utf-8")
    (inner / ".tex-mcp-web.yaml").write_text(
        f"main: paper.tex\noverleaf: http://127.0.0.1:9/project/{PROJECT}\n", encoding="utf-8")
    server = TexMcpWebServer(load_config(inner / ".tex-mcp-web.yaml"))
    client = TestClient(TestServer(server.app))
    await client.start_server()
    try:
        refused = await client.post("/overleaf/connect", json={"sync": True})
        assert refused.status == 400 and "sub inside a git clone" in (await refused.json())["error"]
    finally:
        await client.close()


async def test_the_page_pulls_pushes_and_downloads_comments_only_with_every_overleaf_commit(
        sides, tmp_path: Path, monkeypatch) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from tex_mcp_web import overleaf
    from tex_mcp_web.config import load_config
    from tex_mcp_web.server import TexMcpWebServer

    paper, coauthor = sides
    monkeypatch.setenv(overleaf.COOKIE_ENV, "s%3Asigned-in")
    (paper / ".tex-mcp-web.yaml").write_text(
        f"main: paper.tex\noverleaf: http://127.0.0.1:9/project/{PROJECT}\n", encoding="utf-8")
    server = TexMcpWebServer(load_config(paper / ".tex-mcp-web.yaml"))
    client = TestClient(TestServer(server.app))
    await client.start_server()
    try:
        assert (await (await client.get("/overleaf")).json())["git"] == "overleaf"
        commit(coauthor, "paper.tex", "ONE\ntwo\nthree\n", "there")
        git(coauthor, "push", "-q", "origin", "HEAD:main")
        now = await (await client.post("/overleaf/git/fetch")).json()
        assert (now["state"], now["behind"]) == ("behind", 1)
        # The agent reads the same in few words: only what differs, from the last reading.
        assert await (await client.get("/overleaf/upstream")).json() == {"by": "git", "git": True, "behind": 1}
        # Words Overleaf has and this folder lacks are nowhere to put a thread.
        refused = await client.post("/overleaf/comments")
        assert refused.status == 409 and "git pull first" in (await refused.json())["error"]
        refused = await client.post("/overleaf/git/push")
        assert refused.status == 409 and "git pull first" in (await refused.json())["error"]
        now = await (await client.post("/overleaf/git/pull")).json()
        assert now["state"] == "same" and (paper / "paper.tex").read_text() == "ONE\ntwo\nthree\n"
        assert await (await client.post("/overleaf/upstream")).json() == {"by": "git", "git": True}
        # Past the gate, the comments are read from Overleaf, which is not there in this test.
        assert (await client.post("/overleaf/comments")).status == 502
        # Live may be chosen in a git clone as well: Pull and Push go on beside it.
        chosen = await client.post("/overleaf/connect", json={"sync": True})
        assert chosen.status == 200 and (await chosen.json())["sync"] is True
    finally:
        await client.close()


async def test_a_push_holding_the_review_pages_records_is_refused(sides) -> None:
    """The records beside the paper hold its comments and copies of its text: a clone keeps
    them out of a commit of every file, and a commit that holds them anyway is not pushed."""
    paper, _ = sides
    link = await overleaf_git.link_of(paper, PROJECT)
    await overleaf_git.keep_records_out(paper)
    await overleaf_git.keep_records_out(paper)
    exclude = paper / ".git" / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8").splitlines().count(".tex-mcp-web/") == 1
    (paper / ".tex-mcp-web").mkdir()
    (paper / ".tex-mcp-web" / "comments.json").write_text("{}", encoding="utf-8")
    git(paper, "add", "-A")
    assert ".tex-mcp-web/" not in git(paper, "status", "--porcelain")
    git(paper, "add", "-f", ".tex-mcp-web/comments.json")
    git(paper, "-c", "user.name=S", "-c", "user.email=s@example.com", "commit", "-q", "-m", "records")
    with pytest.raises(overleaf_git.GitError, match="hold .tex-mcp-web/"):
        await overleaf_git.push(link)


async def test_the_review_pages_records_stay_out_of_any_git_the_paper_is_in(tmp_path: Path) -> None:
    """A paper in a git repository of the reviewer's own, a backup with no Overleaf remote
    and the paper in a folder of it, follows Overleaf without git, and once the page starts
    a commit of every file leaves the page's records out. A folder in no git repository is
    left as it is."""
    import socket

    from tex_mcp_web.comments import PaperAnchor
    from tex_mcp_web.config import load_config
    from tex_mcp_web.server import TexMcpWebServer

    backup = tmp_path / "backup"
    subprocess.run(["git", "init", "-q", str(backup)], check=True)
    git(backup, "remote", "add", "origin", "https://example.com/someone/papers.git")
    paper = backup / "paper"
    paper.mkdir()
    (paper / "paper.tex").write_text("\\documentclass{article}\\begin{document}Hi.\\end{document}\n", encoding="utf-8")
    (paper / ".tex-mcp-web.yaml").write_text(
        f"main: paper.tex\noverleaf: http://127.0.0.1:9/project/{PROJECT}\n", encoding="utf-8")
    server = TexMcpWebServer(load_config(paper / ".tex-mcp-web.yaml"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    await server.setup(port)
    try:
        server.comments.add(PaperAnchor(), "A note.")
        assert await server._overleaf_git_link() is None
        status = git(backup, "status", "--porcelain", "--untracked-files=all")
        assert (paper / ".tex-mcp-web" / "comments.json").is_file()
        assert "paper/paper.tex" in status and ".tex-mcp-web/" not in status
    finally:
        await server.cleanup()

    alone = tmp_path / "alone"
    alone.mkdir()
    await overleaf_git.keep_records_out(alone)
    assert list(alone.iterdir()) == []
