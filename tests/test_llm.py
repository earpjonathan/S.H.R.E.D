import json
import os

import pytest

from cardcutter.llm import prompts, schemas
from cardcutter.llm.base import BackendError, BackendUnavailable, Prompt, parse_json
from cardcutter.llm.cost import CostTracker, Price, SpendingCapExceeded
from cardcutter.llm.env import load_dotenv, redact
from cardcutter.llm.retry import generate_with_retries
from tests.fakes import FakeBackend, make_config, registry_with


def test_parse_json_accepts_fences_and_validates():
    good = {"plan": "x", "underline": [{"start": 0, "end": 3}]}
    assert parse_json("```json\n" + json.dumps(good) + "\n```", schemas.UNDERLINE) == good
    with pytest.raises(Exception):
        parse_json('{"plan": "x"}', schemas.UNDERLINE)


def test_retry_feeds_error_back_then_succeeds():
    replies = iter(["not json", json.dumps({"emphasis": [{"start": 1, "end": 2}]})])
    seen_history = []

    def call(prompt, history):
        seen_history.append(list(history))
        return next(replies), {"input": 10, "output": 5}, {}

    res = generate_with_retries(call, Prompt("s", "u"), schemas.EMPHASIS, backend="b", model="m")
    assert res.attempts == 2 and res.input_tokens == 20
    assert seen_history[0] == [] and "not valid JSON" in seen_history[1][-1]["content"]


def test_retry_gives_up_loudly():
    with pytest.raises(BackendError, match="no valid JSON"):
        generate_with_retries(lambda p, h: ("{}", {}, {}), Prompt("s", "u"), schemas.EMPHASIS, backend="b", model="m")


def test_prompts_render_and_fail_on_missing_values():
    p = prompts.render("underline", claim="C", context="", examples="", feedback="", source="0:A 1:b", underline_words=20,
                       underline_pct=35, n_words=60)
    assert "0:A 1:b" in p.user and "35%" in p.user and "index" in p.system
    with pytest.raises(KeyError):
        prompts.render("underline", claim="C")
    assert len(prompts.fingerprint("highlight")) == 8


PROMPT_VARS = {
    "trim_rank": dict(claim="C", sentences="0: A b. (2 words)", count=1, target=10),
    "claimparts": dict(claim="C"),
    "claimcheck": dict(claim="C", tag="T", read_aloud="a"),
    "claimcheck_parts": dict(claim="C", parts="1. C", tag="T", read_aloud="a"),
    "underline_more": dict(claim="C", context="", examples="", source="0:A 1:*b", underlined="b", why="",
                           plain="These paragraphs have nothing underlined yet.\n", add=8),
    "underline": dict(claim="C", context="", examples="", source="0:A", underline_words=20, underline_pct=35, n_words=60, feedback=""),
    "highlight": dict(claim="C", context="", examples="", underlined="0:A", highlight_words=8, underlined_count=20, feedback=""),
    "highlight_fluent": dict(claim="C", context="", examples="", underlined="0:A", highlight_words=8, max_words=10, underlined_count=20, feedback=""),
    "emphasis": dict(claim="C", context="", underlined="0:A", max_boxes=3),
    "tag": dict(claim="C", context="", read_aloud="a", examples=""),
    "readback": dict(tag="T", read_aloud="a", examples=""),
    "quals": dict(excerpt="E", known="{}"),
    "locate": dict(claim="C", context="", paragraphs="[0] p", max_words=650),
    "suggest": dict(outline="O"),
    "intent": dict(tag="T", read_aloud="a", article="A"),
    "trim": dict(claim="C", examples="", count=3, highlighted="0:a", read_aloud="a", target=2),
    "trim_fluent": dict(claim="C", examples="", count=3, highlighted="0:a", read_aloud="a", target=2),
    "smooth": dict(claim="C", view="0:*a", read_aloud="a", max_add=2),
}


@pytest.mark.parametrize("name", sorted(PROMPT_VARS))
def test_every_prompt_renders_with_its_variables(name):
    p = prompts.render(name, **PROMPT_VARS[name])
    assert p.system.strip() and p.user.strip() and "{{" not in p.system + p.user


def test_cost_tracker_and_cap():
    t = CostTracker(prices={"m": Price(4.0, 20.0)}, session_cap_usd=0.01)
    assert t.cost("m", 1_000_000, 0) == pytest.approx(4.0)
    assert t.cost("m", 1000, 1000, free=True) == 0.0
    t.record(0.009, 10, 10)
    with pytest.raises(SpendingCapExceeded):
        t.guard(0.002)


def test_dotenv_never_overrides_and_redacts(tmp_path, monkeypatch):
    monkeypatch.setenv("CC_TEST_KEY", "already-set-value-123")
    (tmp_path / ".env").write_text("CC_TEST_KEY=from-file\nexport CC_OTHER_KEY='secret-value-abcdef'\n# c\n")
    loaded = load_dotenv(tmp_path / ".env")
    assert loaded == ["CC_OTHER_KEY"]
    assert os.environ["CC_TEST_KEY"] == "already-set-value-123"
    assert "secret-value-abcdef" not in redact("error with secret-value-abcdef inside")
    monkeypatch.delenv("CC_OTHER_KEY")


def test_registry_presets_overrides_and_costs(tmp_path):
    cfg = make_config(tmp_path)
    fake = FakeBackend("fake", lambda s, p: {"emphasis": []})
    paid = FakeBackend("paid", lambda s, p: {"emphasis": []}, model="paid-1", is_local=False, is_free=False)
    reg = registry_with(cfg, fake=fake, paid=paid)
    assert reg.backend_name("highlight") == "fake"
    reg.overrides["highlight"] = "paid"
    res = reg.call("highlight", Prompt("s", "u"), schemas.EMPHASIS)
    assert res.cost_usd == pytest.approx((1000 * 10 + 100 * 50) / 1e6)
    assert reg.costs.spent_usd == pytest.approx(res.cost_usd)


def test_local_down_offers_fallback_and_respects_no(tmp_path):
    cfg = make_config(tmp_path)
    down = FakeBackend("fake", lambda s, p: {}, available=False)
    paid = FakeBackend("paid", lambda s, p: {}, model="paid-1", is_local=False, is_free=False)
    reg = registry_with(cfg, fake=down, paid=paid)
    asked = []
    with pytest.raises(BackendUnavailable):
        reg.ensure_available(("underline",), confirm=lambda q: asked.append(q) or False)
    assert "PAID API" in asked[0] and "fake is down" in asked[0]
    assert reg.backend_name("underline") == "fake"  # nothing switched
    notes = reg.ensure_available(("underline",), confirm=lambda q: True)
    assert reg.backend_name("underline") == "paid" and "unavailable" in notes[0]
