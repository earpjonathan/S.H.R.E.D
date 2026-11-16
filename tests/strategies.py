"""Hypothesis strategies shared by the tests: tricky text and random formatting."""
from __future__ import annotations

from hypothesis import strategies as st

from cardcutter.card import Card, FText

# Characters that break naive text handling in Word documents.
TRICKY = "’‘“”—–- ­‑\t …§éñ"
ALPHABET = st.sampled_from(list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,;:!?()[]'\"/&%$") + list(TRICKY))

texts = st.text(alphabet=ALPHABET, min_size=0, max_size=200)
nonblank = st.text(alphabet=ALPHABET, min_size=1, max_size=120).filter(lambda s: s.strip() != "")

HIGHLIGHTS = [None, None, None, "yellow", "cyan", "lightGray", "shd:C0C0C0", "shd:00FF00"]
SIZES = [4, 8, 16, 20, 22, 24, 26]


@st.composite
def ftexts(draw, text_strategy=nonblank, cite=False) -> FText:
    text = draw(text_strategy)
    n = len(text)
    ft = FText(text)
    # random runs of formatting rather than per-char noise, like real cards
    i = 0
    while i < n:
        j = min(n, i + draw(st.integers(1, 12)))
        ft.set_span("underline", i, j, draw(st.booleans()))
        ft.set_span("emphasis", i, j, draw(st.booleans()) and draw(st.booleans()))
        ft.set_span("highlight", i, j, draw(st.sampled_from(HIGHLIGHTS)))
        ft.set_span("size", i, j, draw(st.sampled_from(SIZES)))
        ft.set_span("bold", i, j, draw(st.sampled_from([None, None, "on", "off"])))
        ft.set_span("italic", i, j, draw(st.sampled_from([None, None, None, "on"])))
        ft.set_span("strike", i, j, draw(st.booleans()) and draw(st.booleans()) and draw(st.booleans()))
        ft.set_span("vert", i, j, draw(st.sampled_from([None, None, None, "superscript"])))
        i = j
    if cite:
        k = draw(st.integers(1, n))
        ft.set_span("cite13", 0, k, True)
    return ft


@st.composite
def cards(draw) -> Card:
    tag = draw(ftexts())
    cite = draw(ftexts(cite=True))
    paras = draw(st.lists(ftexts(), min_size=1, max_size=3))
    return Card(tag=tag, cite=cite, body=FText.concat(paras), meta={"id": "h"})
