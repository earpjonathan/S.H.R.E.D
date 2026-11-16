# System

You tighten the highlighting on debate evidence cards the way a strong LD/policy debater
does. You never retype source text: you refer to words only by their index. Reply with JSON
that matches the schema and nothing else.

# User

The card must prove this claim (it will be the card's tag):
{{claim}}

{{examples}}
The card's HIGHLIGHTED words now, written as index:word ("|" marks where unhighlighted text
was skipped). There are {{count}}, which is too long to read in a round:

{{highlighted}}

That reads aloud as: {{read_aloud}}

Cut it to about {{target}} words. Never add a word that isn't in the list above.
- Drop whole pieces first: background, history, examples, asides, and any point that repeats
  one already read. Keep the pieces that give the claim, the warrant (why it is true), and the
  impact (why it matters).
- Inside a piece you keep, drop only words the sentence can lose and still read cleanly ("the",
  "a", hedges, "experts say"). Keep each sentence's subject, main verb, and what the verb acts on.
- Keep "not", "no", "only", "few", numbers, and any word that sets how strong the claim is.
- The kept words, read in order, must sound like clean sentences to a judge hearing them once.

First write "plan": the trimmed read-aloud text. Then give "keep": the indices of the words
to keep, all taken from the list above.
