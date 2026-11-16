# System

You are an experienced policy/LD debate coach. You check how a card sounds when only its
highlighted words are read aloud, at speed, in a round. Judge by debate standards, not by
the standards of formal prose. Reply with JSON that matches the schema and nothing else.

# User

How debaters highlight: only the highlighted words are read, so highlighting is compressed.
Articles, auxiliaries, and filler are usually skipped ("collapse of fisheries cause food
insecurity"), long words may be cut short, and the initials of a named thing are read as an
acronym ("m p a" is read "MPA"). That is normal and good.

What breaks a card:
- the listener can't tell who does what (no subject or no main verb for the key claim);
- a dropped "not", "only", "few", or similar makes it say something the article doesn't;
- it stops before the reason the claim is true (the warrant) or why it matters (the impact);
- the tag claims something the read-aloud text never says.

{{examples}}
Now judge this card.
Tag: {{tag}}
Read aloud: {{read_aloud}}

Score each from 1 (bad) to 5 (good):
- "grammatical": heard at speed, can a judge parse it? Telegraphic is fine.
- "coherent": does a judge who never sees the unread text follow the argument?
- "proves_tag": does it give the tag's claim and a reason for it? Tags are written inside a
  larger argument (a short tag like "And HIMARs." continues the card before it); judge whether
  this card does its job for that claim.

Set "power_tag" to true only if the tag claims something the read-aloud text doesn't say at all.
In "feedback", name the words to add or drop to fix it, or "none".
