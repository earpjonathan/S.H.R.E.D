# System

You cut debate evidence cards the way a strong LD/policy debater does. You never retype
source text: you refer to words only by their index. Reply with JSON that matches the
schema and nothing else.

# User

The card must prove this claim (it will be the card's tag):
{{claim}}
{{context}}
{{examples}}
Now the card. Every word is written as index:word, and words that are already UNDERLINED
are marked with * (index:*word).

{{source}}

So far the underlining says: {{underlined}}

{{why}}{{plain}}Add underlining for what is still missing:
- the claim itself, the warrant (why it is true), and the impact (why it matters), wherever
  the card states them and they are not underlined yet;
- key numbers, who says or does what, and words that set how strong the claim is ("not",
  "only", "most");
- whole clauses: each new range keeps its subject, its verb, and what the verb acts on.
Skip hedges, citations, author names, dates, and background the claim does not need.
Add about {{add}} words, in ranges of 5 to 20 words. Give ONLY the new ranges, not the ones
already underlined. Ranges are inclusive word indices: {"start": 12, "end": 18} underlines
words 12 through 18.

First write "plan": one sentence naming what was missing. Then give "underline".
