"""Author's-view check: does the article's author actually hold the view the card is read for?

The cut stages only see the card's window, so a passage where an author sets up a view in
order to reject it ("critics argue X ... we found no evidence of X") can produce a clean card
that misrepresents them. This check shows the model the card in the context of the article's
framing (opening, conclusion-type sentences, ending) and asks for the author's own position.

Verdicts: "endorses" (fine), "qualifies" (the author adds a limit the card leaves out), and
"disputes" (the author argues against the card's claim). The model must quote the author's
conclusion; the quote is shown only if it appears in the article word for word.
"""
from __future__ import annotations

import re
from typing import Any

from cardcutter.ingest.source import Document
from cardcutter.llm import prompts, schemas
from cardcutter.llm.registry import Registry

CARD_START, CARD_END = "=== CARD STARTS ===", "=== CARD ENDS ==="
_BACK_MATTER = re.compile(r"^\s*(\d+\.?\s*)?(references|bibliography|works cited|literature cited|notes|"
                          r"acknowledg(e)?ments?)\s*:?\s*$", re.I)
_CONCLUSION_CUE = re.compile(
    r"\b(we|i) (find|found|conclude|argue|show|suggest|recommend|propose)\b|\bour (results|findings|study|analysis|"
    r"data)\b|\b(in conclusion|to conclude|in sum|overall|taken together)\b|\bth(is|e) (paper|article|study|report) "
    r"(argues|shows|finds|found|concludes|suggests)\b|\bno evidence\b|\bunjustified\b|\bwe (do not|don't) (think|believe)\b",
    re.I)


def _body_end(paras: list[str]) -> int:
    """Index where back matter (references, acknowledgements) begins, or len(paras)."""
    for i in range(len(paras) // 3, len(paras)):
        if _BACK_MATTER.match(paras[i]):
            return i
    return len(paras)


def _clip(p: str, n: int) -> str:
    w = p.split()
    return p if len(w) <= n else " ".join(w[:n]) + " …"


def article_frame(doc: Document, start_para: int, end_para: int, max_words: int = 2200) -> str:
    """The article as the check sees it: all of it when short, otherwise the opening, the card
    with a paragraph either side, conclusion-type paragraphs, and the ending."""
    paras = doc.paragraphs
    end = max(_body_end(paras), end_para + 1)
    total = sum(len(p.split()) for p in paras[:end])
    if total <= max_words:
        keep = set(range(end))
    else:
        keep = set(range(min(3, end))) | set(range(max(0, start_para - 1), min(end, end_para + 2)))
        keep |= set(range(max(0, end - 4), end))
        used = sum(len(_clip(paras[i], 220).split()) for i in keep)
        cues = [i for i in range(end) if i not in keep and _CONCLUSION_CUE.search(paras[i])]
        # later conclusion-type paragraphs first: that is usually where the author lands
        for i in sorted(cues, key=lambda i: -i):
            n = len(_clip(paras[i], 220).split())
            if used + n > max_words:
                continue
            keep.add(i)
            used += n
    lines: list[str] = []
    prev = -1
    for i in sorted(keep):
        if i > prev + 1:
            lines.append("…")
        if i == start_para:
            lines.append(CARD_START)
        lines.append(paras[i] if start_para <= i <= end_para else _clip(paras[i], 220))
        if i == end_para:
            lines.append(CARD_END)
        prev = i
    if prev < len(paras) - 1:
        lines.append("…")
    return "\n\n".join(lines)


def _norm(s: str) -> str:
    s = s.lower().translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"}))
    return " ".join(re.sub(r"[^\w'\"%-]+", " ", s).split())


def quote_in_text(quote: str, text: str) -> bool:
    q = _norm(quote).strip(" .\"'")
    return len(q.split()) >= 4 and q in _norm(text)


def check_author_view(reg: Registry, doc: Document, start_para: int, end_para: int, tag: str,
                      read_aloud: str, max_words: int = 2200) -> tuple[dict[str, Any], Any]:
    """Returns (verdict dict, LLM result for logging). The dict has verdict, reason, and
    author_conclusion (empty unless the quote was found in the article)."""
    p = prompts.render("intent", tag=tag, read_aloud=read_aloud or "(nothing highlighted)",
                       article=article_frame(doc, start_para, end_para, max_words))
    res = reg.call("intent", p, schemas.INTENT, "intent")
    quote = " ".join(str(res.data.get("author_conclusion", "")).split())
    ok = quote_in_text(quote, doc.text)
    return {"verdict": res.data["verdict"], "reason": " ".join(str(res.data.get("reason", "")).split())[:240],
            "author_conclusion": quote if ok else "", "quote_found": ok}, res


def warning_for(view: dict[str, Any] | None) -> str | None:
    if not view or view.get("verdict") == "endorses":
        return None
    said = f" Their conclusion: “{view['author_conclusion']}”" if view.get("author_conclusion") else ""
    why = f" ({view['reason']})" if view.get("reason") else ""
    if view["verdict"] == "disputes":
        return ("author's view: the author argues against this card's claim, so reading it as their view would "
                f"misrepresent them.{said}{why}")
    return f"author's view: the author limits this claim in a way the card leaves out.{said}{why}"
