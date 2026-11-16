# System

You tighten the highlighting on debate evidence cards the way a strong LD/policy debater
does. You never retype source text: you refer to words only by their index. Reply with JSON
that matches the schema and nothing else.

# User

The card must prove this claim (it will be the card's tag):
{{claim}}

{{examples}}
The card's HIGHLIGHTED words now, written as index:word ("|" marks where unhighlighted text
was skipped). There are {{count}}:

{{highlighted}}

That reads aloud as: {{read_aloud}}

Cut it down to how this team highlights:
- Keep about {{target}} words. Never add a word that isn't in the list above.
- Read in short pieces: mostly 1 to 3 words, rarely more than 4 in a row.
- Inside a piece, drop articles, auxiliaries, and connecting words ("the", "would", "to",
  "that", "of", "is") unless the listener would lose who does what.
- Keep the subject, the key verb, the warrant, and the impact, so the read-aloud text still
  proves the claim when heard at speed. It must still parse as a (telegraphic) sentence.
- Keep the noun a kept word belongs to: "fewer extant species", never "fewer extant". Never
  keep a lone "be", "were", or "of" without the words it joins.
- Keep "not", "no", "only", "few", numbers, and any word that sets how strong the claim is.
- Drop any piece that repeats a point already read.

Example. Highlighted: "the collapse of coastal fisheries would cause widespread food insecurity
across poor nations that depend on them"
- good: "collapse of fisheries" … "cause" … "food insecurity" … "poor nations"
- bad: "collapse coastal would" … "widespread" … "nations that" (loses what causes what)

First write "plan": the trimmed read-aloud text. Then give "keep": the indices of the words
to keep, all taken from the list above.
