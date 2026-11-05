"""Completion lists read from the paper's sources: labels, bib entries, commands and
environments the paper defines."""

from tex_mcp_web.completion import bib_entries, defined_commands, defined_environments, labels

TEX = r"""\section{Intro}\label{sec:intro}
% \label{sec:commented}
Some words. \label{eq:one} and 50\% \label{fig:after-percent}
\begin{figure}
  \caption{A figure.}\label{ fig:spaced }
\end{figure}
"""


def test_labels_carry_their_line_and_skip_comments():
    assert labels(TEX, "tex/a.tex") == [
        {"name": "sec:intro", "file": "tex/a.tex", "line": 1},
        {"name": "eq:one", "file": "tex/a.tex", "line": 3},
        {"name": "fig:after-percent", "file": "tex/a.tex", "line": 3},
        {"name": "fig:spaced", "file": "tex/a.tex", "line": 5},
    ]


BIB = r"""@string{dac = "Design Automation Conference"}
@comment{ not an entry }
@inproceedings{kim2023fusion,
  author    = {Kim, Ji-Hoon and Lee, Su and Park, Min},
  title     = {{Fusion} Cells for {Standard-Cell} Placement},
  booktitle = dac,
  year      = 2023,
}

@article{ solo24 ,
  title = "A Single \"Author\" Study",
  author = "Doe, Jane",
  year = {2024}
}
@misc{bare, note = {no title}}
"""


def test_bib_entries_read_key_title_first_author_and_year():
    assert bib_entries(BIB, "main.bib") == [
        {"key": "kim2023fusion", "title": "Fusion Cells for Standard-Cell Placement",
         "author": "Kim et al.", "year": "2023", "file": "main.bib", "line": 3},
        {"key": "solo24", "title": 'A Single "Author" Study', "author": "Doe",
         "year": "2024", "file": "main.bib", "line": 10},
        {"key": "bare", "title": "", "author": "", "year": "", "file": "main.bib", "line": 15},
    ]


def test_an_author_written_first_name_first_gives_the_last_name():
    entries = bib_entries("@book{k, author = {Ada Lovelace and Charles Babbage}}", "a.bib")
    assert entries[0]["author"] == "Lovelace et al."


def test_commands_and_environments_the_paper_defines():
    source = r"""\newcommand{\red}[1]{{\color{red}#1}}
\renewcommand\figref[2][Fig.]{#1~\ref{#2}}
\newcommand{\etal}{et al.}
\DeclareMathOperator{\argmin}{arg\,min}
% \newcommand{\hidden}{x}
\def\cpp{CPP}
\newenvironment{proofsketch}{\textit{Sketch.}}{}
\newtheorem{lemma}{Lemma}
"""
    assert defined_commands(source) == [
        {"name": "red", "args": 1},
        {"name": "figref", "args": 1},
        {"name": "etal", "args": 0},
        {"name": "argmin", "args": 0},
        {"name": "cpp", "args": 0},
    ]
    assert defined_environments(source) == ["proofsketch", "lemma"]
