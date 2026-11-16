"""Character-level comparison of a generated cut against the human cut.

All counts use non-whitespace characters, so a team's habit of highlighting the spaces
between words doesn't move the score.
"""
from __future__ import annotations

from typing import Any, Sequence

from cardcutter.card import FText


def _on(v: Any) -> bool:
    return v is not None and v is not False and v != 0


def prf(pred: Sequence[Any], gold: Sequence[Any], text: str) -> dict[str, float]:
    tp = fp = fn = 0
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        p, g = _on(pred[i]), _on(gold[i])
        tp += p and g
        fp += p and not g
        fn += g and not p
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"p": precision, "r": recall, "f1": f1}


def hl_density(ft: FText) -> float:
    idx = [i for i, ch in enumerate(ft.text) if not ch.isspace()]
    return sum(1 for i in idx if ft.marks["highlight"][i] is not None) / (len(idx) or 1)


def compare(pred: FText, gold: FText) -> dict[str, float]:
    assert pred.text == gold.text, "eval compares cuts of the same text"
    out: dict[str, float] = {}
    for attr in ("underline", "highlight", "emphasis"):
        for k, v in prf(pred.marks[attr], gold.marks[attr], gold.text).items():
            out[f"{attr}_{k}"] = v
    out["hl_density_pred"] = hl_density(pred)
    out["hl_density_gold"] = hl_density(gold)
    out["hl_density_err_pp"] = 100 * abs(out["hl_density_pred"] - out["hl_density_gold"])
    return out
