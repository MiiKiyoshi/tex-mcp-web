"""The list of terms the reference papers use."""

import os
from pathlib import Path

from tex_mcp_web.config import load_config
from tex_mcp_web.terms import build, refresh

SHARED_LINE = "Accepted to the Example Workshop on sky surveys and planets.\n"
PAPERS = {
    "a/a.md": "# Finding Planets\n\nA. Author, Example University\n\n## Abstract\n\n"
              "We study the radial velocity of nearby stars. The point spread function (PSF) limits "
              "what a small telescope resolves. Our detection rate is high.\n\n" + SHARED_LINE +
              "\n## Method\n\nThe radial velocity curve is fit with a sine. Each light curve is cleaned "
              "before the fit. The companion star is ignored.\n\n## References\n\n"
              "[1] B. Person. Sky surveys of a large telescope, 2019.\n",
    "b/b.md": "## Abstract\n\nWe model the point spread function (PSF) of a survey camera. A radial "
              "velocity survey confirms each candidate, and the detection rate rises with exposure.\n\n"
              + SHARED_LINE + "\n## Results\n\nEach light curve shows a dip. The detection method is simple.\n",
    "c/c.md": "## Abstract\n\nThe rate of detection depends on the point spread function. We measure radial "
              "velocity shifts in light curves of bright stars.\n\n" + SHARED_LINE,
    "d/d.md": "## Abstract\n\nWe describe a calibration lamp for spectrographs. Each light curve of the lamp "
              "is flat, so the detection rate of our pipeline stays fixed.\n",
    "e/e.md": "## Abstract\n\nA higher rate of detection needs a sharper point spread function.\n",
}


def references(root: Path) -> Path:
    for name, text in PAPERS.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    # The same paper again under another name counts once.
    (root / "c" / "c copy.md").write_text(PAPERS["c/c.md"], encoding="utf-8")
    return root


def test_the_list_holds_the_terms_several_papers_use(tmp_path: Path) -> None:
    text = build(references(tmp_path / "refs"))
    lines = text.splitlines()
    assert lines[0] == "# Terms the reference papers use"
    assert "From 5 papers" in text
    # "A of B" and "B A" are one term, named as more papers write it, counted over both.
    assert "- detection rate (5), also rate of detection" in lines
    # An acronym two papers define is attached. Its parts are not terms of their own.
    assert "- point spread function, PSF (4)" in lines
    assert not any(line.startswith(("- point spread (", "- spread function (")) for line in lines)
    assert "- light curve (4)" in lines
    assert "- radial velocity (3)" in lines
    # One paper's phrase, a phrase ending in a generic noun, a line every paper carries and
    # the reference list are not terms.
    assert "companion star" not in text
    assert "detection method" not in text
    assert "sky survey" not in text
    # Ranked by how many papers use a term.
    assert lines.index("- detection rate (5), also rate of detection") < lines.index("- radial velocity (3)")


def project(root: Path, refs: str | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    config = root / ".tex-mcp-web.yaml"
    config.write_text("main: main.tex\n" + (f"references: {refs}\n" if refs else ""), encoding="utf-8")
    return config


def test_the_list_is_rebuilt_only_when_the_papers_change(tmp_path: Path) -> None:
    refs = references(tmp_path / "refs")
    config = project(tmp_path / "paper", "../refs")
    terms = refresh(load_config(config))
    assert terms == tmp_path / "paper" / ".tex-mcp-web" / "terms.md"
    assert "- light curve (4)" in terms.read_text(encoding="utf-8")
    # Unchanged papers leave the list as it was, so a reader is not sent back to it.
    before = terms.stat().st_mtime_ns
    os.utime(terms, ns=(before - 10**9, before - 10**9))
    assert refresh(load_config(config)) == terms
    assert terms.stat().st_mtime_ns == before - 10**9
    # A changed paper rebuilds it.
    (refs / "d" / "d.md").write_text(PAPERS["d/d.md"].replace("light curve", "lamp spectrum"), encoding="utf-8")
    refresh(load_config(config))
    assert "- light curve (3)" in terms.read_text(encoding="utf-8")
    # Without references the list goes.
    config.write_text("main: main.tex\n", encoding="utf-8")
    assert refresh(load_config(config)) is None
    assert not terms.exists()


def test_a_folder_that_cannot_be_read_gives_a_list_saying_so(tmp_path: Path) -> None:
    config = project(tmp_path / "paper", "missing")
    text = refresh(load_config(config)).read_text(encoding="utf-8")
    assert "could not be built" in text and "missing" in text
    (tmp_path / "paper" / "missing").mkdir()
    text = refresh(load_config(config)).read_text(encoding="utf-8")
    assert "no Markdown files" in text


async def test_guide_brings_the_list_up_to_date_and_names_it_to_read(tmp_path: Path) -> None:
    import json
    import socket

    from tex_mcp_web.mcp_client import ProjectBinding
    from tex_mcp_web.mcp_server import create_server

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    references(tmp_path / "refs")
    paper = tmp_path / "paper"
    paper.mkdir()
    (paper / "main.tex").write_text("\\documentclass{article}\n\\begin{document}x\\end{document}\n")
    config = paper / ".tex-mcp-web.yaml"
    config.write_text(f"main: main.tex\nport: {port}\nreferences: ../refs\n")
    binding = ProjectBinding(paper)
    mcp = create_server(binding)

    async def guide():
        return json.loads((await mcp.call_tool("guide", {}))[0][0].text)

    try:
        terms = (paper / ".tex-mcp-web" / "terms.md").resolve()
        assert await guide() == {"read": [str(terms)]}
        assert "- light curve (4)" in terms.read_text(encoding="utf-8")
        # Without references there is nothing to read first.
        config.write_text(f"main: main.tex\nport: {port}\n")
        assert await guide() == {"read": []}
    finally:
        binding.stop()
