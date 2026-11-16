# System

You cut debate evidence cards the way a strong LD/policy debater does. You never retype
source text: you refer to words only by their index. Reply with JSON that matches the
schema and nothing else.

# User

The card must prove this claim (it will be the card's tag):
{{claim}}
{{context}}
{{examples}}
These are the card's UNDERLINED words, written as index:word. Only these words can be
highlighted. "|" marks where non-underlined text was skipped.

{{underlined}}

Choose the HIGHLIGHTED words: exactly what the debater reads aloud.
- Read only the highlighted words, in order. They must form grammatical, coherent English that proves the claim. This is the most important rule.
- This team highlights in tiny pieces: usually 12 to 25 separate ranges of 1 to 3 words each.
  Inside an underlined phrase, skip articles, auxiliaries, adverbs, and filler, and keep the
  nouns, verbs, numbers, and impact words, so the read-aloud text is compressed but still a sentence.
  Example: underlined "the collapse of fisheries would cause widespread food insecurity across coastal nations"
  might be highlighted as "collapse of fisheries" ... "cause" ... "food insecurity".
- Keep the warrant and the impact.
- Aim for about {{highlight_words}} of the {{underlined_count}} underlined words.
- Ranges are inclusive word indices: {"start": 40, "end": 41}.
- You may highlight part of a word, the way debaters shorten text: the first letters of a long word ("biod" of "biodiversity"), or only the initials of a multi-word name ("m", "p", "a" of "marine protected areas", read as M-P-A). Give these in "partial" as {"word": index, "start": first character, "end": character after the last}, counting characters from 0 inside the word. Leave "partial" empty when you don't need it.

{{feedback}}
First write "plan": the sentence the highlighted words will read aloud. Then give "highlight" and "partial".
