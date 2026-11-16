# S.H.R.E.D

**S**earch, **H**ighlight, **R**ead-back, **E**mphasis, **D**ocx.
The command is still called `cardcutter`. Write-up: https://projects.jonathanearp.xyz/cards/

Cuts evidence cards for LD and policy debate. You give it an article and the claim the card has
to prove, and it gives you a finished card (tag, cite, underlining, emphasis and highlighting) in
Word formatting that works with Verbatim.

The card body is always an exact, unbroken excerpt of the source. The model only picks word
indices and never retypes text, and code checks the result before anything is exported.

What's built so far: model setup, the corpus parser and stats, the Word renderer, local and API
backends, the cut stages, few-shot retrieval from your example files, the `cardcutter cut` CLI,
the local web UI and the eval harness.

## Setup (local, free)

Needs macOS on Apple Silicon, [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com).

```bash
brew install uv ollama
uv sync
```

Pull the models named in `config.toml`:

```bash
ollama pull qwen3.8:27b-q4_K_M
ollama pull qwen3-embedding:0.6b
```

Start Ollama and leave it running, either in a terminal or as a login service:

```bash
ollama serve
```

```bash
brew services start ollama
```

Use the `-q4_K_M` (GGUF) tag. On Apple Silicon, Ollama's default `qwen3.8:27b` tag is an MLX
build, and Homebrew's Ollama ships without the library the MLX engine needs for JSON-schema
output, so structured requests to it fail with HTTP 501.

## Setup (API only)

If your laptop can't run a local model, skip Ollama and use an API preset. Keys are read from the
environment or from a git-ignored `.env` file in the project folder. They never go in
`config.toml`, and they're redacted from error messages.

```bash
cp .env.example .env
```

Fill in the keys you have, then pick a preset in `config.toml` (`[llm] preset = ...`) or per
command with `--preset`:

| preset | what runs where | cost |
|---|---|---|
| `local` (default) | everything on Ollama | free |
| `local-thorough` | everything on Ollama, with thinking on for highlighting (better highlights, about 5x slower) | free |
| `hybrid` | cutting stages local, tag and read-back on Claude | paid for tag/read-back |
| `api` | everything on Claude, embeddings on Gemini | paid |
| `free-hybrid` | model calls on Gemini's free tier, embeddings on Ollama (your example index stays local) | free, with daily limits; Google may use free-tier prompts |
| `free-api` | everything on Gemini's free tier, embeddings too (for Macs without Ollama) | free, with daily limits; Google may use free-tier prompts |

Any stage can also be pointed at any backend in `[llm.backends.*]` with
`--stage highlight=anthropic`. Supported backends are Ollama, any OpenAI-compatible server
(LM Studio, llama.cpp `llama-server`, `mlx_lm.server`), Anthropic, OpenAI, Gemini, OpenRouter
and Groq.

It won't make a paid call without telling you. The CLI prints an estimated cost and asks first,
stops at `llm.session_cap_usd`, and if Ollama isn't running it offers the `llm.fallback` backend
instead of switching to it on its own.

```bash
uv run cardcutter backends --preset api
```

This shows which backend and model each stage uses and whether it's reachable, without calling
any model.

## Cutting a card

```bash
uv run cardcutter ui
```

This opens the web UI at http://127.0.0.1:8765. It's local only and has no login.

Write the claim and press "Find sources & cut cards". It writes search queries, searches the web,
news and open-access journals (free and with no keys, through the `ddgs` metasearch library and
OpenAlex), reads the most promising articles, checks which ones actually argue the claim, and
cuts one card from each of the best. It takes one per site, and a story syndicated through Yahoo
or MSN counts as its own outlet. The ranked source list stays on screen with a "Cut this" button
on each, so you can cut from any of them.

You can also switch to "Use my own source" and paste an article, upload a PDF or give a URL. You
can add your case file too, so the card fills a gap in it.

Up to three candidate cards come back side by side. Select text (or click a word) to change the
highlighting, underlining or emphasis (keys H / U / E / X, ⌘Z to undo), click the tag to edit
it, and use "Edit cite" to fix citation fields. Export writes a new `.docx` or appends under a
heading in an existing file. Every export re-checks each card body against the source, so hand
edits can only change formatting. The "Tag A/B" tab shows a model tag and a human tag for the
same evidence from an eval run, without saying which is which, and keeps score.

From the command line:

```bash
uv run cardcutter auto --claim "Most marine protected areas are paper parks" --cards 3 --html out/auto.html
uv run cardcutter auto --claim "..." --sources-only     # just the ranked sources
uv run cardcutter cut --source https://example.org/article --claim "Legal certainty draws biotech investment"
uv run cardcutter cut --source paper.pdf --claim "..." --candidates 3 --html out/preview.html
pbpaste | uv run cardcutter cut --text - --claim "..." --context case.docx --role "internal link"
uv run cardcutter cut --source paper.pdf --claim "..." --append-to aff.docx --under "Solvency"
```

### What auto mode does before cutting

Search. The model writes keyword queries from the claim. Web, news and OpenAlex results are
downloaded and ranked by how closely their best passage matches the claim. Pages made for an
advertiser, like branded or "contributor" content, rank lower and get a note. OpenAlex without
an API key shares a small free daily budget with everyone on your IP address, and it ran out on
2026-11-12. OpenAlex gives out free keys: put one in `.env` as `OPENALEX_API_KEY`. A provider
that answers "rate limited" (HTTP 429) is skipped for the rest of that search, and DuckDuckGo
errors are retried after 2, 6 and 15 seconds.

Checking each source. Every source's best passage gets two scores from 1 to 5: does it argue the
claim, and how far would a judge trust the source? A named expert, think tank or major outlet
scores high, and glossaries, anonymous pages and content farms score low. Reference and glossary
pages, Wikipedia, and essay or homework sites always get a 1 and are set aside. Tabloids (The
Sun, Daily Mail, Express and so on) get 2 at most. They can still be used, but they rank below
real outlets and their cards get a "weak source" note. A source that doesn't argue the claim is
never cut. If fewer sites pass than the number of cards you asked for plus two (because some cuts
fail), it searches again with new queries (`search.rounds`).

Checking the claim. First the model splits the claim into the parts a card has to show. A claim
that links two things has two parts, like "arms to Ukraine drain US stockpiles" and "those
stockpiles are needed to deter China over Taiwan". A simple claim has one. After cutting, the
judge reads the card aloud against the claim you searched for, as well as its own tag, and says
which parts the read-aloud words show. A card that drifts off the claim (1-2/5) is dropped. A
card that never shows one of the parts, or only shows it weakly, is held back (3/5) while auto
mode looks for a card that proves the whole claim. A card doesn't have to repeat the claim's
words. If no card proves the whole claim, held-back cards fill the empty slots, starting with
cards that show a part no other card shows, and each one gets a warning naming the part it
misses (`search.claim_parts`).

Searching again. Once three cards have failed the claim check (or the sources run out after one
has), auto mode searches one more time. The new queries go after what those cards were missing:
the part of the claim no card has shown, or the link between parts that cards only showed one at
a time. It cuts the new sources first, with up to four more tries. A site whose card was only
held back can be tried again with a different article (`search.link_search`, `link_after`,
`link_tries`). A card that fails the read-back is only used if nothing better turns up and it
still proves its tag. Otherwise auto mode returns fewer cards.

### The cutting pipeline

1. Locate picks the best run of paragraphs, using an embedding shortlist first for long
   articles. Cards are long: 350 to 900 words. It can grow past a short subheading if it needs
   to, and it never runs into the author box or the "related stories" at the end of a page.
2. Underline, highlight and emphasis each pick word indices, with your most similar example cards
   as few-shot examples.
   - Underlining marks whole clauses (subject, verb and what it acts on) through to the end of
     the card. Top-up (on) asks again when the underline is thin, or when paragraphs with nothing
     underlined make up a quarter of the card. The model sees those paragraphs and underlines any
     that give a warrant or impact.
   - Edges: a card runs from its first underlined paragraph to its last. It starts at a sentence
     (never mid-sentence after a PDF page break) and keeps at most about 40 unmarked words before
     the first mark and after the last (`cut.drop_unmarked_edges`). Your files leave a median 3%
     of the card unmarked after the last underline.
   - Cut style. This changed on 2026-11-11, after feedback that the overnight cards were "way too
     short", "badly cut" and boxed too much. Highlighting now reads in phrases that keep each
     sentence's subject and verb (`highlight_fluent.md`), about 50 to 65 words on a 400 to 700
     word card (`cut.density = "long"`, close to the longer cards in the example files). Your
     files compress harder, into pieces of about 2 words. The old style is still available with
     `density = "normal"`, `highlight_prompt = "highlight"`, `trim_highlight = true`,
     `min_window_words = 150` and `max_window_words = 650`.
   - Smoothing (on) lets the model add a few words from the same sentence when a read piece has
     no subject or no verb ("possesses 2,000 tactical nukes" becomes "Russia possesses 2,000
     tactical nukes"). An added word gets underlined too. It adds at most 20% more words.
   - Subjects: a read piece that starts with a helping or reporting verb ("has exposed Europe's
     weaknesses", "concludes that") gets the short subject from the start of its sentence ("The
     war in Ukraine", "Harrel"), at most six words (`cut.restore_subjects`).
   - Over-length trim: a highlight more than 1.5 times its target gets cut back by whole
     sentences. The model ranks the read sentences (`trim_rank.md`) and the code keeps the most
     needed ones, up to 1.2 times the target. The sentences it keeps aren't changed.
   - The old trim (`trim_highlight = true`, now off) cuts the highlighting down to your team's
     short pieces. It only deletes, and it puts "not", "only" and "few" back if it drops them
     while keeping the word they apply to. On the dev split it raised highlight F1 from 37.4 to
     39.7, and a repair step that adds back a few dropped words took it to 40.1. It was on
     because it scored better against your files, and I turned it off because of the feedback.
   - Boxes (emphasis): a few boxes of one or two words (three for a number with its unit), only
     on read words, and at most about one boxed word per ten read (`cut.emphasis_share = 0.10`).
     Code enforces the cap whatever the model picks. Your files box about half the read words.
   - A highlighted sentence that only repeats an earlier highlighted point gets unhighlighted.
   - Gap filling (on, `cut.bridge_gaps = 2`) highlights a gap of one or two underlined words
     between highlighted words in the same sentence, unless the gap is only "a/an/the":
     "Ukraine shows no signs of capitulating", "Neither side can rely". On saved cuts it raised
     highlight F1 from 39.7 to 40.6 (dev) and 38.1 to 39.5 (rest).
   - Dangling negators: if a highlighted "not", "isn't" or "no" has its word left unread, that
     word gets highlighted when it's underlined ("would not go off"). Otherwise the piece is
     dropped when nothing is read right after it, because "the goal isn't … Russia at a
     disadvantage" would say the opposite of the article.
3. The tag is written from the highlighted text, in plain words about the world (not "solves the
   link"), and is never stronger than the card.
4. The cite comes from PDF or page metadata, and from Crossref for DOIs. A page's `<h1>` headline
   is used as its title when the `<title>` tag is an SEO line that doesn't match the URL. Quals
   come from these, in order:
   - the journal's own author-affiliation tags
   - OpenAlex affiliations, looked up by DOI (only the DOI is sent). For an arXiv preprint, or
     when OpenAlex has one affiliation string for every author, these are only used if the
     paper itself names none.
   - the page's author boxes and schema.org author data (job title, employer)
   - the author's profile page, when the article has no bio

   The model can copy names and quals from that text, matched to authors by surname, but anything
   not found there word for word gets dropped. A bio box is cut to its first sentence, without
   the author's own name ("graduated from Patrick Henry College's …"). Cites with more than three
   authors read "Bennett et al. 17", with quals for the first three. A field nobody knows shows
   up as a visible marker like `[quals?]`. You can override any field with `--author`,
   `--quals`, `--date`, `--title`, `--publication` or `--url`.
5. Read-back scores the highlighted text read aloud (grammar, coherence, and whether it proves
   the tag, 1-5 each) and warns when the card scores below 3 or might be a power-tag. With the
   old trim on, the trimmed highlight is always kept (`readback.untrimmed_fallback = true` lets
   it hear the untrimmed one too). It doesn't re-cut by default. In evals, re-cuts helped about
   as many cards as they hurt and took twice as long (`readback.max_retries = 1` turns one re-cut
   back on).
6. Author's view reads the card in the context of the whole article (its opening, sentences like
   "we found" or "our results show", and its ending) and asks whether the author endorses,
   qualifies or disputes the card's claim. This catches cards that read a view the author only
   brings up to reject ("critics argue X … we found no evidence of X"). The model has to quote
   the author's conclusion, and the quote is only shown if it's in the article word for word. A
   "disputes" or "qualifies" verdict adds a warning to the card, and auto mode never uses a card
   the author disputes. It takes about 15 s per card locally. Turn it off with
   `cut.author_check = false`.
7. Verify checks that the body is an exact excerpt starting on a sentence boundary. If anything
   fails, nothing is exported.

`uv run cardcutter suggest --context case.docx` lists links the case is missing.

## Corpus and eval

Put your Verbatim example files in `examples/` (it's git-ignored), then:

```bash
uv run cardcutter corpus build
```

This parses every `examples/*.docx` into `data/cards.jsonl` and writes `data/stats.md` and
`data/stats.json`: card counts, underline and highlight density, partial-word highlights, cite
formats, highlight colours and nesting.

```bash
uv run cardcutter corpus template
```

This builds `data/template.docx` from `render.template_source`'s styles, so output pastes into
Verbatim and looks the same.

```bash
uv run cardcutter corpus roundtrip
```

This parses, renders and re-parses every card, and fails unless all of them match exactly. It
also writes three sample cards to `out/roundtrip-sample.docx`.

```bash
uv run cardcutter render tests/fixtures/handwritten_card.json --out out/card.docx
```

This renders card JSON to a new `.docx`. Add `--append-to case.docx --under "Block heading"` to
put the card at the end of that heading's section instead (a `.bak` copy is kept if you
overwrite the file in place).

```bash
uv run cardcutter eval --limit 5
```

This cuts held-out corpus cards from their plain text and tag, then scores them against the
human cut. The held-out set is the 100 cards with the most unformatted text, picked once and
saved to `data/holdout.json`. Scores are character-level precision, recall and F1 for underline,
highlight and emphasis, plus highlight density error, integrity pass rate, and seconds and cost
per card. Each run writes `eval/reports/<timestamp>.md`, and `uv run cardcutter eval --compare`
prints every run side by side. Prompts live in `src/cardcutter/llm/prompts/*.md`, so re-run the
eval after changing one.

Tune on the dev split and report on the test split, so the test numbers aren't ones you tuned
for:

```bash
uv run cardcutter eval --split dev --no-tag --label "try X"                 # holdout cards 31-60, cut stages only
uv run cardcutter eval --split dev --set retrieval.example_format=indexed   # any config key, this run only
uv run cardcutter eval --split test --label "release"                        # holdout cards 1-30, full pipeline
```

The eval tells the cutter each card's place in its file (pocket > hat > block) and the previous
card's tag, since a debater would know those. A lot of tags ("And HIMARs.") only make sense
after the one before (`eval.file_context`).

## Card format

A card is three formatted texts (tag, cite, body). Body paragraphs are joined with `\n`. Each
formatting attribute is stored per character and saved as run-length spans, `[start, end]` or
`[start, end, value]`:

| attribute | meaning |
|---|---|
| `underline` | the read-through (`StyleUnderline`, or direct `w:u`) |
| `emphasis` | boxed key words (`Emphasis` style, or a direct box border) |
| `highlight` | what's read aloud: a Word highlight colour, or `shd:RRGGBB` for shading |
| `cite13` | the short cite (`Style13ptBold`) |
| `size` | font size in half-points (16 = 8 pt shrunk text) |
| `bold`, `italic`, `strike`, `vert` | direct formatting |

## Tests

```bash
uv run pytest
```

The tests cover tokenizer round trips (with Hypothesis, including curly quotes, em dashes,
non-breaking spaces and soft hyphens), masks to `.docx` and back, the integrity verifier, and a
full round trip of every example card (skipped when `examples/` is empty).

## Privacy

`examples/`, `data/`, `out/`, `.env` and every `.docx` file except the test fixtures are
git-ignored. Don't commit evidence files or generated cards to a public repo.
