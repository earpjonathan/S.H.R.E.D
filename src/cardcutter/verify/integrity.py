"""Evidence-integrity checks. A card that fails any check must not be exported.

The rules (from the spec):
- the card body is an exact, unaltered, contiguous excerpt of the cleaned source;
- formatting masks cover only that text, and nest (highlight/emphasis inside underline)
  unless config says the corpus does otherwise;
- the card is not empty: some text, some underline, some highlight;
- it starts on a sentence boundary; it ends on one unless mid-sentence ends are allowed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from cardcutter.card import FText
from cardcutter.tokenize import ends_sentence, sentence_starts


class IntegrityError(Exception):
    """Raised when a card is not a faithful excerpt. Nothing may be exported."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("card failed integrity check:\n  - " + "\n  - ".join(problems))


@dataclass(frozen=True)
class IntegrityPolicy:
    allow_mid_sentence_end: bool = True
    require_highlight_in_underline: bool = True
    require_emphasis_in_underline: bool = True

    @classmethod
    def from_config(cls, cfg) -> "IntegrityPolicy":  # cfg: cardcutter.config.Config
        return cls(
            allow_mid_sentence_end=bool(cfg.get("verify.allow_mid_sentence_end", True)),
            require_highlight_in_underline=bool(cfg.get("verify.require_highlight_in_underline", True)),
            require_emphasis_in_underline=bool(cfg.get("verify.require_emphasis_in_underline", True)),
        )


@dataclass
class IntegrityReport:
    offset: int
    warnings: list[str] = field(default_factory=list)


def verify_body(
    body: FText,
    source: str,
    offset: int | None = None,
    policy: IntegrityPolicy = IntegrityPolicy(),
) -> IntegrityReport:
    """Check a card body against its source. Returns warnings; raises IntegrityError."""
    problems: list[str] = []
    warnings: list[str] = []
    text = body.text

    # 1. exact contiguous excerpt
    if not text.strip():
        raise IntegrityError(["card body is empty"])
    if offset is None:
        offset = source.find(text)
        if offset < 0:
            raise IntegrityError(["card body is not a contiguous substring of the source text"])
    elif source[offset:offset + len(text)] != text:
        first_diff = next(
            (i for i, (a, b) in enumerate(zip(text, source[offset:])) if a != b),
            min(len(text), max(0, len(source) - offset)),
        )
        raise IntegrityError([
            f"card body differs from source at body char {first_diff} "
            f"(source offset {offset + first_diff}): "
            f"card {text[first_diff:first_diff + 30]!r} vs source "
            f"{source[offset + first_diff:offset + first_diff + 30]!r}"
        ])

    # 2. masks are the right length (FText enforces this; re-check in case of mutation)
    for attr, mask in body.marks.items():
        if len(mask) != len(text):
            problems.append(f"mask `{attr}` length {len(mask)} != text length {len(text)}")

    # 3. not empty
    ul, hl, em = body.marks["underline"], body.marks["highlight"], body.marks["emphasis"]
    if not any(ul):
        problems.append("nothing is underlined")
    if not any(h is not None for h in hl):
        problems.append("nothing is highlighted")

    # 4. nesting
    if policy.require_highlight_in_underline:
        bad = [i for i, h in enumerate(hl) if h is not None and not ul[i] and not text[i].isspace()]
        if bad:
            problems.append(f"{len(bad)} highlighted chars are not underlined (first at {bad[0]})")
    if policy.require_emphasis_in_underline:
        bad = [i for i, e in enumerate(em) if e and not ul[i] and not text[i].isspace()]
        if bad:
            problems.append(f"{len(bad)} emphasized chars are not underlined (first at {bad[0]})")

    # 5. sentence boundaries
    lead = len(text) - len(text.lstrip())
    if (offset + lead) not in sentence_starts(source):
        problems.append(f"card does not start on a sentence boundary: {text.lstrip()[:40]!r}")
    if not ends_sentence(text):
        msg = f"card ends mid-sentence: ...{text.rstrip()[-40:]!r}"
        (warnings if policy.allow_mid_sentence_end else problems).append(msg)

    if problems:
        raise IntegrityError(problems)
    return IntegrityReport(offset=offset, warnings=warnings)
