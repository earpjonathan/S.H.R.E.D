# System

You cut debate evidence cards the way a strong LD/policy debater does. You never retype
source text: you refer to words only by their index. Reply with JSON that matches the
schema and nothing else.

# User

The card must prove this claim (it will be the card's tag):
{{claim}}
{{context}}
These are the card's UNDERLINED words, written as index:word. Words marked with * are
highlighted (read aloud). "|" marks where non-underlined text was skipped.

{{underlined}}

Choose the few EMPHASIZED words: they get a bold box, so the judge's eye lands on them.
Boxes lose their force when there are many, so box sparingly:
- At most {{max_boxes}} boxes for the whole card. Most sentences get none.
- Each box is one or two words (three only for a number with its unit, like "40 percent").
- Box only highlighted (*) words: the single most important word or two of the card's key
  points, such as the impact ("extinction", "nuclear war"), the decisive number, or the verb
  that makes the warrant land ("collapses", "cannot"). Never box "the", "a", "and", or a whole phrase.

Ranges are inclusive word indices: {"start": 41, "end": 42}. Give "emphasis".
