from cardcutter.cutter.pipeline import cut_window
from cardcutter.tokenize import tokenize
from tests.fakes import FakeBackend, make_config, registry_with

SOURCE = ("Officials said the program has failed to meet any of its targets, and several independent audits "
          "found that most of the funds were spent on administration rather than on services, which the "
          "agency says it will fix next year.")  # 39 words; the test density aims for 14 underlined


def run(tmp_path, first, more=None, **cut):
    cfg = make_config(tmp_path, emphasis=False, tag=False, **cut)
    seen = []

    def respond(stage, prompt):
        if stage == "underline":
            topup = "too thin" in prompt.user
            seen.append((topup, prompt.user))
            ranges = more if topup else first
            return {"plan": "p", "underline": [{"start": a, "end": b} for a, b in ranges]}
        return {"plan": "p", "highlight": [{"start": 3, "end": 4}], "partial": []}

    res = cut_window(registry_with(cfg, fake=FakeBackend("fake", respond)), cfg, SOURCE, "the program failed",
                     do_readback=False)
    toks = tokenize(SOURCE)
    ul = {t.index for t in toks if any(res.body.marks["underline"][t.start:t.end])}
    return ul, seen


def test_a_thin_underline_gets_topped_up_with_new_ranges_only(tmp_path):
    ul, seen = run(tmp_path, first=[(3, 5)], more=[(3, 4), (16, 22)], underline_topup=True)
    assert [t for t, _ in seen] == [False, True]
    prompt = seen[1][1]
    assert "3:*program" in prompt and "18:most" in prompt  # underlined words are marked, others are not
    assert ul == {3, 4, 5, 16, 17, 18, 19, 20, 21, 22}


def test_no_top_up_when_off_or_when_the_underline_is_full_enough(tmp_path):
    ul, seen = run(tmp_path, first=[(3, 5)], more=[(16, 22)])
    assert [t for t, _ in seen] == [False] and ul == {3, 4, 5}
    ul, seen = run(tmp_path, first=[(3, 10)], more=[(16, 22)], underline_topup=True)
    assert [t for t, _ in seen] == [False] and ul == set(range(3, 11))


def test_paragraphs_with_nothing_underlined_are_offered_for_a_top_up(tmp_path):
    paras = ["China controls most rare-earth refining in the world today, and it has used that hold before.",
             "Arms control limits the pressure to build up arsenals that comes from worst-case planning by rivals.",
             "The reporter then walked back to the car and drove home through the rain that evening."]
    source = "\n".join(paras)
    toks = tokenize(source)
    second = next(t.index for t in toks if t.text == "Arms")
    third = next(t.index for t in toks if t.text == "reporter") - 1
    cfg = make_config(tmp_path, emphasis=False, tag=False, underline_topup=True, topup_below=0.0)
    seen = []

    def respond(stage, prompt):
        if stage == "underline":
            seen.append(prompt.user)
            if "nothing underlined yet" in prompt.user:
                return {"plan": "p", "underline": [{"start": second, "end": second + 8}]}
            return {"plan": "p", "underline": [{"start": 0, "end": 7}]}
        return {"plan": "p", "highlight": [{"start": 0, "end": 3}], "partial": []}

    res = cut_window(registry_with(cfg, fake=FakeBackend("fake", respond)), cfg, source, "rivals build up",
                     do_readback=False)
    assert len(seen) == 2 and "too thin" not in seen[1]
    assert f'from word {second}: "Arms control' in seen[1] and f'from word {third}: "The reporter' in seen[1]
    assert res.body.text == paras[0] + "\n" + paras[1]          # the plain last paragraph is dropped
