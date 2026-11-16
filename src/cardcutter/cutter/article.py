"""Cut finished cards from a whole document: locate -> cut each candidate -> tag -> cite."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from cardcutter.card import Card, FText
from cardcutter.cite.extract import resolve_meta
from cardcutter.cite.format import build_cite
from cardcutter.cite.meta import CiteMeta
from cardcutter.config import Config
from cardcutter.cutter.intent import check_author_view, warning_for
from cardcutter.cutter.locate import Candidate, locate
from cardcutter.cutter.pipeline import CutResult, _log, cut_window
from cardcutter.ingest.source import Document
from cardcutter.llm.base import BackendError
from cardcutter.llm.registry import Registry


@dataclass
class Draft:
    card: Card
    candidate: Candidate
    result: CutResult
    cite_missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    author_view: dict[str, Any] | None = None   # from the author's-view check (cutter/intent.py)
    claim_check: Any = None                     # auto mode's ClaimCheck (search/auto.py)

    @property
    def warnings(self) -> list[str]:
        w = list(self.result.warnings)
        if self.cite_missing:
            w.append("cite is missing: " + ", ".join(self.cite_missing) + " (fill these in; nothing was invented)")
        part = self.claim_check.warning() if hasattr(self.claim_check, "warning") else ""
        if part:
            w.append(part)
        return w


def cut_document(
    reg: Registry,
    cfg: Config,
    doc: Document,
    claim: str,
    *,
    context: str = "",
    candidates: int = 1,
    retriever: Any = None,
    cite_overrides: dict[str, Any] | None = None,
    cite_format: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[Draft]:
    say = progress or (lambda _m: None)
    cands = locate(reg, cfg, doc, claim, context=context, k=max(candidates, 1), progress=say)[:candidates]

    meta, notes = resolve_meta(doc, reg, extract_quals=bool(cfg.get("cite.extract_quals", True)), progress=say)
    if "sponsored" in doc.flags:
        notes = notes + ["sponsored content: the page was produced for an advertiser; check who is behind it "
                         "before reading it as independent evidence"]
    if cite_overrides:
        meta = meta.override(**cite_overrides)
    cite = build_cite(meta, cite_format or cfg.get("cite.default_format", "inline"),
                      cutter=cfg.get("cite.cutter_name", ""), marker=cfg.get("cite.missing_marker", "[{field}?]"))

    drafts: list[Draft] = []
    source = doc.text
    for n, cand in enumerate(cands, 1):
        tagger = (lambda m, n=n: say(f"candidate {n}/{len(cands)}: {m}")) if len(cands) > 1 else say
        window = source[cand.char_start:cand.char_end]
        examples = retriever.examples(claim, window) if retriever else []
        res = cut_window(reg, cfg, source, claim, start=cand.char_start, end=cand.char_end,
                         context=context, examples=examples, progress=tagger)
        view = None
        if cfg.get("cut.author_check", True) and reg.has_stage("intent"):
            tagger("checking the author's own view")
            try:
                view, llm = check_author_view(reg, doc, cand.start_para, cand.end_para,
                                              res.tag.text if res.tag else claim, res.body.read_aloud(),
                                              int(cfg.get("cut.author_check_words", 2200)))
                res.logs.append(_log("intent", llm))
                if (w := warning_for(view)):
                    res.warnings.append(w)
            except (BackendError, KeyError, ValueError) as e:
                res.warnings.append(f"the author's-view check didn't run ({type(e).__name__}); check the article's conclusion yourself")
        card = Card(
            tag=res.tag or FText(claim),
            cite=cite.cite,
            body=res.body,
            meta={"source": doc.origin, "offset": res.offset, "claim": claim, "rationale": cand.rationale,
                  "paragraphs": [cand.start_para, cand.end_para], "cite_meta": meta.to_json(),
                  "readback": res.readback, "author_view": view, "warnings": res.warnings},
        )
        drafts.append(Draft(card, cand, res, cite.missing, notes, view))
    return drafts


def meta_from_options(**kw: Any) -> dict[str, Any]:
    """CLI/UI overrides -> CiteMeta fields (authors and quals accept ';'-separated strings)."""
    out: dict[str, Any] = {}
    for k, v in kw.items():
        if v in (None, ""):
            continue
        if k in ("authors", "quals") and isinstance(v, str):
            out[k] = [x.strip() for x in v.split(";") if x.strip()]
        elif k in CiteMeta.__dataclass_fields__:
            out[k] = v
    return out
