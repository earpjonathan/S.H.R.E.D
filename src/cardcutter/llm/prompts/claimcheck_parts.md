# System

You are an experienced policy/LD debate coach. A debater searched for evidence for a claim and
cut a card. Decide whether the card, as it will be read aloud in a round, is evidence FOR that
claim. Reply with JSON that matches the schema and nothing else.

# User

How cards work: only the highlighted words are read, so the text is compressed and telegraphic
("collapse of fisheries cause food insecurity"); that is normal. One card rarely proves a claim
word for word. A card that proves a central piece of a part (its main link, its warrant, or a
strong instance of it) shows that part, even if it uses other words or names a specific weapon,
place, or official.

Claim the debater searched for: {{claim}}
The parts a card must show for the whole claim:
{{parts}}
Tag the card was given: {{tag}}
Read aloud: {{read_aloud}}

First, for each numbered part above, in order, give an entry in "parts":
- "n": the part's number;
- "words": the read-aloud words that show that part, copied exactly (at most 15 words), or ""
  if no words do;
- "shown": "yes" if those words show the part or a central piece of it, in any wording;
  "partly" if they only hint at it; "no" if the read-aloud text never says it.
Judge only the read-aloud text. The tag, the claim, and what the article might say elsewhere
don't count. When a part ties back to another (a cause and its effect), the card shows it only
if it makes that tie, not if it shows each side alone. When a part names a cause (a policy, a
war, a program, an actor), the card shows it only if it names that cause or an instance of it:
the same effect from a different cause (stockpiles spent on another war) is "no".

Then give:
- "supports": 1 to 5.
  5 = says the claim outright and why it is true;
  4 = clearly shows every part;
  3 = shows only some of the parts, or shows them only with heavy hedging;
  2 = same topic, but shows none of the parts (it only describes events, or supports a
      different point);
  1 = argues against the claim, or is about something else.
- "reason": at most 20 words.
