from hypothesis import given, settings
from hypothesis import strategies as st

from cardcutter.tokenize import (
    apply_partial,
    char_mask_from_ranges,
    indexed_view,
    rebuild,
    sentence_starts,
    tokenize,
    word_ranges_from_mask,
)
from tests.strategies import texts


@given(texts)
@settings(max_examples=500)
def test_round_trip_is_exact(text):
    assert rebuild(text, tokenize(text)) == text


@given(texts)
def test_tokens_are_stable_and_cover_all_visible_chars(text):
    toks = tokenize(text)
    assert [t.index for t in toks] == list(range(len(toks)))
    covered = set()
    for t in toks:
        assert text[t.start:t.end] == t.text
        assert not any(ch.isspace() for ch in t.text)
        covered.update(range(t.start, t.end))
    visible = {i for i, ch in enumerate(text) if not ch.isspace()}
    assert covered == visible
    assert tokenize(text) == toks  # deterministic


def test_dashes_split_but_hyphens_do_not():
    words = [t.text for t in tokenize("Disputes are existential---only BBNJ solves; growth—and well-known risks")]
    assert words == ["Disputes", "are", "existential", "---", "only", "BBNJ", "solves;", "growth", "—", "and", "well-known", "risks"]


def test_special_spaces_and_soft_hyphens():
    text = "bio­diversity loss “quoted” it’s"
    words = [t.text for t in tokenize(text)]
    assert words == ["bio­diversity", "loss", "“quoted”", "it’s"]
    assert rebuild(text, tokenize(text)) == text


def test_indexed_view():
    assert indexed_view(tokenize("The ocean is big.")) == "0:The 1:ocean 2:is 3:big."


@given(texts, st.data())
def test_word_ranges_round_trip_through_char_mask(text, data):
    toks = tokenize(text)
    if not toks:
        return
    picks = sorted(set(data.draw(st.lists(st.integers(0, len(toks) - 1), max_size=10))))
    ranges = [(i, i) for i in picks]
    mask = char_mask_from_ranges(len(text), toks, ranges)
    merged = word_ranges_from_mask(mask, toks)
    expanded = {i for a, b in merged for i in range(a, b + 1)}
    assert expanded == set(picks)


def test_partial_word_selection():
    text = "biodiversity impact assessment"
    toks = tokenize(text)
    mask = [False] * len(text)
    apply_partial(mask, toks, 0, 0, 4)
    apply_partial(mask, toks, 1, 0, 1)
    apply_partial(mask, toks, 2, 0, 1)
    assert "".join(c for c, m in zip(text, mask) if m) == "biodia"


def test_sentence_starts():
    text = "First one. Second “quoted” one! third lower. Fourth.\nNew para"
    starts = sentence_starts(text)
    assert 0 in starts
    assert text.index("Second") in starts
    assert text.index("third") not in starts
    assert text.index("Fourth") in starts
    assert text.index("New para") in starts
