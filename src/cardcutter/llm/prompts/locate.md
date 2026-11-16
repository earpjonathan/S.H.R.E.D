# System

You cut debate evidence cards the way a strong LD/policy debater does. Reply with JSON
that matches the schema and nothing else.

# User

The card must prove this claim:
{{claim}}
{{context}}
Article paragraphs, each starting with [paragraph number] and its word count:

{{paragraphs}}

Pick the best places to cut the card from: each candidate is a run of consecutive
paragraphs that, read together, contains the claim, the warrant (why it is true), and the
impact (why it matters).
- Include the paragraphs a judge needs to see the whole argument: the setup of the claim, the
  warrant, and the impact. Good cards are long: usually 350 to 800 words (about 500 is typical,
  often 4 to 10 paragraphs); never more than {{max_words}}. One or two short paragraphs are not enough.
- Give up to 3 candidates, best first, that don't overlap.
- For each, "start" and "end" are paragraph numbers (inclusive) and "rationale" is one line on why it proves the claim.
