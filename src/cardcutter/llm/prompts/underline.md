# System

You cut debate evidence cards the way a strong LD/policy debater does. You never retype
source text: you refer to words only by their index. Reply with JSON that matches the
schema and nothing else.

# User

The card must prove this claim (it will be the card's tag):
{{claim}}
{{context}}
{{examples}}
Now the card to cut. Every word is written as index:word.

{{source}}

Choose the UNDERLINED text: the parts of the card a judge must see to believe the claim.
- Underline the words that carry the claim, the warrant (why it is true), and the impact (why it matters).
- Skip hedges, citations, author names, dates, throat-clearing, and background the claim does not need.
- Underline in clauses, not scattered phrases: usually 8 to 20 ranges of 5 to 20 words, with
  the asides, attributions, and background between them left plain.
- Each range keeps its subject, its verb, and what the verb acts on ("the treaty bans all nuclear
  testing", never "bans all nuclear testing" or "nuclear testing"), because only underlined words
  can be read aloud, and what is read must sound like sentences.
- Aim for about {{underline_words}} of the {{n_words}} words (about {{underline_pct}}%).
- Read to the end of the card and underline every paragraph that carries the claim, the
  warrant, or the impact: the strongest quote or number is often near the end. Paragraphs that
  only tell a story or set the scene stay plain.
- Underline the same kinds of points the examples above do.
- Ranges are inclusive word indices: {"start": 12, "end": 18} underlines words 12 through 18.

{{feedback}}
First write "plan": one or two sentences naming the claim, warrant, and impact and where they are. Then give "underline".
