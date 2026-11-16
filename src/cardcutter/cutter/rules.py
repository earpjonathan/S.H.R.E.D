"""Deterministic clean-up applied after the LLM stages."""
from __future__ import annotations

import re
from typing import Sequence

from cardcutter.tokenize import Token

STOP = {"of", "and", "the", "for", "on", "in", "to", "a", "an"}
PAREN_ACRONYM = re.compile(r"\(([A-Z][A-Za-z]{1,9}?)(?:s|’s|'s)?\)")


def clean_ranges(ranges: list[dict], n_tokens: int, allowed: set[int] | None = None) -> tuple[list[tuple[int, int]], int]:
    """Clip model ranges to valid word indices; swap reversed ones. Returns (ranges, fixes)."""
    out: list[tuple[int, int]] = []
    fixes = 0
    for r in ranges:
        a, b = int(r["start"]), int(r["end"])
        if a > b:
            a, b = b, a
            fixes += 1
        if b < 0 or a >= n_tokens:
            fixes += 1
            continue
        if a < 0 or b >= n_tokens:
            a, b = max(a, 0), min(b, n_tokens - 1)
            fixes += 1
        if allowed is not None:
            # split the range into runs of allowed words
            run: list[int] = []
            for i in range(a, b + 1):
                if i in allowed:
                    run.append(i)
                elif run:
                    out.append((run[0], run[-1]))
                    run = []
                    fixes += 1
            if run:
                out.append((run[0], run[-1]))
        else:
            out.append((a, b))
    return out, fixes


def acronym_initials(text: str, tokens: Sequence[Token], highlight: list[bool]) -> int:
    """Where a phrase is defined by an acronym, e.g. "marine protected areas (MPAs)",
    and the whole phrase is highlighted somewhere, highlight only its initials.
    Returns how many phrase occurrences were changed."""
    words = [t.text for t in tokens]
    lowered = [re.sub(r"\W", "", w).lower() for w in words]
    phrases: set[tuple[str, ...]] = set()
    for m in PAREN_ACRONYM.finditer(text):
        letters = m.group(1).lower()
        # the tokens right before "(ACR)"
        end_tok = next((i for i, t in enumerate(tokens) if t.start >= m.start()), None)
        if end_tok is None:
            continue
        cand = [lw for lw in lowered[max(0, end_tok - 10):end_tok] if lw]
        # walk back over words, skipping stop words, until the initials match
        picked: list[str] = []
        need = list(letters)
        for lw in reversed(cand):
            if not need:
                break
            if lw[0] == need[-1]:
                picked.insert(0, lw)
                need.pop()
            elif lw in STOP and picked:
                picked.insert(0, lw)
            else:
                break
        if not need and len(picked) >= 2:
            while picked and picked[0] in STOP:
                picked.pop(0)
            phrases.add(tuple(picked))
    changed = 0
    for phrase in phrases:
        k = len(phrase)
        for i in range(len(tokens) - k + 1):
            if tuple(lowered[i:i + k]) != phrase:
                continue
            span = tokens[i:i + k]
            core = [j for t in span for j in range(t.start, t.end) if text[j].isalnum()]
            if core and all(highlight[j] for j in core):
                for t in span:
                    word_chars = [j for j in range(t.start, t.end) if text[j].isalnum()]
                    keep = word_chars[0] if word_chars and lowered[t.index] not in STOP else None
                    for j in range(t.start, t.end):
                        highlight[j] = j == keep
                # also clear the spaces inside the phrase
                for j in range(span[0].start, span[-1].end):
                    if text[j].isspace():
                        highlight[j] = False
                # the initials are read as the acronym, so "(MPAs)" right after is not read again
                if i + k < len(tokens) and PAREN_ACRONYM.fullmatch(tokens[i + k].text.rstrip(".,;:")):
                    nxt = tokens[i + k]
                    for j in range(span[-1].end, nxt.end):
                        highlight[j] = False
                changed += 1
    return changed


_CORE = re.compile(r"[^\w]+", re.UNICODE)


def _core(word: str) -> str:
    return _CORE.sub("", word.lower().replace("’", "'"))


def align_read_aloud(plan: str, tokens: Sequence[Token], allowed: set[int]) -> tuple[list[int], list[tuple[int, int]], float]:
    """Map the sentence the model says it will read aloud onto the source words, in order.

    Only words in `allowed` (the underlined ones) can match, and only by exact spelling
    (case and punctuation aside), so nothing outside the source can ever be highlighted.
    A plan word that is a prefix of a longer source word ("biod" for "biodiversity", at
    least 3 letters) becomes a partial highlight. Returns (whole-word indices, partials as
    (index, chars), share of plan words matched).

    Dynamic programming finds the monotonic match with the most words, breaking ties
    toward compact matches so a stray "the" isn't matched far away.
    """
    plan_words = [w for w in (_core(x) for x in plan.split()) if w]
    cand = [t for t in tokens if t.index in allowed and _core(t.text)]
    m, n = len(plan_words), len(cand)
    if not m or not n:
        return [], [], 0.0
    cores = [_core(t.text) for t in cand]

    def match(i: int, j: int) -> int:  # 2 = whole word, 1 = prefix (partial), 0 = no
        p, c = plan_words[i], cores[j]
        if p == c:
            return 2
        if len(p) >= 3 and len(c) > len(p) and c.startswith(p):
            return 1
        return 0

    # score[i][j]: best (matches, -gaps) aligning plan[:i] within cand[:j], last match at j-1 tracked via prev
    NEG = (-1, 0.0)
    best = [[(0, 0.0)] * (n + 1) for _ in range(m + 1)]
    back: list[list[tuple[int, int, bool]]] = [[(0, 0, False)] * (n + 1) for _ in range(m + 1)]
    last = [[-1] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            options = [(best[i - 1][j], (i - 1, j, False), last[i - 1][j]),   # skip plan word
                       (best[i][j - 1], (i, j - 1, False), last[i][j - 1])]   # skip source word
            if match(i - 1, j - 1):
                prev_last = last[i - 1][j - 1]
                gap = (cand[j - 1].index - cand[prev_last].index - 1) if prev_last >= 0 else 0
                sc = best[i - 1][j - 1]
                options.append(((sc[0] + 1, sc[1] - 0.01 * gap), (i - 1, j - 1, True), j - 1))
            (score, bk, lst) = max(options, key=lambda o: o[0]) if options else (NEG, (0, 0, False), -1)
            best[i][j], back[i][j], last[i][j] = score, bk, lst
    whole: list[int] = []
    partial: list[tuple[int, int]] = []
    i, j = m, n
    while i > 0 and j > 0:
        pi, pj, matched = back[i][j]
        if matched:
            kind = match(i - 1, j - 1)
            t = cand[j - 1]
            if kind == 2:
                whole.append(t.index)
            else:
                # chars of the token covering the prefix (skip leading punctuation like quotes)
                lead = len(t.text) - len(t.text.lstrip("\"'“‘([—–-"))
                partial.append((t.index, lead + len(plan_words[i - 1])))
        i, j = pi, pj
    whole.reverse()
    partial.reverse()
    return whole, partial, best[m][n][0] / m


NEGATORS = {"not", "no", "never", "none", "nor", "neither", "without", "only", "few", "fewer", "less", "least",
            "cannot", "can't", "won't", "don't", "doesn't", "isn't", "aren't", "wasn't", "weren't", "didn't",
            "hardly", "barely", "unlikely", "fail", "fails", "failed"}
_STOP = set("the a an of to in and or for on at by with from that this these those is are was were be been being "
            "as it its their they we our has have had will would can could may might should".split())


def _word(t: Token) -> str:
    return re.sub(r"[^\w'%]", "", t.text.lower().replace("’", "'"))


def _negated(tokens: Sequence[Token], hl: set[int], i: int, reach: int = 3) -> int | None:
    """The word negator i applies to: the next content word after it in the same highlighted
    stretch and sentence ("not budging", "not actually access", "failed to meet")."""
    for j in range(i + 1, min(i + reach + 1, len(tokens))):
        if j not in hl or tokens[j - 1].text.rstrip("”’\"')]").endswith((".", "!", "?", ";", ":")):
            return None
        w = _word(tokens[j])
        if w in _STOP or w in NEGATORS or (w.endswith("ly") and len(w) > 4):
            continue
        return j
    return None


def keep_negators(tokens: Sequence[Token], highlighted: list[int], keep: set[int], reach: int = 3) -> int:
    """Negators and limiters ("not", "only", "few") after a trim. A dropped one comes back when the
    word it applies to was kept: dropping it would flip or inflate the claim. A kept one whose word
    was dropped gets that word back: "Putin not seems confident" -> "Putin not budging seems
    confident". A negator with no word after it ("deliver none.") comes back next to a kept word."""
    hl = set(highlighted)
    added = 0
    for i in highlighted:
        if _word(tokens[i]) not in NEGATORS:
            continue
        j = _negated(tokens, hl, i, reach)
        if i not in keep:
            if j is not None:
                back = j in keep
            else:
                back = any(k in keep and all(m in hl for m in range(min(i, k), max(i, k) + 1))
                           for k in range(i - reach, i + reach + 1) if k != i)
            if back:
                keep.add(i)
                added += 1
        elif j is not None and j not in keep and any(k in keep for k in range(i - reach, i)):
            for k in range(i + 1, j + 1):
                if k not in keep:
                    keep.add(k)
                    added += 1
    return added


def drop_repeats(text: str, tokens: Sequence[Token], highlight: list[bool], min_words: int = 3,
                 overlap: float = 0.75) -> int:
    """Unhighlight a sentence whose highlighted content words (at least `min_words`) mostly repeat
    content words already highlighted earlier in the card. Human cuts almost never do this
    (0.9% of the 966 highlighted example cards). Returns the number of sentences cleared."""
    seen: set[str] = set()
    cleared = 0
    for m in re.finditer(r"[^.!?]+[.!?]*", text):
        a, b = m.span()
        toks = [t for t in tokens if a <= t.start < b and any(highlight[t.start:t.end])]
        words = [w for w in (_word(t) for t in toks) if (len(w) > 3 or w.isdigit()) and w not in _STOP]
        if len(words) >= min_words and sum(w in seen for w in words) / len(words) >= overlap:
            for t in toks:
                for i in range(t.start, t.end):
                    highlight[i] = False
            cleared += 1
            continue
        seen |= set(words)
    return cleared


_ARTICLES = {"a", "an", "the"}


def bridge_gaps(text: str, tokens: Sequence[Token], highlight: list[bool], underline: list[bool],
                max_gap: int = 2) -> int:
    """Highlight a gap of 1..max_gap underlined words between two highlighted words of the same
    sentence, unless the gap is only articles: "Ukraine shows no [signs of] capitulating",
    "Neither side [can] rely". Trimmed highlighting drops these words and the read-aloud stops
    parsing. Offline on saved cuts (2026-11-11), max_gap 2: dev 39.7 -> 40.6 highlight F1,
    rest 38.1 -> 39.5. Returns the number of gaps filled."""
    if max_gap < 1:
        return 0
    on = [t.index for t in tokens if any(highlight[t.start:t.end])]
    filled = 0
    for a, b in zip(on, on[1:]):
        gap = tokens[a + 1:b]
        if not 1 <= len(gap) <= max_gap or "\n" in text[tokens[a].start:tokens[b].end]:
            continue
        if any(t.text.rstrip("”’\"')]").endswith((".", "!", "?", ";", ":")) for t in tokens[a:b]):
            continue  # a sentence (or clause list) ends inside: these are separate pieces
        if not all(all(underline[c] for c in range(*t.core)) for t in gap):
            continue
        if all(_word(t) in _ARTICLES for t in gap):
            continue
        for c in range(tokens[a].start, tokens[b].end):
            highlight[c] = True
        filled += 1
    return filled


_TRUE_NEGATORS = {"not", "no", "never", "nor", "neither", "without", "cannot", "can't", "won't", "don't",
                  "doesn't", "isn't", "aren't", "wasn't", "weren't", "didn't", "hasn't", "haven't", "wouldn't",
                  "shouldn't", "couldn't"}
_SKIP_AFTER_NEGATOR = _STOP | {"yet", "just", "even", "necessarily", "always", "all", "so", "very", "much", "simply"}


def _ends_clause(t: Token) -> bool:
    return t.text.rstrip("”’\"')]").endswith((".", "!", "?", ";", ":"))


def fix_dangling_negators(text: str, tokens: Sequence[Token], highlight: list[bool], underline: list[bool],
                          reach: int = 5, max_run: int = 6) -> int:
    """A highlighted "not"/"isn't"/"no" whose word is left unread can flip the card.
    - If the words up to the negated word are underlined, they are highlighted: "the U.S. may
      not actually [be able]", "the pit can no [longer] ... sustain".
    - Otherwise, if nothing else is read soon after it in the sentence, the highlighted piece
      ending in the negator (up to `max_run` words; else just the negator) is dropped: "the goal
      isn't [to exhaust Russia, but it puts] Russia at a disadvantage" is heard as "goal isn't
      Russia at disadvantage". "No" used as a noun ("an official no to") is left alone.
    Run after bridge_gaps. Returns the number of negators fixed."""
    th = [any(highlight[t.start:t.end]) for t in tokens]
    fixed = 0
    for i, t in enumerate(tokens):
        if not th[i] or _word(t) not in _TRUE_NEGATORS or _ends_clause(t):
            continue  # "did not." negates what came before
        if _word(t) == "no" and i + 1 < len(tokens) and _word(tokens[i + 1]) in _STOP:
            continue  # a noun: "issue an official no to any ..."
        j = None
        for k in range(i + 1, min(i + reach + 1, len(tokens))):
            if "\n" in text[tokens[k - 1].end:tokens[k].start]:
                break
            w = _word(tokens[k])
            if w and w not in _SKIP_AFTER_NEGATOR and w not in NEGATORS and not (w.endswith("ly") and len(w) > 4):
                j = k
                break
            if _ends_clause(tokens[k]):
                break
        if j is None or th[j]:
            continue
        if all(all(underline[c] for c in range(*tokens[k].core)) for k in range(i + 1, j + 1)):
            for c in range(tokens[i].start, tokens[j].end):
                highlight[c] = True
            for k in range(i + 1, j + 1):
                th[k] = True
            fixed += 1
            continue
        nxt = next((k for k in range(i + 1, len(tokens)) if th[k]), None)
        crosses = nxt is None or any(_ends_clause(tokens[k]) for k in range(i, nxt)) \
            or "\n" in text[tokens[i].end:tokens[nxt].start]
        if not crosses and nxt - i <= reach:
            continue  # something is read right after it; leave the cut alone
        a = i
        while a > 0 and th[a - 1] and "\n" not in text[tokens[a - 1].end:tokens[a].start] \
                and not _ends_clause(tokens[a - 1]):
            a -= 1
        if i - a + 1 > max_run:
            a = i  # a long clause stays; only the negator that negates nothing read goes
        for c in range(tokens[a].start, tokens[i].end):
            highlight[c] = False
        for k in range(a, i + 1):
            th[k] = False
        fixed += 1
    return fixed


def tidy_spaces(text: str, highlight: list[bool]) -> None:
    """A highlighted space or punctuation-only gap stays highlighted only between two highlighted
    word characters; trimming words out of a run must not leave stray highlighted gaps."""
    n = len(text)
    i = 0
    while i < n:
        if highlight[i] and not text[i].strip():
            j = i
            while j < n and not text[j].strip():
                j += 1
            left = i > 0 and highlight[i - 1] and text[i - 1].strip()
            right = j < n and highlight[j]
            if not (left and right):
                for k in range(i, j):
                    highlight[k] = False
            i = j
        else:
            i += 1


def drop_stranded(tokens: Sequence[Token], highlighted: list[int], keep: set[int]) -> int:
    """After a trim, remove a kept function word ("be", "of", "were") that no longer touches any
    other kept word: read alone it is noise, not compression."""
    removed = 0
    for i in sorted(keep):
        w = _word(tokens[i])
        if w in _STOP and w not in NEGATORS and (i - 1) not in keep and (i + 1) not in keep:
            keep.discard(i)
            removed += 1
    return removed


def cap_emphasis(tokens: Sequence[Token], ranges: list[tuple[int, int]], allowed: set[int], *,
                 max_boxes: int, max_words: int = 2, max_total: int | None = None) -> list[tuple[int, int]]:
    """Keep emphasis to a few short boxes: only `allowed` (highlighted) words, no box starting or
    ending on "the"/"and"/punctuation-only words, each box at most `max_words` words (one more
    when it holds a number: "40 percent"), at most `max_boxes` boxes and `max_total` boxed words
    in all, in the model's order (a box that would pass max_total is skipped)."""
    out: list[tuple[int, int]] = []
    seen: set[int] = set()
    for a, b in ranges:
        run: list[int] = []
        for i in list(range(a, b + 1)) + [None]:  # type: ignore[list-item]
            if i is not None and i in allowed and i not in seen:
                run.append(i)
                continue
            while run and (_word(tokens[run[0]]) in _STOP or not _word(tokens[run[0]])):
                run.pop(0)
            while run and (_word(tokens[run[-1]]) in _STOP or not _word(tokens[run[-1]])):
                run.pop()
            if run:
                limit = max_words + (1 if any(ch.isdigit() for k in run for ch in tokens[k].text) else 0)
                run = run[:limit]
                while run and _word(tokens[run[-1]]) in _STOP:
                    run.pop()
                if max_total is not None and len(seen) + len(run) > max_total:
                    run = []   # a box that doesn't fit is skipped, never cut ("fait" of "fait accompli")
                if run and len(out) < max_boxes:
                    out.append((run[0], run[-1]))
                    seen.update(range(run[0], run[-1] + 1))
            run = []
    return out


def join_adjacent(text: str, tokens: Sequence[Token], highlight: list[bool], underline: list[bool]) -> int:
    """Two neighboring words that are both highlighted get the space between them highlighted
    (and underlined), and two neighboring underlined words get it underlined, so the yellow and
    the line don't break mid-phrase when the model gave two ranges. Returns gaps joined."""
    joined = 0
    for t, u in zip(tokens, tokens[1:]):
        a, b = t.end, u.start
        if a >= b or text[a:b].strip() or "\n" in text[a:b]:
            continue
        if highlight[a - 1] and highlight[b] and not all(highlight[a:b]):
            for c in range(a, b):
                highlight[c] = underline[c] = True
            joined += 1
        elif underline[a - 1] and underline[b] and not all(underline[a:b]):
            for c in range(a, b):
                underline[c] = True
            joined += 1
    return joined


# "weeks.2", "conflict.”4", and in PDFs "stockpiles10" (glued to a lowercase word of 4+ letters);
# never "9.3", "2,000", "F35" or "COVID19".
_FOOTNOTE = re.compile(r"(?:(?<=[^\d\s][.,;:!?”\"’)\]])\[?\d{1,3}\]?|(?<=[a-z]{4})\d{1,3})$")


def unread_footnotes(text: str, tokens: Sequence[Token], highlight: list[bool]) -> int:
    """A footnote number stuck to a word ("within weeks.2", "conflict.”4", "stockpiles10") is never read aloud:
    its digits are unhighlighted (the word and its punctuation stay). Returns how many."""
    n = 0
    for t in tokens:
        m = _FOOTNOTE.search(t.text)
        if m and any(highlight[t.start + m.start():t.end]):
            for c in range(t.start + m.start(), t.end):
                highlight[c] = False
            n += 1
    return n


_SENT_START = re.compile(r"(?<![A-Z]\.[A-Z])[.!?][”\"’')\]]*(?:\[?\d{1,3}\]?)?\s+(?=[“\"‘(]?[A-Z0-9])")
_SENT_STOP = re.compile(r"(?<![A-Z]\.[A-Z])[.!?][”\"’')\]]*(?:\[?\d{1,3}\]?)?(?=\s|$)")


def marked_edges(text: str, underline: list[bool], max_plain: int = 40, *, mid_sentence: bool = False,
                 starts: set[int] | None = None) -> tuple[int, int]:
    """[a, b): from the start of the first paragraph with any underlining to the end of the last
    one. Whole paragraphs with nothing underlined at either edge of a card are padding: your files
    leave a median 3% of the body unmarked after the last underline (2026-11-12). Within those
    paragraphs, a card starts at a sentence (not mid-sentence, as a PDF page break can leave it)
    and keeps at most about max_plain unmarked words before its first mark and after its last,
    cutting at sentence boundaries. mid_sentence: the text before this window doesn't end a
    sentence (a PDF page break before a capitalized word), so the first sentence is cut too.
    starts: the offsets in `text` the integrity check accepts as sentence starts; a new start
    must be one of them."""
    marked = [i for i, u in enumerate(underline) if u and not text[i].isspace()]
    if not marked:
        return 0, len(text)
    first, last = marked[0], marked[-1]
    a = text.rfind("\n", 0, first) + 1
    b = text.find("\n", last)
    b = len(text) if b < 0 else b
    if text[a:a + 1].islower() or (mid_sentence and a == 0) or len(text[a:first].split()) > max_plain:
        for m in _SENT_START.finditer(text, a, first + 1):
            if starts is not None and m.end() not in starts:
                continue
            if len(text[m.end():first].split()) <= max_plain:
                a = m.end()
                break
    if len(text[last + 1:b].split()) > max_plain:
        m = _SENT_STOP.search(text, last, b)
        if m:
            b = m.end()
    return a, b


_AUX = set("has have had is are was were will would could can may might should must does do did cannot "
           "can't won't isn't aren't wasn't weren't hasn't haven't doesn't don't didn't".split())
# Reporting verbs lose their speaker the same way: "concludes that Russia's gambit failed".
_AUX |= set("says said argues argued concludes concluded notes noted warns warned writes wrote finds found "
            "estimates estimated explains explained reports reported adds added believes believed contends "
            "claims claimed suggests suggested".split())
_INITIALS = re.compile(r"(?:[A-Z]\.){2,}")


def _ends_sentence(t: Token) -> bool:
    w = t.text.rstrip("”’\"')]")
    return w.endswith((".", "!", "?")) and not _INITIALS.fullmatch(w)   # "U.S." doesn't end one


def restore_subjects(text: str, tokens: Sequence[Token], highlight: list[bool], underline: list[bool],
                     max_words: int = 6) -> int:
    """A read piece that starts with a helping or reporting verb ("has exposed Europe's weaknesses",
    "could run out of munitions", "concludes that") has lost its subject. When the words from the start of its sentence up to
    it are all unread and few ("The war in Ukraine", "The U.S."), they are read and underlined
    too. Returns the number of pieces fixed."""
    th = [any(highlight[t.start:t.end]) for t in tokens]
    fixed = 0
    for i, t in enumerate(tokens):
        if not th[i] or (i > 0 and th[i - 1]) or _word(t) not in _AUX:
            continue
        j = i
        while j > 0 and not _ends_sentence(tokens[j - 1]) and "\n" not in text[tokens[j - 1].end:tokens[j].start]:
            j -= 1
        if not 1 <= i - j <= max_words or any(th[k] for k in range(j, i)):
            continue
        for c in range(tokens[j].start, tokens[i].start):
            highlight[c] = underline[c] = True
        for k in range(j, i):
            th[k] = True
        fixed += 1
    return fixed


def fill_underline_gaps(text: str, tokens: Sequence[Token], underline: list[bool], max_gap: int = 1) -> int:
    """Underline a gap of up to max_gap plain words between two underlined words of one sentence,
    so the line reads as a clause: "It has an estimated range" not "It _ an _ range" (the model
    sometimes returns one-word ranges). Returns the number of gaps filled."""
    tu = [any(underline[t.start:t.end]) for t in tokens]
    filled = 0
    i = 0
    while i < len(tokens):
        if not tu[i]:
            i += 1
            continue
        j = i + 1
        while j < len(tokens) and not tu[j] and j - i - 1 < max_gap:
            j += 1
        if j < len(tokens) and tu[j] and j > i + 1 and not any(_ends_sentence(tokens[k]) for k in range(i, j)) \
                and "\n" not in text[tokens[i].end:tokens[j].start]:
            for c in range(tokens[i].end, tokens[j].start):
                underline[c] = True
            for k in range(i + 1, j):
                tu[k] = True
            filled += 1
        i = j if j > i + 1 else i + 1
    return filled


def starts_mid_sentence(before: str) -> bool:
    """Does a window that follows `before` start in the middle of a sentence? Yes when the text
    before it is a long line (not a heading) that ends without sentence punctuation."""
    tail = before.rstrip()
    if not tail:
        return False
    last_line = tail.rsplit("\n", 1)[-1]
    return len(last_line.split()) > 12 and not re.search(r"[.!?…:;\"”’')\]]\[?\d{0,3}\]?$", last_line)


def finish_sentences(text: str, tokens: Sequence[Token], highlight: list[bool], underline: list[bool],
                     min_run: int = 3) -> int:
    """A read piece of at least min_run words that stops one word before its sentence ends ("to
    fracture the NATO" + "alliance.") gets that last word read (and underlined) too.
    Returns the number of pieces finished."""
    th = [any(highlight[t.start:t.end]) for t in tokens]
    done = 0
    for i in range(len(tokens) - 1):
        nxt = tokens[i + 1]
        if not th[i] or th[i + 1] or not _ends_sentence(nxt) or _ends_sentence(tokens[i]) or not _word(nxt):
            continue
        if nxt.text.startswith(("(", "[")):   # "... Sales (FMS)." : the parenthetical isn't the sentence's end word
            continue
        if "\n" in text[tokens[i].end:nxt.start]:
            continue
        k = i
        while k > 0 and th[k - 1] and not _ends_sentence(tokens[k - 1]):
            k -= 1
        if i - k + 1 < min_run:
            continue
        for c in range(tokens[i].end, nxt.end):
            highlight[c] = underline[c] = True
        th[i + 1] = True
        done += 1
    return done
