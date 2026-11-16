"""Masks -> .docx -> masks must be lossless."""
import json
import zipfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings

from cardcutter.card import Card, FText
from cardcutter.corpus.parse import parse_docx
from cardcutter.render.docx import append_under_heading, write_new
from cardcutter.render.template import build_template
from tests.strategies import cards, ftexts

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.docx"))
FIXTURE = ROOT / "tests" / "fixtures" / "handwritten_card.json"
needs_examples = pytest.mark.skipif(not EXAMPLES, reason="examples/*.docx not present")


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    if not EXAMPLES:
        pytest.skip("examples/*.docx not present")
    return build_template(EXAMPLES[0], tmp_path_factory.mktemp("tpl") / "template.docx")


def comparable(c: Card) -> str:
    return json.dumps({"tag": c.tag.to_json(), "cite": c.cite.to_json(), "body": c.body.to_json()}, sort_keys=True)


@given(ftexts())
def test_ftext_json_round_trip(ft):
    assert FText.from_json(json.loads(json.dumps(ft.to_json()))).marks == ft.marks


@needs_examples
@given(card=cards())
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_random_cards_round_trip_through_docx(card, template, tmp_path):
    out = tmp_path / "rt.docx"
    write_new([card], template, out)
    back = parse_docx(out).cards
    assert len(back) == 1
    assert comparable(back[0]) == comparable(card)


@needs_examples
def test_handwritten_fixture_renders_verbatim_styles(template, tmp_path):
    card = Card.from_json(json.loads(FIXTURE.read_text()))
    out = write_new([card], template, tmp_path / "fixture.docx")
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert '<w:pStyle w:val="Heading4"/>' in xml
    assert '<w:rStyle w:val="Style13ptBold"/>' in xml
    assert '<w:rStyle w:val="StyleUnderline"/>' in xml
    assert '<w:rStyle w:val="Emphasis"/>' in xml
    assert '<w:highlight w:val="yellow"/>' in xml
    assert '<w:sz w:val="16"/>' in xml  # shrunk unread text
    back = parse_docx(out).cards[0]
    assert comparable(back) == comparable(card)
    assert back.body.read_aloud().startswith("Without a binding agreement m p a escalate")


@needs_examples
def test_every_example_card_round_trips(tmp_path):
    from cardcutter.corpus.roundtrip import roundtrip_corpus

    corpus = [c for f in EXAMPLES for c in parse_docx(f).cards]
    results = roundtrip_corpus(EXAMPLES, corpus, tmp_path)
    assert sum(r.checked for r in results.values()) == len(corpus) > 0
    assert [m for r in results.values() for m in r.mismatches] == []


@needs_examples
def test_append_under_heading(template, tmp_path):
    card = Card.from_json(json.loads(FIXTURE.read_text()))
    target = tmp_path / "target.docx"
    write_new([card], template, target)
    # make a document with a block heading, then append under it
    import zipfile as zf
    from lxml import etree
    from cardcutter.ooxml import W, W_NS

    with zf.ZipFile(target) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
    body = doc.find(f"{W}body")
    h = etree.fromstring(f'<w:p xmlns:w="{W_NS}"><w:pPr><w:pStyle w:val="Heading3"/></w:pPr><w:r><w:t>My Block</w:t></w:r></w:p>')
    h2 = etree.fromstring(f'<w:p xmlns:w="{W_NS}"><w:pPr><w:pStyle w:val="Heading3"/></w:pPr><w:r><w:t>Next Block</w:t></w:r></w:p>')
    body.insert(0, h)
    body.find(f"{W}sectPr").addprevious(h2)
    from cardcutter.render.docx import _write_zip

    _write_zip(target, target, {"word/document.xml": etree.tostring(doc)})
    out = append_under_heading([card], target, "my block", template, tmp_path / "appended.docx")
    parsed = parse_docx(out)
    assert len(parsed.cards) == 2
    assert [t for _, t in parsed.outline] == ["My Block", card.tag.text, card.tag.text, "Next Block"]
