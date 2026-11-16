"""Word tokenization with stable indices and exact round-trip.

The LLM only ever sees `index:word` and answers with index ranges. A token is a maximal
run of non-whitespace characters, except that dashes used as punctuation (em dash,
en dash, and `--`/`---`) are their own tokens, so "existential---only" is three words.
Whitespace (including non-breaking spaces) is never part of a token; soft hyphens and
zero-width characters stay inside the word they belong to.

`text == rebuild(text, tokenize(text))` always holds, so offsets map back exactly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

TOKEN_RE = re.compile(r"—|–|-{2,}|(?:(?!-{2,})[^\s—–])+")
WORD_CHAR_RE = re.compile(r"\w", re.UNICODE)


@dataclass(frozen=True)
class Token:
    index: int
    start: int
    end: int
    text: str

    @property
    def core(self) -> tuple[int, int]:
        """Char range of the word without leading/trailing punctuation (absolute offsets)."""
        s, e = 0, len(self.text)
        while s < e and not WORD_CHAR_RE.match(self.text[s]):
            s += 1
        while e > s and not WORD_CHAR_RE.match(self.text[e - 1]):
            e -= 1
        if s == e:  # pure punctuation
            return self.start, self.end
        return self.start + s, self.start + e


def tokenize(text: str) -> list[Token]:
    return [Token(i, m.start(), m.end(), m.group()) for i, m in enumerate(TOKEN_RE.finditer(text))]


def rebuild(text: str, tokens: Sequence[Token]) -> str:
    """Reassemble text from tokens and the whitespace between them."""
    out: list[str] = []
    pos = 0
    for t in tokens:
        out.append(text[pos:t.start])
        out.append(t.text)
        pos = t.end
    out.append(text[pos:])
    return "".join(out)


def indexed_view(tokens: Sequence[Token]) -> str:
    """What the LLM sees: `0:The 1:ocean 2:is ...`."""
    return " ".join(f"{t.index}:{t.text}" for t in tokens)


def char_mask_from_ranges(
    n_chars: int,
    tokens: Sequence[Token],
    ranges: Iterable[tuple[int, int]],
    *,
    include_gaps: bool = True,
) -> list[bool]:
    """Turn inclusive word-index ranges into a per-character mask.

    With include_gaps, whitespace between two selected words in the same range is
    selected too (that is how Verbatim underlining looks); never the whitespace before
    the first or after the last word of a range.
    """
    mask = [False] * n_chars
    for a, b in ranges:
        if not (0 <= a <= b < len(tokens)):
            raise IndexError(f"word range {a}-{b} outside 0-{len(tokens) - 1}")
        start, end = tokens[a].start, tokens[b].end
        if include_gaps:
            mask[start:end] = [True] * (end - start)
        else:
            for t in tokens[a:b + 1]:
                mask[t.start:t.end] = [True] * (t.end - t.start)
    return mask


def apply_partial(
    mask: list[bool], tokens: Sequence[Token], word_index: int, char_start: int, char_end: int
) -> None:
    """Select part of one word: chars [char_start, char_end) relative to the word."""
    t = tokens[word_index]
    if not 0 <= char_start < char_end <= len(t.text):
        raise IndexError(f"partial {char_start}:{char_end} outside word {t.text!r}")
    for i in range(t.start + char_start, t.start + char_end):
        mask[i] = True


def word_ranges_from_mask(mask: Sequence[bool], tokens: Sequence[Token]) -> list[tuple[int, int]]:
    """Inverse of char_mask_from_ranges: words with any selected char, as inclusive ranges."""
    sel = [any(mask[t.start:t.end]) for t in tokens]
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(sel):
        if sel[i]:
            j = i
            while j + 1 < len(sel) and sel[j + 1]:
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


# A sentence ends with . ! ? or …, maybe followed by closing quotes or brackets, and maybe by a
# footnote number when a word comes before the stop ("reserve forces.9 If Taiwan ..."), but not
# in a decimal ("3.5").
_SENT_END = re.compile(r"[.!?…][\"'”’)\]]*$|(?<=[^\W\d_])[.!?…][\"'”’)\]]*\[?\d{1,3}\]?$")


def sentence_starts(text: str) -> set[int]:
    """Char offsets where a sentence (or paragraph) starts. Heuristic, deliberately loose."""
    starts = {0}
    for m in re.finditer(r"\n", text):
        starts.add(m.end())
    toks = tokenize(text)
    for prev, nxt in zip(toks, toks[1:]):
        if _SENT_END.search(prev.text) and "\n" not in text[prev.end:nxt.start]:
            first = nxt.text.lstrip("\"'“‘([")
            if first[:1].isupper() or first[:1].isdigit():
                starts.add(nxt.start)
    return starts


def ends_sentence(text: str) -> bool:
    toks = tokenize(text)
    return bool(toks) and bool(_SENT_END.search(toks[-1].text))
