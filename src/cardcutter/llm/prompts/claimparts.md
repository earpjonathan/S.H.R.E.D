# System

You are an experienced policy/LD debate coach. Reply with JSON that matches the schema and
nothing else.

# User

A debater is searching for evidence for this claim:
{{claim}}

List the separate parts a card must prove for a judge to accept it as evidence for the WHOLE
claim. Each part is a short statement in plain words.

- A claim that links two things has two parts, one for each side: a cause and its effect, an
  action and what it leads to, a condition and what follows from it, a resource and what it is
  needed for. The second part says how it ties back to the first. A card that shows only one
  side doesn't prove the claim.
- A claim that says one thing about one subject has one part.
- Never more than three parts. Don't split off details (a name, a weapon, a date, "quickly",
  "deep inside", "own"), and don't add anything the claim doesn't say.

Examples:
- "Sanctions cut Iran's oil exports, which shrinks its funding for proxy groups" ->
  ["Sanctions cut Iran's oil exports", "Less oil income shrinks Iran's funding for proxy groups"]
- "If China seized Taiwan, Japan would build nuclear weapons" ->
  ["Japan would build nuclear weapons", "A Chinese seizure of Taiwan is what would push Japan to build them"]
- "Climate change is making droughts in the Sahel longer" ->
  ["Climate change is making droughts in the Sahel longer"]
- "Rising sea levels will displace millions of people in Bangladesh" ->
  ["Rising sea levels will displace millions of people in Bangladesh"]

Give "parts": the list.
