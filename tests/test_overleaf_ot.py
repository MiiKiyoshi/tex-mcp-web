"""Edits to text as Overleaf's editor sends them: applied, brought together, and made
from two texts."""

import random

import pytest

from tex_mcp_web.overleaf_ot import (
    Mismatch, apply, comments_removed, diff, js_len, move_comments, nearest, py_index, text_only, transform_x,
)

ALPHABET = "ab \n가나😀\\{}"


def random_text(rng: random.Random, size: int) -> str:
    return "".join(rng.choice(ALPHABET) for _ in range(size))


def random_op(rng: random.Random, text: str, steps: int) -> list[dict]:
    """Steps made one after another, each on the text the last left."""
    op = []
    for _ in range(steps):
        boundaries = [js_len(text[:i]) for i in range(len(text) + 1)]
        if text and rng.random() < 0.45:
            start = rng.randrange(len(text))
            stop = rng.randint(start + 1, min(len(text), start + 6))
            step = {"p": boundaries[start], "d": text[start:stop]}
        else:
            step = {"p": rng.choice(boundaries), "i": random_text(rng, rng.randint(1, 4))}
        op.append(step)
        text = apply(text, [step])
    return op


def test_two_edits_made_at_once_end_in_the_same_text_either_way() -> None:
    rng = random.Random(7)
    for _ in range(4000):
        text = random_text(rng, rng.randint(0, 14))
        left, right = random_op(rng, text, rng.randint(1, 3)), random_op(rng, text, rng.randint(1, 3))
        moved_left, moved_right = transform_x(left, right)
        assert apply(apply(text, left), moved_right) == apply(apply(text, right), moved_left), (text, left, right)


def test_where_both_insert_at_one_place_the_left_ones_text_comes_first() -> None:
    left, right = [{"p": 1, "i": "L"}], [{"p": 1, "i": "R"}]
    moved_left, moved_right = transform_x(left, right)
    assert apply(apply("ab", left), moved_right) == "aLRb"
    assert apply(apply("ab", right), moved_left) == "aLRb"


def test_text_inserted_inside_text_another_deletes_stays() -> None:
    mine, theirs = [{"p": 2, "i": "X"}], [{"p": 1, "d": "bcd"}]
    moved_mine, moved_theirs = transform_x(mine, theirs)
    assert apply(apply("abcde", mine), moved_theirs) == "aXe" == apply(apply("abcde", theirs), moved_mine)


def test_an_edit_comes_from_two_texts_and_keeps_what_did_not_change() -> None:
    rng = random.Random(3)
    for _ in range(1500):
        old = random_text(rng, rng.randint(0, 30))
        new = apply(old, random_op(rng, old, rng.randint(0, 4)))
        assert apply(old, diff(old, new)) == new
    old = "First line.\nThe method is fast and small.\nLast line.\n"
    new = "First line.\nThe method is very fast and small.\nLast line.\n"
    assert diff(old, new) == [{"p": 26, "i": "very "}]


def test_a_comment_on_words_an_edit_keeps_is_kept() -> None:
    old = "Our placer is fast. It folds each cell twice.\n"
    new = "Our new placer is fast. It folds each cell once.\n"
    comment = {"t": "t1", "p": js_len(old[:old.index("fast")]), "n": 4}
    assert comments_removed([comment], diff(old, new)) == []
    moved = move_comments([comment], diff(old, new))[0]
    assert new[py_index(new, moved["p"]):][:moved["n"]] == "fast"
    rewritten = "A different sentence altogether.\n"
    assert comments_removed([comment], diff(old, rewritten)) == ["t1"]


def test_positions_count_as_javascript_does() -> None:
    text = "😀a"
    assert js_len(text) == 3 and py_index(text, 2) == 1
    assert apply(text, [{"p": 2, "i": "b"}]) == "😀ba"
    with pytest.raises(Mismatch):
        py_index(text, 1)          # inside the emoji


def test_a_delete_that_names_other_text_is_refused() -> None:
    with pytest.raises(Mismatch):
        apply("abc", [{"p": 0, "d": "x"}])


def test_comment_steps_change_no_text() -> None:
    assert text_only([{"p": 0, "c": "ab", "t": "t1"}, {"p": 1, "i": "x"}]) == [{"p": 1, "i": "x"}]
    assert move_comments([], [{"p": 1, "c": "b", "t": "t9"}]) == [{"t": "t9", "p": 1, "n": 1}]


def covered(text: str, comment: dict) -> str:
    return text[py_index(text, comment["p"]):py_index(text, comment["p"] + comment["n"])]


def test_words_that_replace_commented_words_take_the_comment() -> None:
    old = "Intro.\nTESTA\nTESTB\n"
    new = "Intro.\nThis is test comment\nTESTB\n"
    comment = {"t": "t1", "p": old.index("TESTA"), "n": 5}
    edit = diff(old, new, [comment])
    assert apply(old, edit) == new
    assert comments_removed([comment], edit) == []
    assert covered(new, move_comments([comment], edit)[0]) == "This is test comment"
    # without the comments the old order stays, and the comment keeps only the shared "T"
    assert covered(new, move_comments([comment], diff(old, new))[0]) == "T"


def test_a_comment_over_more_than_the_replaced_words_keeps_the_rest_too() -> None:
    old = "The placer is fast and small.\n"
    new = "The placer is quick and small.\n"
    comment = {"t": "t1", "p": old.index("fast"), "n": len("fast and small")}
    edit = diff(old, new, [comment])
    assert apply(old, edit) == new
    assert covered(new, move_comments([comment], edit)[0]) == "quick and small"
    first_word = {"t": "t2", "p": old.index("is fast"), "n": len("is fast")}
    assert covered(new, move_comments([first_word], diff(old, new, [first_word]))[0]) == "is quick"


def test_a_deleted_or_one_character_comment_still_counts_as_removed() -> None:
    old = "The placer is fast.\n"
    comment = {"t": "t1", "p": old.index("fast"), "n": 4}
    gone = old.replace(" is fast", "")
    assert comments_removed([comment], diff(old, gone, [comment])) == ["t1"]
    single = {"t": "t2", "p": old.index("f"), "n": 1}
    swapped = old.replace("fast", "slow")
    assert comments_removed([single], diff(old, swapped, [single])) == ["t2"]


def test_an_edit_made_around_comments_still_makes_the_new_text() -> None:
    rng = random.Random(5)
    for _ in range(3000):
        old = random_text(rng, rng.randint(1, 30))
        new = apply(old, random_op(rng, old, rng.randint(1, 4)))
        bounds = [js_len(old[:i]) for i in range(len(old) + 1)]
        comments = []
        for index in range(rng.randint(1, 3)):
            a, b = sorted(rng.sample(bounds, 2)) if len(bounds) > 1 else (0, 0)
            comments.append({"t": f"t{index}", "p": a, "n": b - a})
        edit = diff(old, new, comments)
        assert apply(old, edit) == new, (old, new, comments, edit)
        assert len(comments_removed(comments, edit)) <= len(comments_removed(comments, diff(old, new)))


def test_words_are_found_nearest_the_line_they_were_on() -> None:
    text = "a\nfoo\nb\nfoo\n😀foo"
    assert [nearest(text, "foo", line) for line in (1, 4, 5)] == [2, 8, 14]
    assert nearest(text, "bar", 1) is None and nearest(text, "", 1) is None


@pytest.mark.parametrize("theirs, words", [
    ([{"p": 0, "i": ">> "}], "brave"),
    ([{"p": 8, "i": "XX"}], "brXXave"),
    ([{"p": 11, "i": "!"}], "brave"),
    ([{"p": 4, "d": "o br"}], "ave"),
    ([{"p": 6, "d": "brave "}], ""),
    ([{"p": 0, "d": "Hello "}], "brave"),
])
def test_a_comment_sent_while_another_edit_lands_moves_with_the_text(theirs, words) -> None:
    """A comment changes no text: an edit made at once passes it unchanged, and the comment
    stays on its words as Overleaf keeps it, widened, narrowed or emptied with them."""
    base = "Hello brave world."
    mine, moved = transform_x([{"c": "brave", "p": 6, "t": "t1"}], theirs)
    assert moved == theirs
    after = apply(base, theirs)
    [step] = mine
    assert step["c"] == words and after[step["p"]:step["p"] + len(words)] == words
