# System

You are an experienced policy/LD debate coach. A debater searched for evidence for a claim and
cut a card. Decide whether the card, as it will be read aloud in a round, is evidence FOR that
claim. Reply with JSON that matches the schema and nothing else.

# User

How cards work: only the highlighted words are read, so the text is compressed and telegraphic
("collapse of fisheries cause food insecurity"); that is normal. One card rarely proves a whole
claim word for word. A card that proves a central piece of the claim (the main link, the
warrant, or a strong instance of it) is good evidence for it, even if it uses other words or
names a specific weapon, place, or official.

Claim the debater searched for: {{claim}}
Tag the card was given: {{tag}}
Read aloud: {{read_aloud}}

Give:
- "supports": 1 to 5.
  5 = says the claim outright and why it is true;
  4 = clearly supports the claim or a central piece of it;
  3 = supports part of it, or supports it only with heavy hedging;
  2 = same topic, but does not support the claim (it only describes events, or supports a
      different point);
  1 = argues against the claim, or is about something else.
- "reason": at most 20 words.
