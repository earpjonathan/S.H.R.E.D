# S.H.R.E.D

**S**earch, **H**ighlight, **R**ead-back, **E**mphasis, **D**ocx.
The command is still called `cardcutter`. Write-up: https://projects.jonathanearp.xyz/cards/

Cuts debate evidence cards the way a strong LD/policy debater would. You give it an article and
the claim the card must prove; it gives you a finished card (tag, cite, underlining, emphasis,
highlighting) in Verbatim-compatible Word formatting.

The card body is always an exact, contiguous excerpt of the source. The model only selects word
indices, never retypes text, and code checks the result before anything is exported.

**Status:** milestones 0–6 are built: model setup, corpus parser and stats, Word renderer,
backend layer (local and API), the cut stages (locate, underline, highlight, emphasis, tag,
cite, read-back), few-shot retrieval from your example files, the `cardcutter cut` CLI, the
local web UI, and the eval harness.

## Setup (local, free)

Needs macOS on Apple Silicon, [uv](https://docs.astral.sh/uv/), and [Ollama](https://ollama.com).

```bash
brew install uv ollama
uv sync
```

Pull the models named in `config.toml`:

```bash
ollama pull qwen3.8:27b-q4_K_M
ollama pull qwen3-embedding:0.6b
```

Start Ollama (leave it running), either in a terminal or as a login service:

```bash
ollama serve
```

```bash
brew services start ollama
```

> Use the `-q4_K_M` (GGUF) tag. On Apple Silicon, Ollama's default `qwen3.8:27b` tag is an MLX
> build, and Homebrew's Ollama ships without the library the MLX engine needs for JSON-schema
> output, so structured requests to it fail with HTTP 501.

## Setup (API only)

For a laptop that can't run a local model, skip Ollama and use an API preset. Keys are read
from the environment or a git-ignored `.env` file in the project folder; they never go in
`config.toml` and are redacted from error messages.

```bash
cp .env.example .env
```

Then fill in the keys you have and pick a preset in `config.toml` (`[llm] preset = ...`) or per
command with `--preset`:

| preset | what runs where | cost |
|---|---|---|
| `local` (default) | everything on Ollama | free |
| `local-thorough` | everything on Ollama, thinking on for highlighting (better highlights, about 5x slower) | free |
| `hybrid` | cutting stages local; tag and read-back on Claude | paid for tag/read-back |
| `api` | everything on Claude; embeddings on Gemini | paid |
| `free-hybrid` | model calls on Gemini's free tier, embeddings on Ollama (your example index stays local) | free, with daily limits; Google may use free-tier prompts |
| `free-api` | everything on Gemini's free tier, embeddings too (for Macs without Ollama) | free, with daily limits; Google may use free-tier prompts |

Every stage can also be pointed at any backend in `[llm.backends.*]` with
`--stage highlight=anthropic`. Supported backends: Ollama, any OpenAI-compatible server
(LM Studio, llama.cpp `llama-server`, `mlx_lm.server`), Anthropic, OpenAI, Gemini, OpenRouter,
and Groq.

Paid calls are never made silently: the CLI prints an estimated cost and asks first, stops at
`llm.session_cap_usd`, and only offers (never takes) the `llm.fallback` backend when Ollama
isn't running.

```bash
uv run cardcutter backends --preset api
```

Shows which backend and model each stage uses and whether it's reachable, without calling any model.

## Cutting a card

```bash
uv run cardcutter ui
```

Opens the web UI at http://127.0.0.1:8765 (local only; it has no login). Write the claim and press
**Find sources & cut cards**: CardCutter writes search queries, searches the web, news, and
open-access journals (free, no keys: the `ddgs` metasearch library and OpenAlex), reads the most
promising articles, checks which ones actually argue the claim, and cuts one card from each of the
best (one per site; a story syndicated through Yahoo or MSN counts as its own outlet). The ranked source list stays on screen with a **Cut this** button on each, so
you can cut from any of them. Or switch to **Use my own source**: paste an article, upload a PDF,
or give a URL. Optionally add your case file so the card fills a gap.
Up to three candidate cards come back side by side. Select text (or click a word) to change
highlighting, underlining, or emphasis (keys H / U / E / X, ⌘Z to undo), click the tag to edit
it, and use "Edit cite" to fix citation fields. Export writes a new `.docx` or appends under a
heading in an existing file. Every export re-checks each card body against the source: hand
edits can change formatting, never words. The "Tag A/B" tab shows a model tag and a human tag
for the same evidence from an eval run, blind, and keeps score.

From the command line:

```bash
uv run cardcutter auto --claim "Most marine protected areas are paper parks" --cards 3 --html out/auto.html
uv run cardcutter auto --claim "..." --sources-only     # just the ranked sources
uv run cardcutter cut --source https://example.org/article --claim "Legal certainty draws biotech investment"
uv run cardcutter cut --source paper.pdf --claim "..." --candidates 3 --html out/preview.html
pbpaste | uv run cardcutter cut --text - --claim "..." --context case.docx --role "internal link"
uv run cardcutter cut --source paper.pdf --claim "..." --append-to aff.docx --under "Solvency"
```

Auto mode, before the pipeline below:
- **Search**: the model writes keyword queries from the claim; web, news, and OpenAlex results are
  downloaded and ranked by how closely their best passage matches the claim. Pages made for an
  advertiser, such as branded or "contributor" content, rank lower and carry a note.
  OpenAlex without an API key shares a small free daily budget with everyone on your IP address;
  it ran out on 2026-11-12. OpenAlex gives free keys: put one in `.env` as `OPENALEX_API_KEY`.
  A provider that answers "rate limited" (HTTP 429) is skipped for the rest of that search, and
  DuckDuckGo errors are retried after 2, 6 and 15 seconds.
- **Stance and source check**: every source's best passage is checked: does it argue the claim
  (1–5), and how far would a judge trust the source (1–5: a named expert, think tank, or major
  outlet scores high; glossaries, anonymous pages, and content farms score low)? Reference and
  glossary pages, Wikipedia, and essay or homework sites are always rated 1 and set aside.
  Tabloids (The Sun, Daily Mail, Express, …) are rated 2 at most: still usable, but ranked below
  real outlets, and their cards carry a "weak source" note.
  A source that doesn't argue the claim is never cut. If fewer than two more sites than the
  cards you asked for pass (some cuts fail), it searches again with new queries
  (`search.rounds`).
- **Claim check**: first the model splits the claim into the parts a card must show (a claim that
  links two things has two: "arms to Ukraine drain US stockpiles" and "those stockpiles are
  needed to deter China over Taiwan"; a simple claim has one). After cutting, the judge reads the
  card aloud against the claim you searched for, not just its own tag, and says which parts the
  read-aloud words show. A card that drifts off the claim is dropped (1–2/5); one that never
  shows a part of the claim, or proves it only weakly, is held back (3/5) while auto mode looks
  for a card that proves the whole claim. A card needn't repeat the claim's words. If no card
  proves the whole claim, held-back cards fill the empty places, a card showing a part no other
  card shows first, with a warning naming the part it misses (`search.claim_parts`).
- **Second search**: once three cards have failed the claim check (or the sources run out after
  one has), auto mode searches once more. The new queries aim at what those cards lack: the part
  of the claim no card has shown, or the tie between parts that cards showed only one at a time.
  It cuts the new sources first, with up to four more attempts. A site whose card was only held
  back may be tried again with another article (`search.link_search`, `link_after`, `link_tries`).
  A card that fails the read-back is used only if nothing better turns up and it still proves its
  tag. Auto mode would rather return fewer cards than bad ones.

The pipeline:
1. **Locate** picks the best run of paragraphs (embedding shortlist first for long articles).
   Cards are long: 350 to 900 words, growing past a short subheading if needed, and never
   running into the author box or "related stories" at the end of a page.
2. **Underline**, **highlight**, and **emphasis** select word indices, using your most similar
   example cards as few-shot examples.
   - **Underline** marks whole clauses (subject, verb, and what it acts on) through to the end
     of the card. **Top-up** (on) asks again when the underline is thin, or when paragraphs with
     nothing underlined hold a quarter of the card: the model sees them and underlines any that
     give a warrant or impact.
   - **Edges**: a card runs from its first underlined paragraph to its last. It starts at a
     sentence (never mid-sentence after a PDF page break) and keeps at most about 40 unmarked
     words before the first mark and after the last (`cut.drop_unmarked_edges`). Your files
     leave a median 3% of the card unmarked after the last underline.
   - **Cut style** (changed 2026-11-11, after feedback that the overnight cards were "way too
     short", "badly cut", and boxed too much). The highlighting now reads in phrases that keep
     each sentence's subject and verb (`highlight_fluent.md`), about 50 to 65 words on a
     400 to 700 word card (`cut.density = "long"`, close to the longer-card example files). Your files
     compress harder (pieces of about 2 words); the old style is still there:
     `density = "normal"`, `highlight_prompt = "highlight"`, `trim_highlight = true`,
     `min_window_words = 150`, `max_window_words = 650`.
   - **Smooth** (on) lets the model add a few words of the same sentence where a read piece
     has no subject or no verb ("possesses 2,000 tactical nukes" becomes "Russia possesses
     2,000 tactical nukes"). An added word is underlined too. At most 20% more words.
   - **Subjects**: a read piece that starts with a helping or reporting verb ("has exposed
     Europe's weaknesses", "concludes that") gets the short subject from the start of its
     sentence ("The war in Ukraine", "Harrel"), at most six words (`cut.restore_subjects`).
   - **Over-length trim**: a highlight more than 1.5 times its target is cut back by whole
     sentences: the model ranks the read sentences (`trim_rank.md`) and the code keeps the most
     needed ones, up to 1.2 times the target. Kept sentences are untouched.
   - The old **Trim** (`trim_highlight = true`, off now) cuts the highlighting down to your
     team's short pieces. It is deletion-only, and "not", "only", "few" are put back if it
     drops them while keeping the word they apply to. On the dev split it raised highlight F1
     from 37.4 to 39.7. **Repair** then adds back a few dropped words (F1 39.7 to 40.1).
     F1 against your files is why it was on; the feedback is why it is off.
   - **Boxes** (emphasis): a few one- or two-word boxes (three for a number with its unit), on
     read words only, at most about one boxed word per ten read (`cut.emphasis_share = 0.10`).
     Code enforces the cap whatever the model picks. Your files box about half the read words.
   - A highlighted sentence that only repeats an earlier highlighted point is unhighlighted.
   - **Gap filling** (on, `cut.bridge_gaps = 2`) highlights a gap of one or two underlined
     words between highlighted words of the same sentence, unless the gap is only "a/an/the":
     "Ukraine shows no signs of capitulating", "Neither side can rely". On saved cuts it raised
     highlight F1 from 39.7 to 40.6 (dev) and 38.1 to 39.5 (rest).
   - **Dangling negators**: a highlighted "not", "isn't", or "no" whose word is left unread
     gets that word highlighted if it's underlined ("would not go off"). Otherwise the piece
     is dropped when nothing is read right after it: "the goal isn't … Russia at a
     disadvantage" would say the opposite of the article.
3. **Tag** is written from the highlighted text, in plain words about the world (not "solves the
   link"), and never stronger than the card.
4. **Cite** comes from PDF / page metadata and Crossref (for DOIs). A page's `<h1>` headline
   is its title when the `<title>` tag is an SEO line that doesn't match the URL. Quals come from, in order:
   the journal's own author-affiliation tags, OpenAlex affiliations (by DOI; only the DOI is
   sent; for an arXiv preprint, or one affiliation string for every author, only if the paper
   itself names none), and the page's author boxes and schema.org author data (job title, employer), or the
   author's profile page when the article has no bio. The model may copy names and quals from
   that text, matched to authors by surname, but anything not found word for word in it is
   dropped. A bio box is cut to its first sentence, without the author's own name
   ("graduated from Patrick Henry College's …"). Cites with more than three authors read
   "Bennett et al. 17", with quals for the first three. A field nobody knows becomes a visible marker like `[quals?]`; override
   any field with `--author`, `--quals`, `--date`, `--title`, `--publication`, `--url`.
5. **Read-back** scores the highlighted text read aloud (grammatical, coherent, proves the tag;
   1–5 each) and warns when the card scores below 3 or may be a power-tag. With the old trim on,
   the trimmed highlight is always kept (`readback.untrimmed_fallback = true` lets it hear the
   untrimmed one too). It doesn't re-cut by default: in evals,
   re-cuts helped some cards and hurt others about equally, at twice the time
   (`readback.max_retries = 1` turns one re-cut back on).
6. **Author's view** reads the card in the context of the whole article (its opening, sentences
   like "we found" or "our results show", and its ending) and asks whether the author
   *endorses*, *qualifies*, or *disputes* the card's claim. It catches cards that read a view
   the author sets up only to reject ("critics argue X … we found no evidence of X"). The model
   must quote the author's conclusion, and the quote is shown only if it is in the article word
   for word. A *disputes* or *qualifies* verdict adds a warning to the card; auto mode never
   uses a card the author disputes. About 15 s per card locally; turn it off with
   `cut.author_check = false`.
7. **Verify** checks the body is an exact excerpt that starts on a sentence boundary. If
   anything fails, nothing is exported.

`uv run cardcutter suggest --context case.docx` lists links the case is missing.

## Corpus and eval

Put your Verbatim example files in `examples/` (git-ignored), then:

```bash
uv run cardcutter corpus build
```

Parses every `examples/*.docx` into `data/cards.jsonl` and writes `data/stats.md` and
`data/stats.json`: card counts, underline and highlight density, partial-word highlights, cite
formats, highlight colors, and nesting.

```bash
uv run cardcutter corpus template
```

Builds `data/template.docx` from `render.template_source`'s styles, so output pastes into Verbatim
and looks identical.

```bash
uv run cardcutter corpus roundtrip
```

Parses, renders, and re-parses every card and fails unless all of them match exactly. Also writes
three sample cards to `out/roundtrip-sample.docx`.

```bash
uv run cardcutter render tests/fixtures/handwritten_card.json --out out/card.docx
```

Renders card JSON to a new `.docx`. Add `--append-to case.docx --under "Block heading"` to insert
the card at the end of that heading's section instead (a `.bak` copy is kept if you overwrite the
file in place).

```bash
uv run cardcutter eval --limit 5
```

Cuts held-out corpus cards (the 100 with the most unformatted text, chosen once and saved to
`data/holdout.json`) from their plain text and tag, then scores them against the human cut:
character-level precision / recall / F1 for underline, highlight and emphasis, highlight
density error, integrity pass rate, seconds and cost per card. Each run writes
`eval/reports/<timestamp>.md`; `uv run cardcutter eval --compare` prints every run side by side.
Prompts live in `src/cardcutter/llm/prompts/*.md`; re-run the eval after changing one.

Tune on the dev split and report on the test split, so the test numbers stay honest:

```bash
uv run cardcutter eval --split dev --no-tag --label "try X"                 # holdout cards 31-60, cut stages only
uv run cardcutter eval --split dev --set retrieval.example_format=indexed   # any config key, this run only
uv run cardcutter eval --split test --label "release"                        # holdout cards 1-30, full pipeline
```

The eval gives the cutter each card's place in its file (pocket > hat > block) and the previous
card's tag, as a debater would know; many tags ("And HIMARs.") only make sense after the one
before (`eval.file_context`).

## Card format

A card is three formatted texts (tag, cite, body). Body paragraphs are joined with `\n`. Each
formatting attribute is stored per character and saved as run-length spans `[start, end]` or
`[start, end, value]`:

| attribute | meaning |
|---|---|
| `underline` | the read-through (`StyleUnderline`, or direct `w:u`) |
| `emphasis` | boxed key words (`Emphasis` style, or a direct box border) |
| `highlight` | what is read aloud: a Word highlight color, or `shd:RRGGBB` for shading |
| `cite13` | the short cite (`Style13ptBold`) |
| `size` | font size in half-points (16 = 8 pt shrunk text) |
| `bold`, `italic`, `strike`, `vert` | direct formatting |

## Tests

```bash
uv run pytest
```

Covers tokenizer round trips (Hypothesis, with curly quotes, em dashes, non-breaking spaces and
soft hyphens), masks → `.docx` → masks, the integrity verifier, and a full round trip of every
example card (skipped when `examples/` is empty).

## Privacy

`examples/`, `data/`, `out/`, `.env`, and all `.docx` files except test fixtures are git-ignored.
Don't commit evidence files or generated cards to a public repo.
