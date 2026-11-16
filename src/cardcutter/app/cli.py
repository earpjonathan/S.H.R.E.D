"""`cardcutter` command line."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

import typer

from cardcutter.card import Card
from cardcutter.config import load_config

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Cut debate cards the way a strong debater would.")
corpus_app = typer.Typer(no_args_is_help=True, help="Parse the example files and measure them.")
app.add_typer(corpus_app, name="corpus")

ConfigOpt = typer.Option(None, "--config", help="Path to config.toml (default: search upward from cwd).")


@corpus_app.command("build")
def corpus_build(config: Optional[Path] = ConfigOpt) -> None:
    """Parse examples/*.docx into cards.jsonl and write stats (json + markdown)."""
    from cardcutter.corpus.build import build_corpus

    cfg = load_config(config)
    cards, stats = build_corpus(cfg)
    typer.echo(f"{len(cards)} cards -> {cfg.path_of('corpus.cards_path')}")
    typer.echo(f"stats -> {cfg.path_of('corpus.stats_md')}")
    typer.echo("")
    typer.echo(cfg.path_of("corpus.stats_md").read_text())


@corpus_app.command("template")
def corpus_template(config: Optional[Path] = ConfigOpt) -> None:
    """Build the output template from render.template_source's styles.xml."""
    from cardcutter.render.template import build_template

    cfg = load_config(config)
    out = build_template(cfg.path_of("render.template_source"), cfg.path_of("render.template_path"))
    typer.echo(f"template -> {out}")


@corpus_app.command("roundtrip")
def corpus_roundtrip(
    sample: int = typer.Option(3, help="How many cards to also write to out/ for viewing in Word."),
    ids: Optional[list[str]] = typer.Option(None, "--id", help="Card ids to sample (default: varied picks)."),
    seed: int = typer.Option(7),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Parse -> render -> parse every card and require an exact match."""
    from cardcutter.corpus.build import example_files, load_cards
    from cardcutter.corpus.roundtrip import roundtrip, roundtrip_corpus
    from cardcutter.render.template import build_template

    cfg = load_config(config)
    cards = load_cards(cfg)
    results = roundtrip_corpus(example_files(cfg), cards)
    bad = 0
    for f, r in results.items():
        typer.echo(f"{f:32} {r.checked:5} cards  {len(r.mismatches)} mismatches")
        for m in r.mismatches[:5]:
            typer.echo(f"    {m}")
        bad += len(r.mismatches)

    tpl = cfg.path_of("render.template_path")
    if not tpl.exists():
        build_template(cfg.path_of("render.template_source"), tpl)
    picks = [c for c in cards if c.meta["id"] in set(ids)] if ids else varied_sample(cards, sample, seed)
    out = cfg.root / "out" / "roundtrip-sample.docx"
    r = roundtrip(picks, tpl, out)
    typer.echo(f"\nsample of {len(picks)} -> {out}  ({len(r.mismatches)} mismatches)")
    for c in picks:
        typer.echo(f"  {c.meta['id']}  [{c.meta['file']}]  {c.tag.text[:70]}")
    raise typer.Exit(code=1 if bad or r.mismatches else 0)


def varied_sample(cards: list[Card], n: int, seed: int) -> list[Card]:
    """One card per highlight style where possible: yellow, cyan, shading; with partial words."""
    from cardcutter.corpus.stats import partial_words

    rng = random.Random(seed)
    pool = [c for c in cards if 120 <= len(c.body.text.split()) <= 260 and partial_words(c.body, "highlight")]
    rng.shuffle(pool)
    want = ["yellow", "cyan", "shd:"]
    picks: list[Card] = []
    for w in want:
        for c in pool:
            colors = {h for h in c.body.marks["highlight"] if h}
            if any(col.startswith(w) for col in colors) and c not in picks:
                picks.append(c)
                break
    for c in pool:
        if len(picks) >= n:
            break
        if c not in picks:
            picks.append(c)
    return picks[:n]


@app.command("render")
def render_cmd(
    card_json: Path = typer.Argument(..., help="A card JSON file (one card, or a list of cards)."),
    out: Path = typer.Option(Path("out/card.docx"), "--out", help="Output .docx (new file mode)."),
    append_to: Optional[Path] = typer.Option(None, help="Append into this existing .docx instead."),
    under: Optional[str] = typer.Option(None, help="Heading text to append under (with --append-to)."),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Render card JSON to Verbatim-formatted .docx."""
    from cardcutter.render.docx import append_under_heading, write_new
    from cardcutter.render.template import build_template

    cfg = load_config(config)
    obj = json.loads(card_json.read_text(encoding="utf-8"))
    cards = [Card.from_json(o) for o in (obj if isinstance(obj, list) else [obj])]
    tpl = cfg.path_of("render.template_path")
    if not tpl.exists():
        build_template(cfg.path_of("render.template_source"), tpl)
    if append_to:
        if not under:
            raise typer.BadParameter("--under is required with --append-to")
        path = append_under_heading(cards, append_to, under, tpl, out if out != Path("out/card.docx") else None)
    else:
        path = write_new(cards, tpl, out)
    typer.echo(f"wrote {len(cards)} card(s) -> {path}")


def _parse_overrides(stage: Optional[list[str]]) -> dict[str, str]:
    out = {}
    for item in stage or []:
        if "=" not in item:
            raise typer.BadParameter(f"--stage takes stage=backend, got {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@app.command("backends")
def backends_cmd(
    preset: Optional[str] = typer.Option(None, help="Preset to show (default: llm.preset)."),
    stage: Optional[list[str]] = typer.Option(None, help="Override a stage, e.g. --stage highlight=anthropic."),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Show which backend and model each stage uses, and whether it is reachable. Makes no model calls."""
    from cardcutter.llm.base import BackendUnavailable
    from cardcutter.llm.registry import Registry

    cfg = load_config(config)
    reg = Registry(cfg, preset, _parse_overrides(stage))
    typer.echo(f"preset: {reg.preset}")
    for s, desc in reg.describe().items():
        b = reg.for_stage(s)
        try:
            b.check()
            status = "ok"
        except BackendUnavailable as e:
            status = f"UNAVAILABLE: {e}"
        typer.echo(f"  {s:10} {desc:55} {status}")


@app.command("eval")
def eval_cmd(
    limit: Optional[int] = typer.Option(None, help="Only the first N holdout cards (quick checks)."),
    preset: Optional[str] = typer.Option(None, help="Backend preset (default: llm.preset)."),
    stage: Optional[list[str]] = typer.Option(None, help="Override a stage, e.g. --stage highlight=anthropic."),
    density: Optional[str] = typer.Option(None, help="tight | normal | long"),
    cap: Optional[float] = typer.Option(None, help="Session spending cap in USD (overrides config)."),
    yes: bool = typer.Option(False, "--yes", help="Don't ask before paid API calls or fallbacks."),
    rebuild_holdout: bool = typer.Option(False, help="Re-select the holdout cards."),
    compare_only: bool = typer.Option(False, "--compare", help="Only print the comparison of saved runs."),
    no_retrieval: bool = typer.Option(False, "--no-retrieval", help="Zero-shot: no few-shot examples."),
    no_tag: bool = typer.Option(False, "--no-tag", help="Skip the tag and read-back stages."),
    split: Optional[str] = typer.Option(None, help="test = holdout cards 1-30, dev = 31-60 (tune on dev), rest = 61-100."),
    offset: int = typer.Option(0, help="Skip the first N holdout cards."),
    label: str = typer.Option("", help="Name for this run (shown in the report)."),
    set_: Optional[list[str]] = typer.Option(None, "--set", help="Config override for this run, e.g. --set retrieval.k=6"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Cut held-out corpus cards and score them against the human cuts."""
    from cardcutter.corpus.build import load_cards
    from cardcutter.eval.holdout import load_holdout, save_holdout, select_holdout
    from cardcutter.eval.run import CUT_STAGES, compare_reports, run_eval, write_report
    from cardcutter.llm.registry import Registry

    cfg = load_config(config)
    if compare_only:
        typer.echo(compare_reports(cfg))
        return
    overrides = _apply_sets(cfg, set_ or [])
    if density:
        cfg.data.setdefault("cut", {})["density"] = density
    if no_tag:
        cfg.data.setdefault("cut", {})["tag"] = False
        cfg.data.setdefault("readback", {})["enabled"] = False
    cards = load_cards(cfg)
    hpath = cfg.path_of("eval.holdout_path")
    if rebuild_holdout or not hpath.exists():
        entries = select_holdout(cards, int(cfg.get("eval.holdout_size", 100)))
        save_holdout(entries, hpath)
        typer.echo(f"holdout: {len(entries)} cards -> {hpath}")
    ids = load_holdout(hpath)
    by_id = {c.meta["id"]: c for c in cards}
    splits = {"test": (0, 30), "dev": (30, 30), "rest": (60, None)}
    if split:
        if split not in splits:
            raise typer.BadParameter(f"--split must be one of {sorted(splits)}")
        offset, limit = splits[split][0], limit or splits[split][1]
    ordered = [by_id[i] for i in ids if i in by_id]
    chosen = ordered[offset: offset + limit if limit else None]
    use_retrieval = bool(cfg.get("retrieval.enabled", True)) and not no_retrieval

    reg = Registry(cfg, preset, _parse_overrides(stage))
    if cap is not None:
        reg.costs.session_cap_usd = cap
    stages_used = CUT_STAGES + (() if no_tag else ("tag", "readback")) + (("embed",) if use_retrieval else ())
    for note in reg.ensure_available(stages_used, confirm=lambda q: yes or typer.confirm(q, default=False)):
        typer.echo(note)
    retriever = None
    if use_retrieval:
        from cardcutter.cutter.retrieve import Retriever

        retriever = Retriever(cfg, reg, cards, exclude=set(ids), progress=typer.echo)
    typer.echo("stages: " + ", ".join(f"{k} → {v}" for k, v in reg.describe(CUT_STAGES).items()))

    paid = {reg.backend_name(s) for s in CUT_STAGES if not reg.for_stage(s).is_free}
    if paid:
        est = 0.0
        for c in chosen:
            words = len(c.body.text.split())
            for s in CUT_STAGES:
                b = reg.for_stage(s)
                if not b.is_free:
                    est += reg.estimate_card_cost(b, words) / len(CUT_STAGES)
        cap_txt = f"; session cap ${reg.costs.session_cap_usd:.2f}" if reg.costs.session_cap_usd else ""
        typer.echo(f"PAID API ({', '.join(sorted(paid))}): about ${est / max(1, len(chosen)):.4f} per card, "
                   f"${est:.2f} for {len(chosen)} cards{cap_txt}.")
        if not yes and not typer.confirm("Run it?", default=False):
            raise typer.Exit(1)

    run = run_eval(cfg, reg, chosen, progress=typer.echo, retriever=retriever, corpus=cards)
    run.config.update(label=label, split=split or f"offset {offset}, limit {limit}", overrides=overrides)
    path = write_report(cfg, run)
    s = run.summary()
    typer.echo(f"\nreport -> {path}")
    typer.echo(f"integrity {s['integrity_pass']}/{s['cards']}, underline F1 {100 * (s['underline_f1'] or 0):.1f}, "
               f"highlight F1 {100 * (s['highlight_f1'] or 0):.1f}, emphasis F1 {100 * (s['emphasis_f1'] or 0):.1f}, "
               f"read-back {s.get('readback_mean') or 0:.2f}/5, "
               f"{s['seconds_per_card'] or 0:.0f}s/card, actual cost ${reg.costs.spent_usd:.4f}")


def _apply_sets(cfg, sets: list[str]) -> dict[str, object]:
    """--set a.b.c=value (value parsed as TOML: 6, 0.5, true, "text" or bare text)."""
    import tomllib

    out: dict[str, object] = {}
    for item in sets:
        if "=" not in item:
            raise typer.BadParameter(f"--set needs key=value, got {item!r}")
        key, raw = item.split("=", 1)
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw
        node = cfg.data
        parts = key.strip().split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        out[key.strip()] = value
    return out


CUT_PIPELINE = ("locate", "underline", "highlight", "trim", "emphasis", "tag", "readback", "intent", "quals")


def _pipeline(reg) -> tuple[str, ...]:
    """The cut stages this preset runs (a preset may switch optional ones off)."""
    return tuple(s for s in CUT_PIPELINE if reg.has_stage(s))


@app.command("cut")
def cut_cmd(
    claim: str = typer.Option(..., "--claim", help="What the card must prove."),
    source: Optional[str] = typer.Option(None, "--source", help="Article: URL, .pdf, or text file."),
    text: Optional[str] = typer.Option(None, "--text", help="Pasted article text ('-' reads stdin)."),
    context: Optional[Path] = typer.Option(None, "--context", help="Case/file .docx the card should fit into."),
    role: Optional[str] = typer.Option(None, help="The card's job, e.g. 'internal link: legal certainty -> investment'."),
    out: Path = typer.Option(Path("out/card.docx"), "--out", help="Output .docx (new file)."),
    append_to: Optional[Path] = typer.Option(None, help="Append the card into this .docx instead."),
    under: Optional[str] = typer.Option(None, help="Heading to append under (with --append-to)."),
    candidates: int = typer.Option(1, help="Cut the top N locate candidates (1-3)."),
    cite_format: Optional[str] = typer.Option(None, help="inline | bracketed (default: cite.default_format)."),
    author: Optional[str] = typer.Option(None, help="Override author(s), ';'-separated."),
    quals: Optional[str] = typer.Option(None, help="Override qualifications, ';'-separated (one per author)."),
    date: Optional[str] = typer.Option(None, help="Override date (e.g. 2026-01-07)."),
    title: Optional[str] = typer.Option(None, help="Override title."),
    publication: Optional[str] = typer.Option(None, help="Override publication."),
    url: Optional[str] = typer.Option(None, help="Override URL."),
    html: Optional[Path] = typer.Option(None, help="Also write an HTML preview here."),
    preset: Optional[str] = typer.Option(None, help="Backend preset (default: llm.preset)."),
    stage: Optional[list[str]] = typer.Option(None, help="Override a stage, e.g. --stage tag=anthropic."),
    no_retrieval: bool = typer.Option(False, "--no-retrieval", help="No few-shot examples."),
    yes: bool = typer.Option(False, "--yes", help="Don't ask before paid API calls or fallbacks."),
    set_: Optional[list[str]] = typer.Option(None, "--set", help="Config override for this run, e.g. --set cut.trim_highlight=true"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Cut a finished card from an article: locate, underline, highlight, emphasis, tag, cite, verify."""
    import sys

    from cardcutter.corpus.build import load_cards
    from cardcutter.cutter.article import cut_document, meta_from_options
    from cardcutter.cutter.context import context_block, outline_text
    from cardcutter.ingest.source import from_text, load_source
    from cardcutter.llm.registry import Registry
    from cardcutter.verify.integrity import IntegrityError

    if bool(source) == bool(text):
        raise typer.BadParameter("give exactly one of --source or --text")
    cfg = load_config(config)
    _apply_sets(cfg, set_ or [])
    doc = from_text(sys.stdin.read() if text == "-" else text) if text else load_source(source)
    typer.echo(f"source: {doc.kind}, {len(doc.paragraphs)} paragraphs, {doc.words} words")

    reg = Registry(cfg, preset, _parse_overrides(stage))
    use_retrieval = bool(cfg.get("retrieval.enabled", True)) and not no_retrieval and reg.has_stage("embed")
    long_doc = doc.words > int(cfg.get("cut.max_window_words", 650))
    used = _pipeline(reg) + (("embed",) if reg.has_stage("embed") and (use_retrieval or long_doc) else ())
    for note in reg.ensure_available(used, confirm=lambda q: yes or typer.confirm(q, default=False)):
        typer.echo(note)
    paid = sorted({reg.backend_name(s) for s in _pipeline(reg) if not reg.for_stage(s).is_free})
    if paid:
        est = sum(reg.estimate_card_cost(reg.for_stage(s), min(doc.words, 650)) / 3 for s in _pipeline(reg)
                  if not reg.for_stage(s).is_free) * max(1, candidates)
        typer.echo(f"PAID API ({', '.join(paid)}): about ${est:.3f} for this card.")
        if not yes and not typer.confirm("Run it?", default=False):
            raise typer.Exit(1)

    ctx = context_block(outline_text(context) if context else "", role or "")
    retriever = None
    if use_retrieval:
        from cardcutter.cutter.retrieve import Retriever

        retriever = Retriever(cfg, reg, load_cards(cfg), progress=typer.echo)

    overrides = meta_from_options(authors=author, quals=quals, date=date, title=title, publication=publication, url=url)
    try:
        drafts = cut_document(reg, cfg, doc, claim, context=ctx, candidates=max(1, min(3, candidates)),
                              retriever=retriever, cite_overrides=overrides, cite_format=cite_format,
                              progress=lambda m: typer.echo(f"  … {m}"))
    except IntegrityError as e:
        typer.secho(f"\nINTEGRITY CHECK FAILED — nothing exported.\n{e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)

    _print_drafts(drafts)
    _write_cards(cfg, [d.card for d in drafts], out, append_to, under, html)
    typer.echo(f"total cost ${reg.costs.spent_usd:.4f}")


def _print_drafts(drafts, headers: list[str] | None = None) -> None:
    for i, d in enumerate(drafts, 1):
        rb = d.result.readback
        head = headers[i - 1] if headers else (f"paragraphs {d.candidate.start_para}-{d.candidate.end_para} "
                                               f"({d.candidate.words} words) — {d.candidate.rationale}")
        typer.echo(f"\n=== card {i}: {head}")
        typer.echo(f"TAG:  {d.card.tag.text}")
        typer.echo(f"CITE: {d.card.cite.text}")
        typer.echo(f"READ: {d.card.body.read_aloud()}")
        if rb:
            typer.echo(f"read-back: grammatical {rb['grammatical']}, coherent {rb['coherent']}, proves tag {rb['proves_tag']}")
        for w in d.warnings + d.notes:
            typer.secho(f"  ! {w}", fg=typer.colors.YELLOW)
        typer.echo(f"time {d.result.seconds:.0f}s, cost ${d.result.cost_usd:.4f}")


def _write_cards(cfg, cards: list[Card], out: Path, append_to: Optional[Path], under: Optional[str],
                 html: Optional[Path]) -> None:
    from cardcutter.render.docx import append_under_heading, write_new
    from cardcutter.render.html import page_html
    from cardcutter.render.template import build_template

    tpl = cfg.path_of("render.template_path")
    if not tpl.exists():
        build_template(cfg.path_of("render.template_source"), tpl)
    if append_to:
        if not under:
            raise typer.BadParameter("--under is required with --append-to")
        path = append_under_heading(cards, append_to, under, tpl)
    else:
        path = write_new(cards, tpl, out)
    typer.echo(f"\nwrote {len(cards)} card(s) -> {path}")
    if html:
        html.parent.mkdir(parents=True, exist_ok=True)
        html.write_text(page_html(cards, "CardCutter"), encoding="utf-8")
        typer.echo(f"preview -> {html}")


@app.command("auto")
def auto_cmd(
    claim: str = typer.Option(..., "--claim", help="What the cards must prove."),
    cards: int = typer.Option(0, help="How many cards, each from a different site (default: search.cards)."),
    context: Optional[Path] = typer.Option(None, "--context", help="Case/file .docx the cards should fit into."),
    role: Optional[str] = typer.Option(None, help="The card's job in the case."),
    out: Path = typer.Option(Path("out/auto.docx"), "--out", help="Output .docx (new file)."),
    append_to: Optional[Path] = typer.Option(None, help="Append the cards into this .docx instead."),
    under: Optional[str] = typer.Option(None, help="Heading to append under (with --append-to)."),
    html: Optional[Path] = typer.Option(None, help="Also write an HTML preview here."),
    sources_only: bool = typer.Option(False, "--sources-only", help="Just list the ranked sources; cut nothing."),
    cite_format: Optional[str] = typer.Option(None, help="inline | bracketed"),
    preset: Optional[str] = typer.Option(None, help="Backend preset (default: llm.preset)."),
    stage: Optional[list[str]] = typer.Option(None, help="Override a stage, e.g. --stage tag=anthropic."),
    no_retrieval: bool = typer.Option(False, "--no-retrieval", help="No few-shot examples."),
    yes: bool = typer.Option(False, "--yes", help="Don't ask before paid API calls or fallbacks."),
    set_: Optional[list[str]] = typer.Option(None, "--set", help="Config override for this run, e.g. --set cut.trim_highlight=true"),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Find sources for a claim on the web and in scholarly databases, then cut the best cards."""
    from cardcutter.cutter.context import context_block, outline_text
    from cardcutter.llm.registry import Registry
    from cardcutter.search.auto import auto_cut, find_sources, more_sources
    from cardcutter.corpus.build import load_cards

    cfg = load_config(config)
    _apply_sets(cfg, set_ or [])
    n = cards or int((cfg.get("search") or {}).get("cards", 3))
    reg = Registry(cfg, preset, _parse_overrides(stage))
    use_retrieval = bool(cfg.get("retrieval.enabled", True)) and not no_retrieval and reg.has_stage("embed")
    used = ("queries", "screen") + (() if sources_only else _pipeline(reg)) + (("embed",) if reg.has_stage("embed") else ())
    for note in reg.ensure_available(used, confirm=lambda q: yes or typer.confirm(q, default=False)):
        typer.echo(note)
    paid = sorted({reg.backend_name(s) for s in used if s != "embed" and not reg.for_stage(s).is_free})
    if paid:
        est = sum(reg.estimate_card_cost(reg.for_stage(s), 500) / 3 for s in _pipeline(reg)
                  if not reg.for_stage(s).is_free) * n
        typer.echo(f"PAID API ({', '.join(paid)}): about ${est:.3f} for {n} card(s).")
        if not yes and not typer.confirm("Run it?", default=False):
            raise typer.Exit(1)
    ctx = context_block(outline_text(context) if context else "", role or "")
    say = lambda m: typer.echo(f"  … {m}")  # noqa: E731
    try:
        found = find_sources(reg, cfg, claim, context=ctx, want=n + 2, say=say)
    except ValueError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    typer.echo(f"\nqueries: {' | '.join(found.queries)}")
    typer.echo(f"{len(found.sources)} readable sources of {found.hits} results:")
    for k, src in enumerate(found.sources[:12], 1):
        m = src.doc.meta
        typer.echo(f"{k:2}. {src.score:.2f}  {(m.title or src.hit.title)[:70]}  — {m.publication or src.summary()['domain']}"
                   f"{', ' + m.date[:10] if m.date else ''}  [{src.hit.provider}]")
    if sources_only:
        return
    retriever = None
    if use_retrieval:
        from cardcutter.cutter.retrieve import Retriever

        retriever = Retriever(cfg, reg, load_cards(cfg), progress=say)
    done, skipped = auto_cut(reg, cfg, claim, found.sources, n_cards=n, context=ctx, retriever=retriever,
                             cite_format=cite_format, say=say,
                             search_more=more_sources(reg, cfg, claim, found, context=ctx, want=n + 2, say=say))
    for s_ in skipped:
        typer.secho(f"  skipped {s_}", fg=typer.colors.YELLOW)
    if not done:
        typer.secho("no card could be cut from the sources found; nothing exported", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    _print_drafts([d for _, d in done], [f"{src.summary()['domain']} — {src.doc.origin}" for src, _ in done])
    _write_cards(cfg, [d.card for _, d in done], out, append_to, under, html)
    typer.echo(f"total cost ${reg.costs.spent_usd:.4f}")


@app.command("suggest")
def suggest_cmd(
    context: Path = typer.Option(..., "--context", help="Case/file .docx."),
    preset: Optional[str] = typer.Option(None),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """List the links (uniqueness, internal link, impact, solvency) the case still needs."""
    from cardcutter.cutter.context import outline_text, suggest_gaps
    from cardcutter.llm.registry import Registry

    cfg = load_config(config)
    reg = Registry(cfg, preset)
    reg.ensure_available(("tag",), confirm=lambda q: typer.confirm(q, default=False))
    for g in suggest_gaps(reg, outline_text(context, max_tokens=4000)):
        typer.echo(f"- [{g['kind']}] under “{g['where']}”: {g['claim']}")


@app.command("ui")
def ui_cmd(
    port: int = typer.Option(8765, help="Port for the local web UI."),
    host: str = typer.Option("127.0.0.1", help="Interface to bind. Keep the default: the UI has no login."),
    config: Optional[Path] = ConfigOpt,
) -> None:
    """Open the local web UI (paste/PDF/URL, three candidates, hand edits, export, tag A/B)."""
    import uvicorn

    from cardcutter.app.web import create_app

    if host not in ("127.0.0.1", "localhost", "::1"):
        typer.secho(f"warning: binding {host} exposes the UI (and your API keys' spending) to your network",
                    fg=typer.colors.YELLOW)
    cfg = load_config(config)
    typer.echo(f"CardCutter UI: http://{'127.0.0.1' if host in ('127.0.0.1', 'localhost') else host}:{port}")
    uvicorn.run(create_app(cfg), host=host, port=port, log_level="warning")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
