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
    with pytest.raises(overleaf_git.GitError, match="Pull first"):
        await overleaf_git.push(link)


async def test_a_token_in_the_remote_address_never_reaches_a_message(sides) -> None:
    paper, _ = sides
    git(paper, "remote", "set-url", "overleaf", f"https://someone:secret-token@127.0.0.1:9/{PROJECT}")
    link = await overleaf_git.link_of(paper, PROJECT)
    with pytest.raises(overleaf_git.GitError) as raised:
        await overleaf_git.fetch(link)
    assert "secret-token" not in str(raised.value)


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
        # Words Overleaf has and this folder lacks are nowhere to put a thread.
        refused = await client.post("/overleaf/comments")
        assert refused.status == 409 and "Pull first" in (await refused.json())["error"]
        refused = await client.post("/overleaf/git/push")
        assert refused.status == 409 and "Pull first" in (await refused.json())["error"]
        now = await (await client.post("/overleaf/git/pull")).json()
        assert now["state"] == "same" and (paper / "paper.tex").read_text() == "ONE\ntwo\nthree\n"
        # Past the gate, the comments are read from Overleaf, which is not there in this test.
        assert (await client.post("/overleaf/comments")).status == 502
        # A git clone follows Overleaf by pull and push, never by keeping the files the same too.
        refused = await client.post("/overleaf/connect", json={"sync": True})
        assert refused.status == 400 and "by git" in (await refused.json())["error"]
    finally:
        await client.close()
