import pytest
from hypothesis import given
from hypothesis import strategies as st

from cardcutter.card import FText
from cardcutter.verify.integrity import IntegrityError, IntegrityPolicy, verify_body

SOURCE = (
    "Background sentence here. Without a binding agreement, disputes over “marine protected areas” "
    "will escalate—and biodiversity collapses. Experts say so, maybe.\nA new paragraph starts here."
)


def make_body(text: str, ul: str, hl: str, em: str | None = None) -> FText:
    ft = FText(text)
    s = text.index(ul)
    ft.set_span("underline", s, s + len(ul))
    s = text.index(hl)
    ft.set_span("highlight", s, s + len(hl), "yellow")
    if em:
        s = text.index(em)
        ft.set_span("emphasis", s, s + len(em))
    return ft


GOOD_TEXT = "Without a binding agreement, disputes over “marine protected areas” will escalate—and biodiversity collapses."


def test_exact_excerpt_passes():
    body = make_body(GOOD_TEXT, "disputes over “marine protected areas” will escalate", "disputes", "escalate")
    rep = verify_body(body, SOURCE)
    assert rep.offset == SOURCE.index(GOOD_TEXT)
    assert rep.warnings == []


def test_straightened_quote_fails():
    altered = GOOD_TEXT.replace("“", '"')
    with pytest.raises(IntegrityError, match="not a contiguous substring"):
        verify_body(make_body(altered, "disputes", "disputes"), SOURCE)


def test_wrong_offset_fails_loudly():
    body = make_body(GOOD_TEXT, "disputes", "disputes")
    with pytest.raises(IntegrityError, match="differs from source"):
        verify_body(body, SOURCE, offset=SOURCE.index(GOOD_TEXT) + 1)


def test_skipped_text_fails():
    skipped = "Without a binding agreement, disputes will escalate—and biodiversity collapses."
    with pytest.raises(IntegrityError):
        verify_body(make_body(skipped, "disputes", "disputes"), SOURCE)


def test_highlight_outside_underline_fails():
    body = make_body(GOOD_TEXT, "disputes over", "biodiversity")
    with pytest.raises(IntegrityError, match="not underlined"):
        verify_body(body, SOURCE)
    relaxed = IntegrityPolicy(require_highlight_in_underline=False)
    assert verify_body(body, SOURCE, policy=relaxed).offset > 0


def test_emphasis_outside_underline_fails():
    body = make_body(GOOD_TEXT, "disputes over", "disputes", em="collapses")
    with pytest.raises(IntegrityError, match="emphasized"):
        verify_body(body, SOURCE)


def test_empty_and_unformatted_cards_fail():
    with pytest.raises(IntegrityError, match="empty"):
        verify_body(FText("   "), SOURCE)
    with pytest.raises(IntegrityError, match="nothing is underlined"):
        verify_body(FText(GOOD_TEXT), SOURCE)


def test_mid_sentence_start_fails():
    text = "a binding agreement, disputes over"
    with pytest.raises(IntegrityError, match="sentence boundary"):
        verify_body(make_body(text, "disputes", "disputes"), SOURCE)


def test_mid_sentence_end_is_a_warning_unless_disallowed():
    text = "Without a binding agreement, disputes over"
    body = make_body(text, "disputes", "disputes")
    assert verify_body(body, SOURCE).warnings
    with pytest.raises(IntegrityError, match="mid-sentence"):
        verify_body(body, SOURCE, policy=IntegrityPolicy(allow_mid_sentence_end=False))


def test_card_may_span_paragraphs():
    text = "Experts say so, maybe.\nA new paragraph starts here."
    assert verify_body(make_body(text, "Experts say so", "Experts"), SOURCE).offset == SOURCE.index(text)


@given(st.integers(0, len(GOOD_TEXT) - 1), st.sampled_from(["x", "’", " ", "—", ""]))
def test_any_single_edit_is_caught(pos, repl):
    altered = GOOD_TEXT[:pos] + repl + GOOD_TEXT[pos + 1:]
    if altered == GOOD_TEXT or altered in SOURCE:
        return
    body = FText(altered)
    body.set_span("underline", 0, len(altered))
    body.set_span("highlight", 0, len(altered), "yellow")
    with pytest.raises(IntegrityError):
        verify_body(body, SOURCE)
