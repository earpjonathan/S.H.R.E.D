"""The cutting pipeline for one window of source text:

    underline -> highlight -> emphasis -> tag -> read-back (-> re-highlight, at most twice)

Every LLM stage that touches the card body returns word-index ranges. Code turns them
into per-character masks, enforces nesting (highlight and emphasis inside underline),
and runs the integrity check. A card that fails it raises IntegrityError and is never
exported. Retrieved human-cut cards are given to each stage as few-shot examples; when a
prompt would not fit the context window, examples are dropped first, never the source.
"""
from __future__ import annotations

import re

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Sequence

from cardcutter.card import Card, FText
from cardcutter.config import Config
from cardcutter.cutter.examples import (render_examples, render_readback_examples, render_tag_examples,
                                        render_trim_examples)
from cardcutter.cutter.rules import (acronym_initials, align_read_aloud, bridge_gaps, cap_emphasis, clean_ranges,
                                     drop_repeats, drop_stranded, fill_underline_gaps, finish_sentences,
                                     fix_dangling_negators, join_adjacent, keep_negators, marked_edges,
                                     restore_subjects, starts_mid_sentence, tidy_spaces, unread_footnotes)
from cardcutter.llm import prompts, schemas
from cardcutter.llm.base import Prompt
from cardcutter.llm.cost import estimate_tokens
from cardcutter.llm.registry import Registry
from cardcutter.tokenize import Token, apply_partial, char_mask_from_ranges, indexed_view, sentence_starts, tokenize
from cardcutter.verify.integrity import IntegrityPolicy, verify_body

Progress = Callable[[str], None]


@dataclass
class StageLog:
    stage: str
    backend: str
    model: str
    seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    attempts: int
    fixes: int = 0
    plan: str = ""
    examples: int = 0


@dataclass
class CutResult:
    body: FText
    offset: int
    tag: FText | None = None
    readback: dict[str, Any] | None = None
    readback_history: list[dict[str, Any]] = field(default_factory=list)
    # every version the read-back judge heard, with its underline/highlight masks (for eval)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    example_ids: list[str] = field(default_factory=list)
    logs: list[StageLog] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(l.seconds for l in self.logs)

    @property
    def cost_usd(self) -> float:
        return sum(l.cost_usd for l in self.logs)

    def log_dicts(self) -> list[dict[str, Any]]:
        return [asdict(l) for l in self.logs]


def density(cfg: Config, n_words: int) -> dict[str, int]:
    """Target counts for a window of n_words. Config gives [coef, exp] (words = coef * n^exp,
    fit to the example cards) or, in older configs, plain percentages."""
    name = cfg.get("cut.density", "normal")
    d = cfg.get(f"cut.densities.{name}")
    if not d:
        raise KeyError(f"no [cut.densities.{name}] in config.toml")
    if "underline" in d:
        ul = d["underline"][0] * n_words ** d["underline"][1]
        hl = d["highlight"][0] * n_words ** d["highlight"][1]
    else:
        ul = n_words * d["underline_pct"] / 100
        hl = ul * d["highlight_pct"] / 100
    ul = max(4, min(n_words, round(ul)))
    hl = max(2, min(ul, round(hl)))
    return {"underline_words": ul, "underline_pct": round(100 * ul / max(n_words, 1)),
            "highlight_words": hl, "emphasis_pct": int(d.get("emphasis_pct", 26))}


def _runs(indices: Sequence[int]) -> list[list[int]]:
    """Consecutive word indices grouped: [3, 4, 5, 9, 10] -> [[3, 4, 5], [9, 10]]."""
    out: list[list[int]] = []
    for i in sorted(indices):
        if out and i == out[-1][-1] + 1:
            out[-1].append(i)
        else:
            out.append([i])
    return out


def underlined_view(tokens: Sequence[Token], ul: list[bool], mark: list[bool] | None = None) -> str:
    """index:word for underlined words only, '|' where text was skipped, '*' for marked words."""
    parts: list[str] = []
    prev = None
    for t in tokens:
        if not any(ul[t.start:t.end]):
            continue
        if prev is not None and t.index != prev + 1:
            parts.append("|")
        star = "*" if mark is not None and any(mark[t.start:t.end]) else ""
        parts.append(f"{t.index}:{star}{t.text}")
        prev = t.index
    return " ".join(parts)


def read_aloud(text: str, hl: Sequence[bool]) -> str:
    ft = FText(text)
    ft.marks["highlight"] = ["x" if h else None for h in hl]
    return ft.read_aloud()


def _log(stage: str, res, fixes: int = 0, n_examples: int = 0) -> StageLog:
    plan = res.data.get("plan", "") if isinstance(res.data, dict) else ""
    return StageLog(stage, res.backend, res.model, round(res.seconds, 2), res.input_tokens, res.output_tokens,
                    round(res.cost_usd, 6), res.attempts, fixes, str(plan)[:400], n_examples)


def prompt_budget(reg: Registry, cfg: Config, stage: str) -> int:
    """Tokens a prompt may use for `stage`: the backend's context minus room for the answer."""
    b = reg.for_stage(stage)
    ctx = getattr(b, "num_ctx", None) or int(cfg.get("retrieval.max_prompt_tokens", 24000))
    answer = int(getattr(b, "num_predict", 2048) or 2048)
    return max(2000, ctx - answer - 300)


def fit_prompt(build: Callable[[str], Prompt], examples: list[Card], fmt: str, budget: int) -> tuple[Prompt, int]:
    """Render with as many examples as fit in `budget` tokens (dropping the least similar first)."""
    for k in range(len(examples), -1, -1):
        p = build(render_examples(examples[:k], fmt))
        if estimate_tokens(p.system + p.user) <= budget or k == 0:
            if k == 0 and estimate_tokens(p.system + p.user) > budget:
                raise ValueError("the source window alone does not fit the model's context; pick a shorter window")
            return p, k
    raise AssertionError("unreachable")


def cut_window(
    reg: Registry,
    cfg: Config,
    source: str,
    claim: str,
    *,
    start: int = 0,
    end: int | None = None,
    context: str = "",
    examples: list[Card] | None = None,
    do_tag: bool | None = None,
    do_readback: bool | None = None,
    progress: Progress | None = None,
    keep_window: bool = False,
) -> CutResult:
    """Cut the card whose body is source[start:end] (less any unmarked paragraphs at its edges,
    unless keep_window: the eval compares marks against a gold card of exactly this text)."""
    say = progress or (lambda _m: None)
    end = len(source) if end is None else end
    text = source[start:end]
    tokens = tokenize(text)
    n = len(text)
    if not tokens:
        raise ValueError("empty source window")
    d = density(cfg, len(tokens))
    ctx = f"\nArgument context:\n{context}\n" if context else ""
    examples = examples or []
    fmt = cfg.get("retrieval.example_format", "markup")
    do_tag = cfg.get("cut.tag", True) if do_tag is None else do_tag
    do_readback = cfg.get("readback.enabled", True) if do_readback is None else do_readback
    logs: list[StageLog] = []
    warnings: list[str] = []

    # 1. underline; re-runnable with read-back feedback when the card doesn't prove the tag
    def underline(feedback: str = "") -> tuple[list[bool], set[int]]:
        say("underline" + (" (retry)" if feedback else ""))
        fb = (f"A coach read the last version of this card aloud and said: {feedback}\n"
              "Underline again so the card makes sense and proves the claim.\n") if feedback else ""
        p, k = fit_prompt(lambda ex: prompts.render("underline", claim=claim, context=ctx, examples=ex,
                                                    source=indexed_view(tokens), underline_words=d["underline_words"],
                                                    underline_pct=d["underline_pct"], n_words=len(tokens), feedback=fb),
                          examples, fmt, prompt_budget(reg, cfg, "underline"))
        res = reg.call("underline", p, schemas.UNDERLINE, "underline")
        ranges, fixes = clean_ranges(res.data["underline"], len(tokens))
        logs.append(_log("underline", res, fixes, k))
        mask = char_mask_from_ranges(n, tokens, ranges)
        mask, words = topup(mask, {t.index for t in tokens if any(mask[t.start:t.end])})
        if int(cfg.get("cut.underline_fill_gaps", 1)) > 0 and \
                fill_underline_gaps(text, tokens, mask, int(cfg.get("cut.underline_fill_gaps", 1))):
            words = {t.index for t in tokens if any(mask[t.start:t.end])}
        return mask, words

    # 1b. top-up: a thin underline caps what the highlight can pick, and a paragraph with nothing
    # underlined is dropped from the card (marked_edges) or sits in it as padding, so ask for the
    # missing ranges: on a thin card, anywhere; otherwise in the plain paragraphs.
    def topup(mask: list[bool], words: set[int]) -> tuple[list[bool], set[int]]:
        if not cfg.get("cut.underline_topup", False):
            return mask, words
        goal = round(float(cfg.get("cut.topup_goal", 0.75)) * d["underline_words"])
        thin = len(words) < float(cfg.get("cut.topup_below", 0.55)) * d["underline_words"]
        plain = plain_paragraphs(text, tokens, words)
        plain_words = sum(k for _, k in plain)
        if not thin and plain_words < float(cfg.get("cut.topup_plain_share", 0.25)) * len(tokens):
            return mask, words
        say("underline (top-up)")
        why = (f"That is too thin: {len(words)} of {len(tokens)} words. On a card this long this team underlines "
               f"about {goal}, and the highlighting is chosen only from inside the underlining, so a point left "
               "plain can never be read in the round.\n") if thin else ""
        listed = "".join(f'- from word {i}: "{" ".join(t.text for t in tokens[i:i + 8])} …" ({k} words)\n'
                         for i, k in plain[:12])
        plain_txt = ("These paragraphs have nothing underlined yet. Read each one: if it gives a reason the claim "
                     "is true, evidence for it, or what follows from it, underline that clause; leave a paragraph "
                     f"that is only story or background plain.\n{listed}") if plain else ""
        add = max(4, goal - len(words)) if thin else max(8, round(0.2 * plain_words))
        src = " ".join(f"{t.index}:{'*' if t.index in words else ''}{t.text}" for t in tokens)
        p, k = fit_prompt(lambda ex: prompts.render("underline_more", claim=claim, context=ctx, examples=ex, source=src,
                                                    underlined=read_aloud(text, mask) or "(nothing)", why=why,
                                                    plain=plain_txt, add=add),
                          examples, fmt, prompt_budget(reg, cfg, "underline"))
        res = reg.call("underline", p, schemas.UNDERLINE, "underline")
        ranges, fixes = clean_ranges(res.data["underline"], len(tokens))
        logs.append(_log("underline-topup", res, fixes, k))
        added = {i for a, b in ranges for i in range(a, b + 1)} - words
        if not added:
            return mask, words
        allw = words | added
        out = char_mask_from_ranges(n, tokens, [(r[0], r[-1]) for r in _runs(sorted(allw))])
        return [a or b for a, b in zip(out, mask)], allw

    ul, ul_words = underline()

    # 2. highlight (only underlined words are offered); re-runnable with read-back feedback
    def highlight(feedback: str = "") -> list[bool]:
        say("highlight" + (" (retry)" if feedback else ""))
        fb = f"A coach read your last highlighting aloud and said: {feedback}\nFix that.\n" if feedback else ""
        p, k = fit_prompt(lambda ex: prompts.render(cfg.get("cut.highlight_prompt", "highlight"), claim=claim,
                                                    context=ctx, examples=ex,
                                                    underlined=underlined_view(tokens, ul), feedback=fb,
                                                    highlight_words=min(d["highlight_words"], len(ul_words)),
                                                    max_words=min(round(1.3 * d["highlight_words"]), len(ul_words)),
                                                    underlined_count=len(ul_words)),
                          examples, fmt, prompt_budget(reg, cfg, "highlight"))
        res = reg.call("highlight", p, schemas.HIGHLIGHT, "highlight")
        h_ranges, fixes = clean_ranges(res.data["highlight"], len(tokens), allowed=ul_words)
        hl = char_mask_from_ranges(n, tokens, h_ranges)
        parts = res.data.get("partial", [])
        if cfg.get("cut.highlight_from_plan", False):
            # Experimental: highlight the source words that spell out the model's own read-aloud
            # plan (exact words only, in order). Falls back to its indices if the plan doesn't fit.
            whole, partial, coverage = align_read_aloud(str(res.data.get("plan", "")), tokens, ul_words)
            if coverage >= float(cfg.get("cut.plan_min_coverage", 0.8)) and whole:
                ranges = [(w, w) for w in whole]
                hl = char_mask_from_ranges(n, tokens, ranges)
                parts = [{"word": w, "start": len(tokens[w].text) - len(tokens[w].text.lstrip("\"'“‘([—–-")),
                          "end": e} for w, e in partial]
            else:
                fixes += 1
        for part in parts:
            w, a, b = int(part["word"]), int(part["start"]), int(part["end"])
            if w in ul_words and 0 <= a < b <= len(tokens[w].text):
                for i in range(tokens[w].start, tokens[w].end):  # a partial replaces a whole-word highlight
                    hl[i] = False
                apply_partial(hl, tokens, w, a, b)
            else:
                fixes += 1
        logs.append(_log("highlight", res, fixes, k))
        hl = [h and u for h, u in zip(hl, ul)]
        trimmed = None
        if reg.has_stage("trim"):
            if cfg.get("cut.trim_highlight", False):
                trimmed = trim(hl)
            elif float(cfg.get("cut.trim_if_over", 0)) > 0:  # only a highlight far over its target
                want = min(d["highlight_words"], len(ul_words))
                have = sum(1 for t in tokens if any(hl[t.start:t.end]))
                if have > float(cfg.get("cut.trim_if_over", 0)) * want:
                    if cfg.get("cut.trim_over_mode", "rank") == "rank":
                        trimmed, log = rank_trim(reg, text, tokens, hl, claim, want,
                                                 slack=float(cfg.get("cut.trim_rank_slack", 1.2)), say=say)
                        if log is not None:
                            logs.append(log)
                    else:
                        trimmed = trim(hl, prompt=cfg.get("cut.trim_over_prompt", "trim_fluent"), repair=False)
        last["untrimmed"] = finish(hl) if trimmed is not None and trimmed != hl else None
        out = trimmed if trimmed is not None else hl
        # Smooth after the trim, so it only adds subjects and verbs to what is still read: before
        # it, words it added to sentences the trim then dropped stayed underlined, scattered
        # ("_It_ has _an_ estimated range", 2026-11-12).
        if cfg.get("cut.smooth", False) and reg.has_stage("trim"):
            out, log = smooth_highlight(reg, text, tokens, out, ul, ul_words, claim,
                                        share=float(cfg.get("cut.smooth_share", 0.2)), say=say)
            if log is not None:
                logs.append(log)
        return finish(out)

    last: dict[str, list[bool] | None] = {"untrimmed": None}

    def finish(hl: list[bool]) -> list[bool]:
        hl = list(hl)
        if int(cfg.get("cut.underline_fill_gaps", 1)) > 0 and \
                fill_underline_gaps(text, tokens, ul, int(cfg.get("cut.underline_fill_gaps", 1))):
            ul_words.update(t.index for t in tokens if any(ul[t.start:t.end]))
        if cfg.get("cut.acronym_rule", True):
            acronym_initials(text, tokens, hl)
        if cfg.get("cut.drop_repeats", False):
            drop_repeats(text, tokens, hl)
        if cfg.get("cut.dangling_negators", True):
            fix_dangling_negators(text, tokens, hl, ul)
        if cfg.get("cut.restore_subjects", True):
            restore_subjects(text, tokens, hl, ul)
        if cfg.get("cut.finish_sentences", True):
            finish_sentences(text, tokens, hl, ul)
        bridge_gaps(text, tokens, hl, ul, int(cfg.get("cut.bridge_gaps", 0)))
        join_adjacent(text, tokens, hl, ul)
        unread_footnotes(text, tokens, hl)
        tidy_spaces(text, hl)
        return [h and u for h, u in zip(hl, ul)]  # nesting is enforced, not trusted

    # 2b. trim: a deletion-only pass that cuts the highlighting down to this team's short pieces
    def trim(hl: list[bool], prompt: str | None = None, repair: bool = True) -> list[bool]:
        out, log = trim_highlight(reg, cfg, text, tokens, hl, ul_words, claim, examples,
                                  min(d["highlight_words"], len(ul_words)), say=say, prompt=prompt,
                                  force=prompt is not None,
                                  examples_text="" if prompt else None)  # your trim examples are the compressed style
        if log is not None:
            logs.append(log)
            if repair and cfg.get("cut.trim_repair", False) and out != hl:
                out, log = repair_trim(reg, text, tokens, hl, out, claim,
                                       share=float(cfg.get("cut.repair_share", 0.3)), say=say)
                if log is not None:
                    logs.append(log)
        return out

    hl = highlight()

    # 4. tag
    tag_ft: FText | None = None
    if do_tag:
        say("tag")
        p = prompts.render("tag", claim=claim, context=ctx, read_aloud=read_aloud(text, hl) or "(nothing highlighted)",
                           examples=render_tag_examples(examples))
        res = reg.call("tag", p, schemas.TAG, "tag")
        tag_ft = build_tag(strip_stray_reference(res.data["tag"], source + " " + claim),
                           res.data.get("underline", []))
        logs.append(_log("tag", res, 0, len(examples)))

    # 5. read-back: score the read-aloud text against the tag; re-highlight with feedback
    rb: dict[str, Any] | None = None
    history: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if do_readback:
        tag_text = tag_ft.text if tag_ft else claim
        threshold = int(cfg.get("readback.min_score", 4))
        retries = int(cfg.get("readback.max_retries", 2))
        best_cut, best = (ul, ul_words, hl), None

        def judge(cur: list[bool], label: str) -> dict[str, Any]:
            nonlocal best, best_cut
            say(label)
            p = prompts.render("readback", tag=tag_text, read_aloud=read_aloud(text, cur) or "(nothing highlighted)",
                               examples=render_readback_examples(examples))
            res = reg.call("readback", p, schemas.READBACK, "readback")
            logs.append(_log("readback", res))
            scores = {k_: max(1, min(5, int(res.data[k_]))) for k_ in ("grammatical", "coherent", "proves_tag")}
            entry = {**scores, "power_tag": bool(res.data["power_tag"]), "feedback": res.data["feedback"][:500],
                     "min": min(scores.values()), "mean": sum(scores.values()) / 3}
            history.append(entry)
            candidates.append({"label": label, "ul": list(ul), "hl": list(cur), **entry})
            if best is None or (entry["min"], entry["mean"]) > (best["min"], best["mean"]):
                best, best_cut = entry, (ul, ul_words, cur)
            return entry

        entry = judge(hl, "read-back")
        attempt = 0
        while entry["min"] < threshold:
            # The trim may be what made it hard to follow: hear the untrimmed highlight first
            # (one judge call) before re-cutting. The better-scoring version is kept.
            if last["untrimmed"] is not None and cfg.get("readback.untrimmed_fallback", False):
                alt, last["untrimmed"] = last["untrimmed"], None
                if judge(alt, "read-back (untrimmed)")["min"] >= threshold:
                    break
            if attempt >= retries:
                break
            # A card that doesn't prove the tag or doesn't make sense needs a new underline,
            # not just new highlighting (the highlight can only pick from what is underlined).
            if (attempt >= 1 or retries == 1) and cfg.get("readback.reunderline", True) \
                    and min(entry["proves_tag"], entry["coherent"]) <= 2:
                ul, ul_words = underline(feedback=entry["feedback"])
            hl = highlight(feedback=entry["feedback"])
            attempt += 1
            entry = judge(hl, f"read-back (after retry {attempt})")
        (ul, ul_words, hl), rb = best_cut, best
        if rb and rb["min"] < threshold:
            warnings.append(f"read-back below threshold after retries (grammatical {rb['grammatical']}, "
                            f"coherent {rb['coherent']}, proves tag {rb['proves_tag']}): {rb['feedback'][:160]}")
        if rb and rb["power_tag"] and rb["proves_tag"] <= 2:
            warnings.append(f"possible power-tag: the tag may claim more than the highlighted text supports. {rb['feedback'][:160]}")

    # 6. emphasis (after read-back, on the final cut)
    # Boxes go on a few highlighted words only (feedback 2026-11-11: "don't box so much"):
    # at most emphasis_share x the highlighted words, in boxes of one or two words.
    em = [False] * n
    hl_words = {t.index for t in tokens if any(hl[t.start:t.end])}
    if cfg.get("cut.emphasis", True) and hl_words:
        say("emphasis")
        box_words = int(cfg.get("cut.emphasis_box_words", 2))
        max_boxes = max(1, round(float(cfg.get("cut.emphasis_share", 0.15)) * len(hl_words) / 1.5))
        p = prompts.render("emphasis", claim=claim, context=ctx, max_boxes=max_boxes,
                           underlined=underlined_view(tokens, ul, mark=hl))
        res = reg.call("emphasis", p, schemas.EMPHASIS, "emphasis")
        e_ranges, fixes = clean_ranges(res.data["emphasis"], len(tokens), allowed=ul_words)
        capped = cap_emphasis(tokens, e_ranges, hl_words, max_boxes=max_boxes, max_words=box_words,
                              max_total=max(1, round(float(cfg.get("cut.emphasis_share", 0.15)) * len(hl_words))))
        em = [e and u for e, u in zip(char_mask_from_ranges(n, tokens, capped), ul)]
        logs.append(_log("emphasis", res, fixes + (len(e_ranges) != len(capped)), 0))

    # 7. build the card body and verify it against the source. The card runs from its first
    # marked paragraph to its last: unmarked paragraphs at either edge are dropped.
    if cfg.get("cut.drop_unmarked_edges", True) and not keep_window:
        valid = {o - start for o in sentence_starts(source) if start <= o < start + n}
        a, b = marked_edges(text, ul, mid_sentence=starts_mid_sentence(source[:start]), starts=valid)
        if (a, b) != (0, n):
            text, ul, hl, em = text[a:b], ul[a:b], hl[a:b], em[a:b]
            start, n = start + a, b - a
    color = cfg.get("render.highlight_color", "yellow")
    shrink = cfg.get("render.shrink_unread_pt")
    body = FText(text)
    for i, ch in enumerate(text):
        if ch == "\n":
            continue
        body.marks["underline"][i] = ul[i]
        body.marks["emphasis"][i] = em[i]
        body.marks["highlight"][i] = color if hl[i] else None
        if shrink and not ul[i]:
            body.marks["size"][i] = int(shrink * 2)
    report = verify_body(body, source, offset=start, policy=IntegrityPolicy.from_config(cfg))
    return CutResult(body=body, offset=start, tag=tag_ft, readback=rb, readback_history=history,
                     candidates=candidates,
                     example_ids=[c.meta.get("id", "") for c in examples], logs=logs,
                     warnings=warnings + report.warnings)


def trim_highlight(reg: Registry, cfg: Config, text: str, tokens: Sequence[Token], hl: list[bool],
                   ul_words: set[int], claim: str, examples: list[Card], target: int, *,
                   say: Progress | None = None, prompt: str | None = None,
                   examples_text: str | None = None, force: bool = False) -> tuple[list[bool], StageLog | None]:
    """Deletion-only: keep a subset of the highlighted words (about `target` of them).
    Returns the new highlight mask and the stage log (None when no call was needed)."""
    hw = [t.index for t in tokens if any(hl[t.start:t.end])]
    longest = max((len(r) for r in _runs(hw)), default=0)
    if len(hw) < 6 or (not force and len(hw) <= target and longest <= 4):
        return hl, None
    (say or (lambda _m: None))("trim highlight")
    view = " | ".join(" ".join(f"{i}:{tokens[i].text}" for i in r) for r in _runs(hw))
    ex = render_trim_examples(examples) if examples_text is None else examples_text
    p = prompts.render(prompt or cfg.get("cut.trim_prompt", "trim"), claim=claim, examples=ex, count=len(hw),
                       highlighted=view, read_aloud=read_aloud(text, hl), target=target)
    res = reg.call("trim", p, schemas.TRIM, "trim")
    allowed = set(hw)
    keep = {int(i) for i in res.data["keep"] if isinstance(i, int) and int(i) in allowed}
    fixes = len(res.data["keep"]) - len(keep)
    if len(keep) < max(3, round(0.4 * target)):  # cut too deep to still prove anything: keep the original
        return hl, _log("trim", res, fixes + 1)
    fixes += drop_stranded(tokens, hw, keep)
    fixes += keep_negators(tokens, hw, keep)
    out = list(hl)
    for i in hw:
        if i not in keep:
            for c in range(tokens[i].start, tokens[i].end):
                out[c] = False
    return out, _log("trim", res, fixes)


def repair_trim(reg: Registry, text: str, tokens: Sequence[Token], before: list[bool], after: list[bool],
                claim: str, *, share: float = 0.3, say: Progress | None = None) -> tuple[list[bool], StageLog | None]:
    """After a trim, add back a few of the dropped words so each kept piece reads as a clause
    ("obligations made up 9.3 percent", not "obligations 9.3 percent"). Add-only, and only words
    that were highlighted before the trim, at most `share` of the words kept."""
    was = [t.index for t in tokens if any(before[t.start:t.end])]
    now = {t.index for t in tokens if any(after[t.start:t.end])}
    dropped = set(was) - now
    if not dropped or not now:
        return after, None
    (say or (lambda _m: None))("repair trim")
    view = " | ".join(" ".join(f"{i}:{'*' if i in now else ''}{tokens[i].text}" for i in r) for r in _runs(was))
    max_add = max(2, round(share * len(now)))
    p = prompts.render("repair", claim=claim, view=view, read_aloud=read_aloud(text, after), max_add=max_add)
    res = reg.call("trim", p, schemas.REPAIR, "repair")
    raw = [i for i in res.data.get("add", []) if isinstance(i, int) and not isinstance(i, bool)]
    add = [i for i in dict.fromkeys(raw) if i in dropped][:max_add]
    out = list(after)
    for i in add:
        for c in range(tokens[i].start, tokens[i].end):
            out[c] = before[c]  # the word's own pre-trim highlight (a partial stays partial)
    # a gap between two highlighted words is highlighted again if it was before the trim
    n = len(text)
    i = 0
    while i < n:
        if not text[i].strip() and before[i] and not out[i]:
            j = i
            while j < n and not text[j].strip():
                j += 1
            if i > 0 and out[i - 1] and j < n and out[j]:
                for k in range(i, j):
                    out[k] = before[k]
            i = j
        else:
            i += 1
    return out, _log("repair", res, len(raw) - len(add))


def rank_trim(reg: Registry, text: str, tokens: Sequence[Token], hl: list[bool], claim: str, target: int, *,
              slack: float = 1.2, say: Progress | None = None) -> tuple[list[bool], StageLog | None]:
    """Cut a highlight far over its target by whole read sentences: the model ranks the sentences
    the card reads aloud, and the code keeps them, most needed first, up to slack x target words
    (always at least the first). Kept sentences are untouched, so the read stays fluent; the local
    model asked for word indices kept nearly everything (117 of a 49-word target, 2026-11-12)."""
    sid = _sentence_ids(tokens)
    groups: dict[int, list[int]] = {}
    for t in tokens:
        if any(hl[t.start:t.end]):
            groups.setdefault(sid[t.index], []).append(t.index)
    sents = list(groups.values())
    if len(sents) < 2:
        return hl, None
    (say or (lambda _m: None))("trim highlight")

    def said(g: list[int]) -> str:
        m = [False] * len(text)
        for c in range(tokens[g[0]].start, tokens[g[-1]].end):
            m[c] = hl[c]
        return read_aloud(text, m)

    listing = "\n".join(f"{k}: {said(g)} ({len(g)} words)" for k, g in enumerate(sents))
    p = prompts.render("trim_rank", claim=claim, sentences=listing, count=len(sents), target=target)
    res = reg.call("trim", p, schemas.RANK, "trim")
    raw = [k for k in res.data.get("order", []) if isinstance(k, int) and not isinstance(k, bool)]
    order = list(dict.fromkeys(k for k in raw if 0 <= k < len(sents)))
    fixes = len(raw) - len(order)
    order += [k for k in range(len(sents)) if k not in order]
    keep: set[int] = set()
    total = 0
    for k in order:
        if not keep or total + len(sents[k]) <= slack * target:
            keep.add(k)
            total += len(sents[k])
    out = list(hl)
    for k, g in enumerate(sents):
        if k not in keep:
            for c in range(tokens[g[0]].start, tokens[g[-1]].end):
                out[c] = False
    return out, _log("trim", res, fixes)


def plain_paragraphs(text: str, tokens: Sequence[Token], words: set[int], min_words: int = 8) -> list[tuple[int, int]]:
    """(first word index, word count) of each paragraph of at least min_words with no word in `words`."""
    out: list[tuple[int, int]] = []
    para: list[int] = []
    for t in list(tokens) + [None]:
        if t is None or (para and "\n" in text[tokens[para[-1]].end:t.start]):
            if len(para) >= min_words and not words & set(para):
                out.append((para[0], len(para)))
            para = []
        if t is not None:
            para.append(t.index)
    return out


def _sentence_ids(tokens: Sequence[Token]) -> list[int]:
    out, sid = [], 0
    for t in tokens:
        out.append(sid)
        if t.text.rstrip("”’\"')]").endswith((".", "!", "?")):
            sid += 1
    return out


def smooth_highlight(reg: Registry, text: str, tokens: Sequence[Token], hl: list[bool], ul: list[bool],
                     ul_words: set[int], claim: str, *, share: float = 0.2,
                     say: Progress | None = None) -> tuple[list[bool], StageLog | None]:
    """Add-only: where a highlighted piece has no subject or no verb ("possesses 2,000 weapons"),
    add the fewest words of the same sentence that fix it, underlined or not (an added word is
    underlined too). At most `share` of the highlighted words. Changes `ul`/`ul_words` in place."""
    now = {t.index for t in tokens if any(hl[t.start:t.end])}
    if len(now) < 4:
        return hl, None
    sid = _sentence_ids(tokens)
    lit = {sid[i] for i in now}
    offered = [t.index for t in tokens if sid[t.index] in lit]
    parts, prev = [], None
    for i in offered:
        if prev is not None and sid[i] != sid[prev]:
            parts.append("|")
        parts.append(f"{i}:{'*' if i in now else ''}{tokens[i].text}")
        prev = i
    max_add = max(2, round(share * len(now)))
    (say or (lambda _m: None))("smooth")
    p = prompts.render("smooth", claim=claim, view=" ".join(parts), read_aloud=read_aloud(text, hl), max_add=max_add)
    res = reg.call("trim", p, schemas.REPAIR, "smooth")
    raw = [i for i in res.data.get("add", []) if isinstance(i, int) and not isinstance(i, bool)]
    ok = set(offered) - now
    add = [i for i in dict.fromkeys(raw) if i in ok][:max_add]
    if not add:
        return hl, _log("smooth", res, len(raw))
    out = list(hl)
    allw = sorted(now | set(add))
    runs = [r for r in _runs(allw) if any(i in add for i in r)]
    for r in runs:  # highlight the added words and the spaces joining them to their neighbors
        a, b = tokens[r[0]].start, tokens[r[-1]].end
        for c in range(a, b):
            out[c] = True
    for i in add:
        for c in range(tokens[i].start, tokens[i].end):
            ul[c] = True
        ul_words.add(i)
    for r in _runs(sorted(ul_words)):  # underline the gaps an added word closed
        if any(i in add for i in r):
            for c in range(tokens[r[0]].start, tokens[r[-1]].end):
                ul[c] = True
    return out, _log("smooth", res, len(raw) - len(add))


_THATS = re.compile(r"\s*(?:-{2,3}|—|–|,)?\s*that['’]?s\s+(?:the\s+)?((?:[A-Z][\w'’.-]*\s*)+?)\s*([.!]?)\s*$")


def strip_stray_reference(tag: str, known: str) -> str:
    """Drop a trailing "---that's Frolova" naming someone the article (or the claim) never
    mentions: the model copies these pointers from the example tags."""
    m = _THATS.search(tag)
    if not m:
        return tag
    name = m.group(1).split()[-1].strip(".'’")
    if name and re.search(rf"\b{re.escape(name)}\b", known, re.I):
        return tag
    head = tag[:m.start()].rstrip(" ,-—–")
    end = tag.rstrip()[-1:]
    return head + (end if end in ".!" and not head.endswith((".", "!", "?")) else "")


MARKDOWN_EMPHASIS = re.compile(r"(?<![\w*_])[*_]{1,2}([^*_\n]+?)[*_]{1,2}(?![\w*_])")


def build_tag(text: str, underline_phrases: list[str]) -> FText:
    """Tag as Heading 4 text (13 pt) with the model's key phrases underlined where they occur."""
    text = " ".join(text.replace("\n", " ").split())
    text = MARKDOWN_EMPHASIS.sub(r"\1", text)          # models sometimes write _underline_ or **bold** into the tag
    underline_phrases = [MARKDOWN_EMPHASIS.sub(r"\1", ph) for ph in underline_phrases]
    tag = FText(text)
    for i in range(len(text)):
        tag.marks["size"][i] = 26
    used = 0
    for phrase in underline_phrases:
        phrase = phrase.strip()
        if not phrase:
            continue
        pos = text.find(phrase, used)
        if pos < 0:
            pos = text.lower().find(phrase.lower())
        if pos >= 0:
            tag.set_span("underline", pos, pos + len(phrase))
    return tag
