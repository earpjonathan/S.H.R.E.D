"""`cardcutter cut` end to end through the CLI, with the fake backend (no model calls)."""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cardcutter.app.cli import app
from cardcutter.corpus.parse import parse_docx
from cardcutter.llm import registry as registry_mod
from tests.fakes import FakeBackend
from tests.test_web import ARTICLE, respond

EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.docx"))

CONFIG = """
[llm]
preset = "local"
session_cap_usd = 1.0
[llm.presets.local]
default = "fake"
[llm.backends.fake]
type = "ollama"
model = "fake-1"
[cut]
density = "normal"
emphasis = true
acronym_rule = false
tag = true
max_window_words = 650
[cut.densities.normal]
underline_pct = 35
highlight_pct = 38
emphasis_pct = 30
[retrieval]
enabled = false
[readback]
enabled = true
min_score = 4
max_retries = 2
[cite]
default_format = "inline"
missing_marker = "[{field}?]"
extract_quals = true
[render]
template_source = "{template_source}"
template_path = "data/template.docx"
highlight_color = "yellow"
shrink_unread_pt = 8
[verify]
allow_mid_sentence_end = true
"""


@pytest.mark.skipif(not EXAMPLES, reason="examples/*.docx not present (needed for the Word template)")
def test_cut_command_writes_a_verified_docx(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(CONFIG.replace("{template_source}", str(EXAMPLES[0])))
    fake = FakeBackend("fake", respond)
    original = registry_mod.Registry.backend
    monkeypatch.setattr(registry_mod.Registry, "backend",
                        lambda self, name: fake if name == "fake" else original(self, name))
    out = tmp_path / "out" / "card.docx"
    html = tmp_path / "out" / "card.html"
    res = CliRunner().invoke(app, ["cut", "--text", ARTICLE, "--claim", "MPA disputes escalate", "--date", "2026-01-07",
                                   "--out", str(out), "--html", str(html), "--config", str(cfg_path)])
    assert res.exit_code == 0, res.output
    assert "TAG:  MPA disputes escalate" in res.output
    assert "cite is missing" in res.output                 # title/publication/url were never invented
    [card] = parse_docx(out).cards
    assert card.tag.text.strip() == "MPA disputes escalate"
    assert card.body.read_aloud().startswith("Experts warn that disputes over")
    assert "Doe 26" in card.cite.text and html.exists()


def test_cut_needs_exactly_one_source(tmp_path):
    res = CliRunner().invoke(app, ["cut", "--claim", "x"])
    assert res.exit_code != 0
