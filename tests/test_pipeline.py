import json
from pathlib import Path

import pytest

from cardcutter.card import Card
from cardcutter.cutter.pipeline import cut_window, underlined_view
from cardcutter.cutter.rules import acronym_initials, clean_ranges
from cardcutter.eval.holdout import select_holdout
from cardcutter.eval.metrics import compare, prf
from cardcutter.eval.run import run_eval, write_report
from cardcutter.tokenize import char_mask_from_ranges, tokenize
from cardcutter.verify.integrity import IntegrityError
from tests.fakes import FakeBackend, make_config, registry_with

SOURCE = ("Experts warn that disputes over marine protected areas (MPAs) will escalate. "
          "Without a treaty, marine protected areas collapse and biodiversity loss becomes irreversible.")


def scripted(ul, hl, partial=(), em=(), tag="Disputes over MPAs escalate", tag_ul=("MPAs escalate",),
             scores=((5, 5, 5),), power=False, hl_retry=None):
    """Answers each stage by schema name. `scores` is consumed one read-back call at a time
    (the last entry repeats); `hl_retry` is the highlight returned after read-back feedback."""
    state = {"rb": 0, "hl": 0}

    def respond(stage, prompt):
        if stage == "underline":
            return {"plan": "p", "underline": [{"start": a, "end": b} for a, b in ul]}
        if stage == "highlight":
            state["hl"] += 1
            use = hl_retry if (hl_retry is not None and state["hl"] > 1) else hl
            return {"plan": "p", "highlight": [{"start": a, "end": b} for a, b in use],
                    "partial": [{"word": w, "start": s, "end": e} for w, s, e in partial]}
        if stage == "emphasis":
            return {"emphasis": [{"start": a, "end": b} for a, b in em]}
        if stage == "tag":
            return {"plan": "p", "tag": tag, "underline": list(tag_ul)}
        if stage == "readback":
            g, c, pt = scores[min(state["rb"], len(scores) - 1)]
            state["rb"] += 1
            return {"grammatical": g, "coherent": c, "proves_tag": pt, "power_tag": power,
                    "feedback": "reads choppy" if min(g, c, pt) < 4 else "fine"}
        raise AssertionError(f"unexpected stage {stage}")
    return respond


def word(i):
    return tokenize(SOURCE)[i].text


def test_cut_builds_nested_masks_and_passes_integrity(tmp_path):
    cfg = make_config(tmp_path, acronym_rule=False)
    toks = tokenize(SOURCE)
    # underline words 2-12 and 13-22; highlight a range that pokes outside underline
    fake = FakeBackend("fake", scripted(ul=[(2, 12), (13, 22)], hl=[(0, 5), (14, 17)], partial=[(19, 0, 4)], em=[(15, 16), (0, 1)]))
    reg = registry_with(cfg, fake=fake)
    res = cut_window(reg, cfg, SOURCE, "Disputes escalate")
    b = res.body
    assert b.text == SOURCE
    ul, hl, em = b.marks["underline"], b.marks["highlight"], b.marks["emphasis"]
    assert all(ul[i] for i, h in enumerate(hl) if h)          # highlight inside underline
    assert all(ul[i] for i, e in enumerate(em) if e)          # emphasis inside underline
    assert not any(hl[toks[0].start:toks[1].end])            # words 0-1 were not underlined
    biod = toks[19]
    assert word(19) == "biodiversity"
    assert "".join(SOURCE[i] for i in range(biod.start, biod.end) if hl[i]) == "biod"
    assert b.marks["size"][toks[0].start] == 16               # unread text shrunk to 8 pt
    assert [c[0] for c in fake.calls] == ["underline", "highlight", "tag", "readback", "emphasis"]
    assert res.tag.text == "Disputes over MPAs escalate"
    assert res.readback["min"] == 5 and not res.warnings
    assert underlined_view(toks, ul).startswith("2:that 3:disputes")


def test_llm_never_supplies_text_only_indices(tmp_path):
    cfg = make_config(tmp_path)
    fake = FakeBackend("fake", scripted(ul=[(0, 30)], hl=[(0, 3)]))
    reg = registry_with(cfg, fake=fake)
    cut_window(reg, cfg, SOURCE, "c")
    user_prompts = [p.user for _, p in fake.calls]
    assert "0:Experts" in user_prompts[0]


def test_empty_highlight_fails_loudly(tmp_path):
    cfg = make_config(tmp_path)
    reg = registry_with(cfg, fake=FakeBackend("fake", scripted(ul=[(0, 5)], hl=[])))
    with pytest.raises(IntegrityError, match="nothing is highlighted"):
        cut_window(reg, cfg, SOURCE, "c")


def test_clean_ranges_clips_swaps_and_splits():
    out, fixes = clean_ranges([{"start": 5, "end": 2}, {"start": 8, "end": 99}, {"start": 200, "end": 300}], 10)
    assert out == [(2, 5), (8, 9)] and fixes == 3
    out, fixes = clean_ranges([{"start": 0, "end": 5}], 10, allowed={0, 1, 4, 5})
    assert out == [(0, 1), (4, 5)]


def test_acronym_rule_highlights_initials():
    toks = tokenize(SOURCE)
    hl = char_mask_from_ranges(len(SOURCE), toks, [(14, 17)])  # "marine protected areas collapse"
    changed = acronym_initials(SOURCE, toks, hl)
    assert changed == 1
    phrase_start = SOURCE.index("marine protected areas collapse")
    read = "".join(SOURCE[i] for i in range(phrase_start, phrase_start + 31) if hl[i])
    assert read == "mpa collapse"


def test_metrics():
    text = "ab cd"
    assert prf([1, 1, 0, 0, 0], [1, 0, 0, 1, 0], text) == {"p": 0.5, "r": 0.5, "f1": 0.5}


def test_eval_end_to_end_with_fake_backend(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "handwritten_card.json").read_text())
    gold = Card.from_json(fixture)
    gold.meta["id"] = "fixture#0"
    cfg = make_config(tmp_path)
    n = len(tokenize(gold.body.text))
    reg = registry_with(cfg, fake=FakeBackend("fake", scripted(ul=[(5, 25)], hl=[(5, 9)], em=[(7, 8)])))
    run = run_eval(cfg, reg, [gold], progress=lambda m: None)
    s = run.summary()
    assert s["integrity_pass_rate"] == 1.0 and 0 < s["underline_f1"] <= 1
    report = write_report(cfg, run)
    text = report.read_text()
    assert "integrity pass rate | 100.0%" in text
    assert gold.body.text[:40] not in text  # reports carry metrics, not evidence
    assert (cfg.root / "runs").exists()
    assert n > 25


def test_holdout_prefers_unformatted_clean_cards():
    from cardcutter.card import FText

    def mk(i, ul_frac, flags=()):
        words = " ".join(f"w{i}x{k}" for k in range(200)) + "."
        ft = FText(words.capitalize())
        cut = int(len(ft.text) * ul_frac)
        ft.set_span("underline", 0, cut)
        ft.set_span("highlight", 0, cut // 2, "yellow")
        return Card(tag=FText("t"), cite=FText("c"), body=ft, meta={"id": f"c{i}", "flags": list(flags)})

    cards = [mk(1, 0.5), mk(2, 0.15), mk(3, 0.3), mk(4, 0.1, flags=["no_cite"])]
    ids = [e["id"] for e in select_holdout(cards, 2)]
    assert ids == ["c2", "c3"]


def test_tag_underlines_phrases_at_13pt():
    from cardcutter.cutter.pipeline import build_tag

    t = build_tag("Disputes over MPAs\n escalate  fast", ["MPAs escalate", "fast", "missing phrase"])
    assert t.text == "Disputes over MPAs escalate fast"
    on = "".join(ch for ch, u in zip(t.text, t.marks["underline"]) if u)
    assert on == "MPAs escalatefast"
    assert set(t.marks["size"]) == {26}


def test_readback_retries_then_keeps_best_highlight(tmp_path):
    cfg = make_config(tmp_path, acronym_rule=False)
    fake = FakeBackend("fake", scripted(ul=[(0, 30)], hl=[(0, 2)], hl_retry=[(0, 4)],
                                        scores=[(2, 2, 3), (5, 4, 4)]))
    res = cut_window(registry_with(cfg, fake=fake), cfg, SOURCE, "c")
    stages = [c[0] for c in fake.calls]
    assert stages == ["underline", "highlight", "tag", "readback", "highlight", "readback", "emphasis"]
    assert "reads choppy" in fake.calls[4][1].user           # feedback reaches the retry prompt
    assert res.readback["min"] == 4 and len(res.readback_history) == 2
    assert res.body.read_aloud().startswith("Experts warn that disputes over")
    assert not any("threshold" in w for w in res.warnings)


def test_readback_below_threshold_warns_and_flags_power_tag(tmp_path):
    cfg = make_config(tmp_path, acronym_rule=False)
    fake = FakeBackend("fake", scripted(ul=[(0, 30)], hl=[(0, 3)], scores=[(3, 2, 2), (2, 2, 2), (1, 1, 1)], power=True))
    res = cut_window(registry_with(cfg, fake=fake), cfg, SOURCE, "c")
    stages = [c[0] for c in fake.calls]
    assert stages.count("readback") == 3                       # 1 + max_retries
    assert stages.count("underline") == 2                      # second retry re-underlines (proves tag <= 2)
    assert "Underline again" in [c for c in fake.calls if c[0] == "underline"][1][1].user
    assert res.readback["min"] == 2                             # the best attempt is kept
    assert any("below threshold" in w for w in res.warnings)
    assert any("power-tag" in w for w in res.warnings)


def test_disabling_tag_and_readback_skips_calls(tmp_path):
    cfg = make_config(tmp_path, tag=False)
    cfg.data["readback"]["enabled"] = False
    fake = FakeBackend("fake", scripted(ul=[(0, 30)], hl=[(0, 3)]))
    res = cut_window(registry_with(cfg, fake=fake), cfg, SOURCE, "c")
    assert [c[0] for c in fake.calls] == ["underline", "highlight", "emphasis"]
    assert res.tag is None and res.readback is None


def test_examples_are_compact_and_reach_the_prompt(tmp_path):
    from cardcutter.cutter.examples import markup, render_examples

    fixture = json.loads((Path(__file__).parent / "fixtures" / "handwritten_card.json").read_text())
    ex = Card.from_json(fixture)
    m = markup(ex)
    assert "[[" in m and "_" in m
    assert ex.tag.text.strip() in render_examples([ex])
    cfg = make_config(tmp_path)
    fake = FakeBackend("fake", scripted(ul=[(0, 30)], hl=[(0, 3)]))
    res = cut_window(registry_with(cfg, fake=fake), cfg, SOURCE, "c", examples=[ex])
    assert "Example 1" in fake.calls[0][1].user
    assert res.logs[0].examples == 1


def test_fit_prompt_drops_examples_to_fit_budget():
    from cardcutter.cutter.pipeline import fit_prompt
    from cardcutter.llm.base import Prompt

    fixture = json.loads((Path(__file__).parent / "fixtures" / "handwritten_card.json").read_text())
    ex = Card.from_json(fixture)
    build = lambda e: Prompt("sys", "src " * 50 + e)  # noqa: E731
    _, k = fit_prompt(build, [ex] * 3, "markup", budget=10_000)
    assert k == 3
    _, k = fit_prompt(build, [ex] * 3, "markup", budget=80)
    assert k == 0
    with pytest.raises(ValueError):
        fit_prompt(lambda e: Prompt("sys", "src " * 5000), [], "markup", budget=100)


def test_eval_context_uses_block_path_and_previous_tag():
    from cardcutter.card import FText
    from cardcutter.eval.run import eval_context

    def mk(tag, block="U---1AR"):
        return Card(tag=FText(tag), cite=FText("c"), body=FText("b"),
                    meta={"id": tag, "file": "f.docx", "pocket": "DA", "hat": "DA---Deterrence", "block": block})

    prev, gold = mk("ATACMs are depleted."), mk("And HIMARs.")
    ctx = eval_context(gold, prev)
    assert "DA > DA---Deterrence > U---1AR" in ctx and "ATACMs are depleted." in ctx and "continues" in ctx
    assert "before it" not in eval_context(gold, mk("Other block card", block="Impact"))


def test_tag_strips_markdown_the_model_adds():
    from cardcutter.cutter.pipeline import build_tag

    t = build_tag("MPAs are _paper parks_---compliance is **low**.", ["_paper parks_", "low"])
    assert t.text == "MPAs are paper parks---compliance is low."
    assert "".join(ch for ch, u in zip(t.text, t.marks["underline"]) if u) == "paper parkslow"


def test_acronym_rule_unhighlights_the_parenthetical():
    text = "Many marine protected areas (MPAs) are paper parks."
    toks = tokenize(text)
    hl = char_mask_from_ranges(len(text), toks, [(0, 5)])
    acronym_initials(text, toks, hl)
    read = "".join(ch if h else " " for ch, h in zip(text, hl))
    assert " ".join(read.split()) == "Many m p a are"


def test_align_read_aloud_maps_plan_to_source_words_in_order():
    from cardcutter.cutter.rules import align_read_aloud

    text = "the collapse of fisheries would cause widespread food insecurity and the biodiversity loss"
    toks = tokenize(text)
    allowed = set(range(len(toks)))
    whole, partial, cov = align_read_aloud("collapse of fisheries cause food insecurity, biod loss", toks, allowed)
    assert [toks[i].text for i in whole] == ["collapse", "of", "fisheries", "cause", "food", "insecurity", "loss"]
    assert partial == [(11, 4)] and cov == 1.0
    # words outside the allowed (underlined) set can't be matched; invented words lower coverage
    whole, _, cov = align_read_aloud("collapse causes famine", toks, allowed - {1})
    assert whole == [] and cov == 0.0


def test_highlight_from_plan_is_opt_in(tmp_path):
    cfg = make_config(tmp_path, acronym_rule=False, highlight_from_plan=True)
    fake = FakeBackend("fake", scripted(ul=[(0, 30)], hl=[(0, 1)]))
    # the scripted plan is "p" (coverage 0), so the model's indices are kept
    res = cut_window(registry_with(cfg, fake=fake), cfg, SOURCE, "c")
    assert res.body.read_aloud().startswith("Experts warn")
