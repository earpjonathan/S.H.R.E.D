"""Run the cutter on held-out corpus cards and score it against the human cuts.

For each card: strip the formatting, give the cutter the body text plus the original
tag as the claim, and compare. Results go to eval/reports/<timestamp>.md (metrics only,
no evidence text) and the generated cards to data/eval_runs/<timestamp>/ (git-ignored).
"""
from __future__ import annotations

import json
import statistics
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from cardcutter.card import Card, FText
from cardcutter.config import Config
from cardcutter.cutter.pipeline import cut_window
from cardcutter.eval.metrics import compare, prf
from cardcutter.llm import prompts
from cardcutter.llm.base import BackendError, BackendUnavailable
from cardcutter.llm.cost import SpendingCapExceeded
from cardcutter.llm.registry import Registry
from cardcutter.verify.integrity import IntegrityError

CUT_STAGES = ("underline", "highlight", "emphasis")
METRICS = ("underline_f1", "underline_p", "underline_r", "highlight_f1", "highlight_p", "highlight_r",
           "emphasis_f1", "hl_density_err_pp", "readback_mean", "readback_min")


@dataclass
class EvalRun:
    started: str
    config: dict[str, Any]
    rows: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        ok = [r for r in self.rows if r.get("integrity_ok")]
        s: dict[str, Any] = {"cards": len(self.rows), "integrity_pass": len(ok),
                             "integrity_pass_rate": len(ok) / len(self.rows) if self.rows else 0.0}
        for m in METRICS:
            vals = [r[m] for r in ok if m in r]
            s[m] = statistics.fmean(vals) if vals else None
        for k in ("seconds", "cost_usd", "input_tokens", "output_tokens"):
            vals = [r[k] for r in self.rows if k in r]
            s[f"{k}_per_card"] = statistics.fmean(vals) if vals else None
        s["errors"] = sum(1 for r in self.rows if r.get("error"))
        rb = [r for r in ok if r.get("readback_min") is not None]
        threshold = self.config.get("readback_threshold", 4)
        s["readback_pass_rate"] = (sum(1 for r in rb if r["readback_min"] >= threshold) / len(rb)) if rb else None
        s["power_tag_warnings"] = sum(1 for r in ok if r.get("power_tag"))
        s["readback_retries_per_card"] = statistics.fmean(r.get("readback_retries", 0) for r in rb) if rb else None
        return s


def candidate_scores(c: dict[str, Any], gold: FText, res: Any) -> dict[str, Any]:
    text = gold.text
    ul, hl = prf(c["ul"], gold.marks["underline"], text), prf(c["hl"], gold.marks["highlight"], text)
    idx = [i for i, ch in enumerate(text) if not ch.isspace()]
    final = res.body.marks
    chosen = all(bool(final["underline"][i]) == bool(c["ul"][i]) and
                 (final["highlight"][i] is not None) == bool(c["hl"][i]) for i in idx)
    return {"label": c["label"], "min": c["min"], "mean": c["mean"], "power_tag": c["power_tag"],
            "underline_f1": ul["f1"], "highlight_f1": hl["f1"], "highlight_p": hl["p"], "highlight_r": hl["r"],
            "hl_density": sum(1 for i in idx if c["hl"][i]) / (len(idx) or 1), "chosen": chosen}


def eval_context(gold: Card, prev: Card | None) -> str:
    """What a debater cutting this card would know: where it goes in the file and the tag of
    the card before it (many tags, like "And HIMARs.", only make sense after the previous one)."""
    m = gold.meta
    parts = []
    path = " > ".join(x for x in (m.get("pocket"), m.get("hat"), m.get("block")) if x)
    if path:
        parts.append(f"Where this card goes in the file: {path}")
    if prev is not None and prev.meta.get("file") == m.get("file") and prev.meta.get("block") == m.get("block"):
        parts.append(f"The card before it in this block is tagged: {' '.join(prev.tag.text.split())}")
        if len(gold.tag.text.split()) <= 6:
            parts.append("This card's tag is short and continues that argument; read the claim in that light.")
    return "\n".join(parts)


def run_eval(
    cfg: Config,
    reg: Registry,
    cards: list[Card],
    *,
    progress: Callable[[str], None] = print,
    run_dir: Path | None = None,
    retriever: Any = None,
    corpus: list[Card] | None = None,
) -> EvalRun:
    started = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = EvalRun(
        started=started,
        config={
            "preset": reg.preset,
            "stages": reg.describe(CUT_STAGES),
            "density": cfg.get("cut.density"),
            "prompts": {s: prompts.fingerprint(s) for s in CUT_STAGES},
            "think": {name: cfg.get(f"llm.backends.{name}.think") for name in {reg.backend_name(s) for s in CUT_STAGES}},
            "retrieval": (f"k={retriever.k}, {cfg.get('retrieval.example_format', 'markup')}, "
                          f"embed={retriever.embedder.model}") if retriever else "off",
            "tag": bool(cfg.get("cut.tag", True)),
            "readback": bool(cfg.get("readback.enabled", True)),
            "readback_threshold": int(cfg.get("readback.min_score", 4)),
            "file_context": bool(cfg.get("eval.file_context", True)) and corpus is not None,
        },
    )
    for s_ in ("tag", "readback"):
        try:
            run.config["stages"][s_] = reg.describe((s_,))[s_]
            run.config["prompts"][s_] = prompts.fingerprint(s_)
        except KeyError:
            pass
    prev_of: dict[str, Card] = {}
    if corpus is not None and run.config["file_context"]:
        for a, b in zip(corpus, corpus[1:]):
            prev_of[b.meta["id"]] = a
    run_dir = run_dir or (cfg.path_of("eval.runs_dir") / started)
    run_dir.mkdir(parents=True, exist_ok=True)
    out_cards = open(run_dir / "cards.jsonl", "w", encoding="utf-8")
    t_all = time.time()
    try:
        for k, gold in enumerate(cards, 1):
            source = gold.body.text
            claim = gold.tag.text.strip()
            row: dict[str, Any] = {"id": gold.meta["id"], "words": len(source.split())}
            t0 = time.time()
            stage_times: list[str] = []

            def say(stage: str) -> None:
                stage_times.append(stage)
                progress(f"[{k}/{len(cards)}] {gold.meta['id']}: {stage}…")

            try:
                examples = retriever.examples(claim, source) if retriever else []
                ctx = eval_context(gold, prev_of.get(gold.meta["id"])) if run.config["file_context"] else ""
                res = cut_window(reg, cfg, source, claim, context=ctx, examples=examples, progress=say,
                                 keep_window=True)
                row.update(compare(res.body, gold.body))
                row["integrity_ok"] = True
                row["warnings"] = res.warnings
                row["stages"] = res.log_dicts()
                row["examples"] = res.example_ids
                row["tag_human"] = claim
                row["tag_generated"] = res.tag.text if res.tag else None
                if res.readback:
                    row["readback_min"] = res.readback["min"]
                    row["readback_mean"] = res.readback["mean"]
                    row["readback"] = {k: res.readback[k] for k in ("grammatical", "coherent", "proves_tag")}
                    row["power_tag"] = res.readback["power_tag"]
                    row["readback_retries"] = len(res.readback_history) - 1
                # every version the judge heard, scored against the human cut, so selection
                # rules can be compared offline on the same cuts
                row["candidates"] = [candidate_scores(c, gold.body, res) for c in res.candidates]
                pred = Card(tag=res.tag or gold.tag, cite=gold.cite, body=res.body,
                            meta={"id": gold.meta["id"], "eval_run": started, "tag_human": claim})
                out_cards.write(json.dumps(pred.to_json(), ensure_ascii=False) + "\n")
                out_cards.flush()
            except IntegrityError as e:
                row["integrity_ok"] = False
                row["error"] = f"integrity: {'; '.join(e.problems)[:300]}"
            except (BackendUnavailable, SpendingCapExceeded):
                raise
            except BackendError as e:
                row["integrity_ok"] = False
                row["error"] = f"backend: {str(e)[:300]}"
            except Exception as e:  # keep going; record the failure
                row["integrity_ok"] = False
                row["error"] = f"{type(e).__name__}: {str(e)[:200]}"
                row["trace"] = traceback.format_exc()[-1500:]
            row["seconds"] = round(time.time() - t0, 2)
            stages = row.get("stages", [])
            row["cost_usd"] = round(sum(s["cost_usd"] for s in stages), 6)
            row["input_tokens"] = sum(s["input_tokens"] for s in stages)
            row["output_tokens"] = sum(s["output_tokens"] for s in stages)
            run.rows.append(row)
            f1 = f"ul F1 {row['underline_f1']:.2f}, hl F1 {row['highlight_f1']:.2f}" if row.get("integrity_ok") else row.get("error", "")[:80]
            progress(f"[{k}/{len(cards)}] done in {row['seconds']:.0f}s — {f1} — spent ${reg.costs.spent_usd:.4f}")
            (run_dir / "rows.json").write_text(json.dumps(run.rows, indent=1), encoding="utf-8")
    finally:
        out_cards.close()
        run.config["wall_seconds"] = round(time.time() - t_all, 1)
    return run


def _fmt(v: Any, pct: bool = True) -> str:
    if v is None:
        return "-"
    return f"{100 * v:.1f}" if pct else f"{v:.2f}"


def write_report(cfg: Config, run: EvalRun) -> Path:
    s = run.summary()
    d = cfg.path_of("eval.reports_dir")
    d.mkdir(parents=True, exist_ok=True)
    md = [
        f"# Eval {run.started}" + (f": {run.config['label']}" if run.config.get("label") else ""),
        "",
        f"- Split: {run.config.get('split', 'all')}; config overrides: {run.config.get('overrides') or 'none'}",
        f"- Preset: `{run.config['preset']}`, density `{run.config['density']}`",
        "- Stages: " + ", ".join(f"{k} → {v}" for k, v in run.config["stages"].items()),
        f"- Prompt versions: {run.config['prompts']}",
        f"- Thinking: {run.config['think']}",
        f"- Retrieval: {run.config.get('retrieval', 'off')}; tag stage: {run.config.get('tag', False)}; "
        f"read-back: {run.config.get('readback', False)}; file context: {run.config.get('file_context', False)}",
        f"- Cards: {s['cards']}, wall time {run.config.get('wall_seconds', 0) / 60:.1f} min",
        "",
        "| metric | value |",
        "|---|---|",
        f"| integrity pass rate | {_fmt(s['integrity_pass_rate'])}% ({s['integrity_pass']}/{s['cards']}) |",
        f"| underline F1 (P / R) | {_fmt(s['underline_f1'])} ({_fmt(s['underline_p'])} / {_fmt(s['underline_r'])}) |",
        f"| highlight F1 (P / R) | {_fmt(s['highlight_f1'])} ({_fmt(s['highlight_p'])} / {_fmt(s['highlight_r'])}) |",
        f"| emphasis F1 | {_fmt(s['emphasis_f1'])} |",
        f"| highlight density error (pp) | {_fmt(s['hl_density_err_pp'], pct=False)} |",
        f"| seconds per card | {_fmt(s['seconds_per_card'], pct=False)} |",
        f"| tokens per card (in / out) | {s['input_tokens_per_card'] or 0:.0f} / {s['output_tokens_per_card'] or 0:.0f} |",
        f"| cost per card | ${s['cost_usd_per_card'] or 0:.4f} |",
        f"| read-back score, mean of 3 criteria (1–5) | {_fmt(s.get('readback_mean'), pct=False)} |",
        f"| read-back pass rate (every criterion ≥ {run.config.get('readback_threshold', 4)}) | "
        f"{_fmt(s.get('readback_pass_rate')) if s.get('readback_pass_rate') is not None else '-'}% |",
        f"| read-back retries per card | {_fmt(s.get('readback_retries_per_card'), pct=False)} |",
        f"| power-tag warnings | {s.get('power_tag_warnings', 0)} |",
        "",
        "F1, precision and recall are character-level (non-whitespace) against the human cut, in %.",
        "",
        "## Per card",
        "",
        "| card | words | ul F1 | hl F1 | em F1 | density err | read-back | s | $ | note |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in run.rows:
        if r.get("integrity_ok"):
            md.append(f"| {r['id']} | {r['words']} | {_fmt(r['underline_f1'])} | {_fmt(r['highlight_f1'])} | "
                      f"{_fmt(r['emphasis_f1'])} | {r['hl_density_err_pp']:.1f} | "
                      f"{_fmt(r.get('readback_mean'), pct=False)} | {r['seconds']:.0f} | {r['cost_usd']:.4f} | "
                      f"{'; '.join(r.get('warnings', []))[:60]} |")
        else:
            md.append(f"| {r['id']} | {r['words']} | - | - | - | - | - | {r['seconds']:.0f} | {r['cost_usd']:.4f} | "
                      f"FAILED: {r.get('error', '')[:80]} |")
    path = d / f"{run.started}.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    (d / f"{run.started}.json").write_text(json.dumps({"config": run.config, "summary": s}, indent=1), encoding="utf-8")
    return path


def compare_reports(cfg: Config) -> str:
    """One table comparing every saved eval run (backend, model, quality, speed, cost)."""
    d = cfg.path_of("eval.reports_dir")
    lines = ["| run | label | split | retrieval | n | integrity | ul F1 (P/R) | hl F1 (P/R) | em F1 | density err | read-back | s/card | $/card |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for p in sorted(d.glob("*.json")):
        o = json.loads(p.read_text())
        s, c = o["summary"], o["config"]
        lines.append(f"| {p.stem} | {c.get('label', '')} | {c.get('split', '')} | {c.get('retrieval', 'off')} | {s['cards']} | "
                     f"{_fmt(s['integrity_pass_rate'])}% | {_fmt(s['underline_f1'])} ({_fmt(s['underline_p'])}/{_fmt(s['underline_r'])}) | "
                     f"{_fmt(s['highlight_f1'])} ({_fmt(s['highlight_p'])}/{_fmt(s['highlight_r'])}) | {_fmt(s['emphasis_f1'])} | "
                     f"{_fmt(s['hl_density_err_pp'], pct=False)} | {_fmt(s.get('readback_mean'), pct=False)} | "
                     f"{_fmt(s['seconds_per_card'], pct=False)} | {s['cost_usd_per_card'] or 0:.4f} |")
    return "\n".join(lines)
