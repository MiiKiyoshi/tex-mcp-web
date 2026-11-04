import json
from pathlib import Path

import pytest

from tex_mcp_web.translate import (
    Paragraph,
    TranslationStore,
    Translator,
    build_request,
    load_glossary,
    paragraph_key,
    parse_reply,
    split_paragraphs,
    store_name,
    system_prompt,
)

SOURCE = """% ====
% A plan kept in a comment: never translated.
% ====

First paragraph has some words
that continue here. % inline note
\\begin{figure}[t]
\\end{figure}

\\label{sec:x}

Second paragraph with 50\\% savings and more words.
"""


def test_split_paragraphs_drops_comments_and_command_only_blocks():
    paragraphs = split_paragraphs(SOURCE, "tex/a.tex")
    assert [(p.line_start, p.line_end) for p in paragraphs] == [(5, 6), (12, 12)]
    assert "inline note" not in paragraphs[0].text
    assert "plan" not in paragraphs[0].text
    assert "50\\% savings" in paragraphs[1].text


def test_a_float_gives_only_its_caption_at_the_caption_lines():
    source = """Words before the float stay here.
\\begin{figure}[t]
    \\centering
    \\begin{tikzpicture}
        \\node at (0, 0) {left side: power in both rails};

        \\draw (0, 0) -- (1, 1) node {right side of the drawing};
    \\end{tikzpicture}
    \\caption[Short]{Euler paths of a {\\it NAND2} gate,
    % a note kept in a comment
    with more of the caption here.}
    \\label{fig:euler}
\\end{figure}
Words after the float stay here.

\\begin{table*}[t]
    \\caption{Results of the three benchmark runs.}
    \\begin{tabular}{l|c}
    words in a cell of the table & 12 \\\\
    \\end{tabular}
\\end{table*}
"""
    paragraphs = split_paragraphs(source, "a.tex")
    assert [(p.line_start, p.line_end) for p in paragraphs] == [(1, 1), (9, 11), (14, 14), (17, 17)]
    assert paragraphs[1].text == (
        "Euler paths of a {\\it NAND2} gate,\n    with more of the caption here.")
    assert paragraphs[3].text == "Results of the three benchmark runs."


def test_a_commented_float_marker_or_caption_is_not_one():
    source = """Some words of prose here.
% \\begin{figure}
More words of prose here.
% \\end{figure}

\\begin{figure}
% \\caption{An old caption kept in a comment.}
\\node {three words here};
\\end{figure}
"""
    paragraphs = split_paragraphs(source, "a.tex")
    assert [(p.line_start, p.line_end) for p in paragraphs] == [(1, 3)]


def test_an_unclosed_float_or_caption_keeps_the_words_around_it():
    assert [p.text for p in split_paragraphs("\\begin{figure}\nThree more words.\n", "a.tex")] == [
        "\\begin{figure}\nThree more words."]
    assert split_paragraphs("\\begin{figure}\n\\caption{Three more words.\n\\end{figure}\n", "a.tex") == []


def test_key_ignores_reflowed_whitespace():
    assert paragraph_key("a  b\nc") == paragraph_key("a b c")
    assert paragraph_key("a b c") != paragraph_key("a b d")


def test_build_request_and_parse_reply_round_trip():
    paragraphs = [Paragraph("f", 1, 1, "One two three."), Paragraph("f", 3, 3, "Four five six.")]
    prompt, ids = build_request(paragraphs, ["fusion cell"])
    assert ids == ["p1", "p2"]
    assert "fusion cell" in prompt and json.dumps("One two three.") in prompt
    reply = 'Here: {"t": {"p1": "하나 둘 셋.", "p2": " ", "p9": "x"}}'
    assert parse_reply(reply, ids) == {"p1": "하나 둘 셋."}
    with pytest.raises(ValueError):
        parse_reply("no json", ids)


def test_load_glossary_reads_term_names(tmp_path: Path):
    terms = tmp_path / "terms.md"
    terms.write_text("# Terms\n\n- standard cell (16)\n- runtime (15), also run time\n- diffusion break (6)\n")
    assert load_glossary(terms) == ["standard cell", "runtime", "diffusion break"]
    assert load_glossary(tmp_path / "missing.md") == []


def test_only_changed_paragraphs_are_sent_again(tmp_path: Path):
    calls = []

    def runner(prompt, cwd):
        blocks = json.loads(prompt.split("Paragraphs (JSON):\n", 1)[1])
        calls.append(sorted(blocks.values()))
        return json.dumps({"t": {pid: "KO:" + text for pid, text in blocks.items()}})

    store = TranslationStore(tmp_path / "translations.json")
    translator = Translator(store, [], tmp_path, runner=runner)
    first = split_paragraphs("Alpha beta gamma.\n\nDelta epsilon zeta.\n", "a.tex")
    translator.translate(first)
    assert calls == [["Alpha beta gamma.", "Delta epsilon zeta."]]

    edited = split_paragraphs("Alpha beta gamma.\n\nDelta epsilon eta.\n", "a.tex")
    translator.translate(edited)
    assert calls[-1] == ["Delta epsilon eta."]
    assert len(calls) == 2

    translator.translate(edited)          # nothing new: no call
    assert len(calls) == 2
    reloaded = TranslationStore(tmp_path / "translations.json")
    assert reloaded.get(edited[1].key) == "KO:Delta epsilon eta."


def test_failed_call_records_error_and_retries_later(tmp_path: Path):
    attempts = []

    def runner(prompt, cwd):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("login expired")
        return json.dumps({"t": {"p1": "번역"}})

    translator = Translator(TranslationStore(tmp_path / "t.json"), [], tmp_path, runner=runner)
    paragraphs = split_paragraphs("Some words here.\n", "a.tex")
    translator.translate(paragraphs)
    assert translator.error == "login expired"
    assert translator.missing(paragraphs) == paragraphs
    translator.translate(paragraphs)
    assert translator.error is None and translator.missing(paragraphs) == []


def test_comment_line_inside_a_paragraph_does_not_split_it():
    paragraphs = split_paragraphs("Alpha beta gamma\n% a note\ndelta epsilon zeta.\n", "a.tex")
    assert [(p.line_start, p.line_end) for p in paragraphs] == [(1, 3)]
    assert paragraphs[0].text == "Alpha beta gamma\ndelta epsilon zeta."


def test_system_prompt_names_the_language_and_adds_instructions():
    prompt = system_prompt("Korean", "Keep cell names in English.\n\n  Write plainly. ")
    assert "into Korean" in prompt and "academic Korean" in prompt
    assert "- Keep cell names in English.\n- Write plainly.\n" in prompt
    assert prompt.endswith('{"t": {"<id>": "<translation>", ...}} covering every id.')
    assert "cell names" not in system_prompt("Korean")


def test_store_name_per_language():
    assert store_name("Korean") == "translations-korean.json"
    assert store_name("Brazilian Portuguese") == "translations-brazilian-portuguese.json"
    hashed = store_name("한국어")
    assert hashed.startswith("translations-") and hashed != store_name("日本語")

