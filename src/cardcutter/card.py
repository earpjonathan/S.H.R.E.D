"""Card representation: text plus per-character formatting masks.

A card is three formatted texts (tag, cite, body). Each `FText` holds the exact text and,
for every attribute, a list with one entry per character. Body paragraphs are joined
with "\\n" so a card body is one string that can be checked against the source text.

Attributes
- underline, emphasis: the read-through and the boxed key words.
- highlight: None, a Word highlight name ("yellow", "cyan", ...), or "shd:RRGGBB" for
  shading used as highlight.
- cite13: the `Style13ptBold` short cite.
- size: effective font size in half-points (22 = 11 pt), or None when unknown.
- bold, italic: direct formatting only, "on" / "off" (explicitly unbolded) / None.
- strike: direct strikethrough.
- vert: "superscript" / "subscript" or None.

JSON stores each attribute as run-length spans `[start, end]` (end exclusive) or
`[start, end, value]`, so cards stay compact and diffable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

BOOL_ATTRS: tuple[str, ...] = ("underline", "emphasis", "cite13", "strike")
VALUE_ATTRS: tuple[str, ...] = ("highlight", "size", "vert", "bold", "italic")
ATTRS: tuple[str, ...] = BOOL_ATTRS + VALUE_ATTRS

PARA_SEP = "\n"
LINE_BREAK = " "  # w:br inside a paragraph


def _empty(attr: str, n: int) -> list[Any]:
    return [False] * n if attr in BOOL_ATTRS else [None] * n


@dataclass
class FText:
    text: str
    marks: dict[str, list[Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        n = len(self.text)
        for attr in ATTRS:
            if attr not in self.marks:
                self.marks[attr] = _empty(attr, n)
            elif len(self.marks[attr]) != n:
                raise ValueError(f"mask `{attr}` has {len(self.marks[attr])} entries for {n} chars")
        unknown = set(self.marks) - set(ATTRS)
        if unknown:
            raise ValueError(f"unknown attributes: {sorted(unknown)}")

    # -- building -----------------------------------------------------------------
    @classmethod
    def plain(cls, text: str) -> "FText":
        return cls(text)

    def append(self, text: str, fmt: dict[str, Any]) -> None:
        """Append text with one formatting dict applied to every character."""
        self.text += text
        for attr in ATTRS:
            default = False if attr in BOOL_ATTRS else None
            self.marks[attr].extend([fmt.get(attr, default)] * len(text))

    def set_span(self, attr: str, start: int, end: int, value: Any = True) -> None:
        if not 0 <= start <= end <= len(self.text):
            raise IndexError(f"span {start}:{end} outside 0:{len(self.text)}")
        self.marks[attr][start:end] = [value] * (end - start)

    # -- reading ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.text)

    def fmt_at(self, i: int) -> tuple[Any, ...]:
        return tuple(self.marks[a][i] for a in ATTRS)

    def runs(self) -> Iterator[tuple[int, int, dict[str, Any]]]:
        """Maximal runs of identical formatting: (start, end, {attr: value})."""
        n = len(self.text)
        i = 0
        while i < n:
            f = self.fmt_at(i)
            j = i + 1
            while j < n and self.fmt_at(j) == f:
                j += 1
            yield i, j, dict(zip(ATTRS, f))
            i = j

    def spans(self, attr: str) -> list[list[Any]]:
        """Run-length spans of non-empty values for one attribute."""
        vals = self.marks[attr]
        out: list[list[Any]] = []
        i, n = 0, len(vals)
        while i < n:
            v = vals[i]
            if v is False or v is None:
                i += 1
                continue
            j = i + 1
            while j < n and vals[j] == v:
                j += 1
            out.append([i, j] if v is True else [i, j, v])
            i = j
        return out

    def chars(self, attr: str) -> int:
        return sum(1 for v in self.marks[attr] if v not in (False, None))

    def slice(self, start: int, end: int) -> "FText":
        return FText(self.text[start:end], {a: m[start:end] for a, m in self.marks.items()})

    def paragraphs(self) -> list["FText"]:
        out, start = [], 0
        for i, ch in enumerate(self.text):
            if ch == PARA_SEP:
                out.append(self.slice(start, i))
                start = i + 1
        out.append(self.slice(start, len(self.text)))
        return out

    def read_aloud(self) -> str:
        """Only the highlighted characters, with gaps collapsed to single spaces."""
        out: list[str] = []
        gap = False
        for ch, h in zip(self.text, self.marks["highlight"]):
            if h is not None:
                if gap and out:
                    out.append(" ")
                out.append(ch)
                gap = False
            else:
                gap = True
        return " ".join("".join(out).split())

    # -- JSON ---------------------------------------------------------------------
    def to_json(self) -> dict[str, Any]:
        marks = {a: s for a in ATTRS if (s := self.spans(a))}
        return {"text": self.text, "marks": marks}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "FText":
        ft = cls(obj["text"])
        for attr, spans in obj.get("marks", {}).items():
            for span in spans:
                ft.set_span(attr, span[0], span[1], span[2] if len(span) > 2 else True)
        return ft

    @classmethod
    def concat(cls, parts: list["FText"], sep: str = PARA_SEP) -> "FText":
        out = cls("")
        for k, p in enumerate(parts):
            if k:
                out.append(sep, {})
            out.text += p.text
            for a in ATTRS:
                out.marks[a].extend(p.marks[a])
        return out


@dataclass
class Card:
    tag: FText
    cite: FText
    body: FText
    meta: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "meta": self.meta,
            "tag": self.tag.to_json(),
            "cite": self.cite.to_json(),
            "body": self.body.to_json(),
        }

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "Card":
        return cls(
            tag=FText.from_json(obj["tag"]),
            cite=FText.from_json(obj["cite"]),
            body=FText.from_json(obj["body"]),
            meta=obj.get("meta", {}),
        )
