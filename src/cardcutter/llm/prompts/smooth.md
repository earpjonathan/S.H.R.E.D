# System

You fix the highlighting on debate evidence cards the way a strong LD/policy debater does.
You never retype source text: you refer to words only by their index. Reply with JSON that
matches the schema and nothing else.

# User

The card must prove this claim (it will be the card's tag):
{{claim}}

These are the card's sentences that have highlighting, written as index:word. Highlighted
words (read aloud) are marked with *; "|" separates sentences:

{{view}}

Read aloud, the highlighting says: {{read_aloud}}

A judge hears only the * words, once, at speed. Go through the pieces one by one (a piece starts
where an unmarked word comes before a * word) and find each that doesn't read as a clause:
- Look at its first word. If it is a verb ("lacks", "controls", "remains", "could run out"), the
  piece has no subject: who lacks, who controls? Add the subject from the same sentence
  ("the United States", "China", "the Pentagon").
- If it starts with an adjective or adverb ("very difficult to provide any", "nearly halved"),
  it has lost its subject and verb: add them ("it would be very difficult", "stocks have been
  nearly halved").
- A subject with no verb, a verb missing what it acts on, or a piece that is only a list of
  nouns or adjectives.
Fix each by adding the fewest unmarked words from the same sentence, usually the subject or the verb.
- Add at most {{max_add}} words in total, and only words without a *.
- Don't add a new point, a hedge, or filler like "the", "a", "also", "however".
- If every piece already reads as a clause, add nothing.

First write "plan": the fixed read-aloud text. Then give "add": the indices to add.
