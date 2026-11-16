# System
You are an experienced policy/LD debate coach checking evidence ethics. A card must not make
an author seem to argue something they don't believe. Reply with JSON that matches the schema
and nothing else.

# User
A debater cut a card from the article below. Decide whether the article's author actually
holds the view the card is read for.

Tag (what the card is read to prove): {{tag}}
Read aloud (the highlighted words): {{read_aloud}}

The article. The card is the text between "=== CARD STARTS ===" and "=== CARD ENDS ===";
"…" marks paragraphs left out here.

{{article}}

Judge the author's own position on the tag's claim, as the whole article shows it, not just
the card:
- "endorses": the author argues for the claim, or reports it as fact, as a research finding,
  or as what experts say, without disputing it. Ordinary news reporting counts as endorses.
- "qualifies": the author accepts the claim only with an important limit or condition that the
  card leaves out ("only if", "in some cases", "the evidence is weak", "so far").
- "disputes": the author argues against the claim, concludes the opposite, or presents it as a
  view held by others (critics, opponents) that the article then rejects or finds no support
  for. A card that reads only that set-up as if it were the author's view is a disputes.

"author_conclusion": copy word for word the one sentence (at most 40 words) from the article
that best shows the author's own position on the tag's claim.
"reason": at most 25 words.

Answer with JSON: {"verdict": "endorses" | "qualifies" | "disputes", "author_conclusion": "...",
"reason": "..."}
