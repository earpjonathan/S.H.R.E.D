"""Local web UI: load an article (paste, PDF, URL), cut up to three candidate cards, edit
the underlining/highlighting/emphasis by hand, fix the tag and cite, export to Verbatim.

Runs on 127.0.0.1 only. Model calls run one at a time in a worker thread; the page polls
a job for per-stage progress. Every export re-verifies each card body against the loaded
source, so a hand edit can change formatting but never the words.
"""
from __future__ import annotations

import copy
import json
import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from cardcutter.card import Card
from cardcutter.cite.format import build_cite
from cardcutter.cite.meta import CiteMeta
from cardcutter.config import Config
from cardcutter.ingest.source import Document, from_pdf, from_text, from_url
from cardcutter.llm.base import BackendUnavailable
from cardcutter.llm.cost import CostTracker
from cardcutter.llm.registry import Registry
from cardcutter.verify.integrity import IntegrityError, IntegrityPolicy, verify_body

STATIC = Path(__file__).parent / "static"
CUT_PIPELINE = ("locate", "underline", "highlight", "trim", "emphasis", "tag", "readback", "intent", "quals")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"          # queued | running | done | error
    log: list[str] = field(default_factory=list)
    result: Any = None
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def view(self) -> dict[str, Any]:
        end = self.finished or time.time()
        return {"id": self.id, "kind": self.kind, "status": self.status, "log": self.log[-200:],
                "result": self.result, "error": self.error, "seconds": round(end - self.started, 1)}


class CutRequest(BaseModel):
    doc_id: str
    claim: str
    role: str = ""
    context_id: Optional[str] = None
    candidates: int = 1
    density: Optional[str] = None
    cite_format: Optional[str] = None
    preset: Optional[str] = None
    retrieval: bool = True
    overrides: dict[str, str] = {}
    allow_fallback: bool = False
    allow_paid: bool = False


class AutoRequest(BaseModel):
    claim: str
    role: str = ""
    context_id: Optional[str] = None
    cards: int = 3
    density: Optional[str] = None
    cite_format: Optional[str] = None
    preset: Optional[str] = None
    retrieval: bool = True
    sources_only: bool = False
    allow_fallback: bool = False
    allow_paid: bool = False


class SuggestRequest(BaseModel):
    context_id: str
    preset: Optional[str] = None
    allow_fallback: bool = False
    allow_paid: bool = False


class CiteRequest(BaseModel):
    meta: dict[str, Any]
    format: Optional[str] = None


class ExportRequest(BaseModel):
    doc_id: str = ""
    cards: list[dict[str, Any]]
    mode: str = "new"               # new | append
    filename: str = "cards.docx"
    append_path: str = ""
    heading: str = ""


class VoteRequest(BaseModel):
    key: str
    choice: str                     # A | B | tie


class State:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.docs: dict[str, Document] = {}
        self.source_info: dict[str, dict[str, Any]] = {}
        self.contexts: dict[str, dict[str, str]] = {}
        self.jobs: dict[str, Job] = {}
        self.pool = ThreadPoolExecutor(max_workers=1)   # one model job at a time
        self.costs = CostTracker.from_config(cfg)       # session spending cap spans all jobs
        self.retrievers: dict[str, Any] = {}
        self.lock = threading.Lock()
        self.ab_pending: dict[str, dict[str, Any]] = {}
        self._cards: list[Card] | None = None

    # -- helpers ------------------------------------------------------------------
    def cards(self) -> list[Card]:
        if self._cards is None:
            from cardcutter.corpus.build import load_cards

            self._cards = load_cards(self.cfg)
        return self._cards

    def cfg_for(self, density: str | None) -> Config:
        if not density or density == self.cfg.get("cut.density"):
            return self.cfg
        if density not in (self.cfg.get("cut.densities") or {}):
            raise HTTPException(400, f"unknown density {density!r}")
        data = copy.deepcopy(self.cfg.data)
        data["cut"]["density"] = density
        return Config(path=self.cfg.path, data=data)

    def registry(self, cfg: Config, preset: str | None, stages: tuple[str, ...], allow_fallback: bool,
                 allow_paid: bool, words: int) -> Registry | JSONResponse:
        """A registry ready to run `stages`, or a 409/402 asking the user to confirm a switch."""
        try:
            reg = Registry(cfg, preset)
        except KeyError as e:
            raise HTTPException(400, str(e))
        reg.costs = self.costs
        asked: list[str] = []
        try:
            reg.ensure_available(stages, confirm=lambda q: asked.append(q) or allow_fallback)
        except BackendUnavailable as e:
            if asked and not allow_fallback:
                return JSONResponse({"confirm": "fallback", "question": asked[0]}, status_code=409)
            raise HTTPException(503, str(e))
        paid = sorted({reg.backend_name(s) for s in stages if s != "embed" and not reg.for_stage(s).is_free})
        if paid and not allow_paid:
            est = sum(reg.estimate_card_cost(reg.for_stage(s), min(words, 650)) / 3
                      for s in stages if s != "embed" and not reg.for_stage(s).is_free)
            return JSONResponse({"confirm": "paid", "question": f"This uses a PAID API ({', '.join(paid)}): "
                                 f"about ${est:.3f} per card. Run it?"}, status_code=402)
        return reg

    def submit(self, kind: str, work: Callable[[Callable[[str], None], Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self.jobs[job.id] = job

        def run() -> None:
            job.status = "running"
            say = job.log.append
            try:
                job.result = work(say, job)
                job.status = "done"
            except IntegrityError as e:
                job.status, job.error = "error", "Integrity check failed; nothing was produced.\n" + "\n".join(e.problems)
            except Exception as e:  # shown to the user as-is
                job.status, job.error = "error", f"{type(e).__name__}: {e}"
            finally:
                job.finished = time.time()

        self.pool.submit(run)
        return job

    def retriever(self, cfg: Config, reg: Registry, say: Callable[[str], None]):
        from cardcutter.cutter.retrieve import Retriever

        name = reg.backend_name("embed")
        with self.lock:
            if name not in self.retrievers:
                self.retrievers[name] = Retriever(cfg, reg, self.cards(), progress=say)
            return self.retrievers[name]


def draft_json(d, doc_id: str, source: dict[str, Any] | None = None) -> dict[str, Any]:
    card = d.card.to_json()
    card["meta"]["doc_id"] = doc_id
    return {
        "card": card,
        "source": source,
        "candidate": asdict(d.candidate),
        "warnings": d.warnings,
        "notes": d.notes,
        "cite_missing": d.cite_missing,
        "readback": d.result.readback,
        "readback_history": d.result.readback_history,
        "examples": d.result.example_ids,
        "seconds": round(d.result.seconds, 1),
        "cost_usd": round(d.result.cost_usd, 5),
        "stages": d.result.log_dicts(),
    }


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="CardCutter", docs_url=None, redoc_url=None)
    st = State(cfg)
    app.state.cc = st

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/api/info")
    def info() -> dict[str, Any]:
        reg = Registry(cfg)
        return {
            "preset": reg.preset,
            "presets": sorted(cfg.get("llm.presets") or {}),
            "stages": reg.describe(CUT_PIPELINE + ("embed",)),
            "density": cfg.get("cut.density", "normal"),
            "densities": sorted(cfg.get("cut.densities") or {}),
            "cite_format": cfg.get("cite.default_format", "inline"),
            "highlight_color": cfg.get("render.highlight_color", "yellow"),
            "shrink_half_points": int((cfg.get("render.shrink_unread_pt") or 0) * 2),
            "retrieval": bool(cfg.get("retrieval.enabled", True)),
            "spent_usd": round(st.costs.spent_usd, 4),
        }

    # -- inputs -------------------------------------------------------------------
    @app.post("/api/source")
    async def load(text: str = Form(""), url: str = Form(""), file: UploadFile | None = File(None)) -> dict[str, Any]:
        try:
            if file is not None and file.filename:
                data = await file.read()
                if file.filename.lower().endswith(".pdf"):
                    up = cfg.root / "data" / "uploads"
                    up.mkdir(parents=True, exist_ok=True)
                    path = up / f"{uuid.uuid4().hex[:8]}-{SAFE_NAME.sub('_', file.filename)}"
                    path.write_bytes(data)
                    doc = from_pdf(path)
                    doc.origin = file.filename
                else:
                    doc = from_text(data.decode("utf-8", errors="replace"), origin=file.filename)
            elif url.strip():
                doc = from_url(url.strip())
            elif text.strip():
                doc = from_text(text)
            else:
                raise HTTPException(400, "paste text, choose a file, or enter a URL")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(422, f"could not read the source: {type(e).__name__}: {e}")
        if not doc.paragraphs:
            raise HTTPException(422, "no text found in the source")
        doc_id = uuid.uuid4().hex[:12]
        st.docs[doc_id] = doc
        return {"doc_id": doc_id, "kind": doc.kind, "origin": doc.origin, "paragraphs": doc.paragraphs,
                "words": doc.words, "meta": doc.meta.to_json()}

    @app.post("/api/context")
    async def context(file: UploadFile = File(...)) -> dict[str, Any]:
        from cardcutter.cutter.context import outline_text

        if not (file.filename or "").lower().endswith(".docx"):
            raise HTTPException(400, "the case file must be a .docx")
        up = cfg.root / "data" / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        path = up / f"{uuid.uuid4().hex[:8]}-{SAFE_NAME.sub('_', file.filename)}"
        path.write_bytes(await file.read())
        outline = outline_text(path)
        cid = uuid.uuid4().hex[:12]
        st.contexts[cid] = {"name": file.filename, "outline": outline, "path": str(path)}
        return {"context_id": cid, "name": file.filename, "outline": outline}

    # -- model jobs ---------------------------------------------------------------
    @app.post("/api/cut")
    def cut(req: CutRequest):
        from cardcutter.cutter.article import cut_document, meta_from_options
        from cardcutter.cutter.context import context_block

        doc = st.docs.get(req.doc_id)
        if doc is None:
            raise HTTPException(404, "source not loaded (the server may have restarted); load it again")
        if not req.claim.strip():
            raise HTTPException(400, "enter the claim the card should prove")
        c = st.cfg_for(req.density)
        probe = Registry(c, req.preset) if req.preset in (None, "") or req.preset in (c.get("llm.presets") or {}) else None
        if probe is None:
            raise HTTPException(400, f"unknown preset {req.preset!r}")
        use_retrieval = req.retrieval and bool(c.get("retrieval.enabled", True)) and probe.has_stage("embed")
        long_doc = doc.words > int(c.get("cut.max_window_words", 650))
        stages = tuple(x for x in CUT_PIPELINE if probe.has_stage(x)) + (("embed",) if probe.has_stage("embed") and (use_retrieval or long_doc) else ())
        reg = st.registry(c, req.preset, stages, req.allow_fallback, req.allow_paid, doc.words)
        if isinstance(reg, JSONResponse):
            return reg
        outline = st.contexts.get(req.context_id or "", {}).get("outline", "")
        ctx = context_block(outline, req.role.strip())
        overrides = meta_from_options(**{k: v for k, v in req.overrides.items() if k in CiteMeta.__dataclass_fields__})

        def work(say, job):
            retriever = st.retriever(c, reg, say) if use_retrieval else None
            drafts = cut_document(reg, c, doc, req.claim.strip(), context=ctx,
                                  candidates=max(1, min(3, req.candidates)), retriever=retriever,
                                  cite_overrides=overrides, cite_format=req.cite_format, progress=say)
            say("done")
            src = st.source_info.get(req.doc_id)
            return {"drafts": [draft_json(d, req.doc_id, src) for d in drafts], "spent_usd": round(st.costs.spent_usd, 4)}

        return st.submit("cut", work).view()

    @app.post("/api/auto")
    def auto(req: AutoRequest):
        from cardcutter.cutter.context import context_block
        from cardcutter.search.auto import auto_cut, find_sources, more_sources

        if not req.claim.strip():
            raise HTTPException(400, "enter the claim the cards should prove")
        c = st.cfg_for(req.density)
        if req.preset and req.preset not in (c.get("llm.presets") or {}):
            raise HTTPException(400, f"unknown preset {req.preset!r}")
        probe = Registry(c, req.preset)
        use_retrieval = req.retrieval and bool(c.get("retrieval.enabled", True)) and probe.has_stage("embed")
        stages = ("queries", "screen") + (() if req.sources_only else tuple(x for x in CUT_PIPELINE if probe.has_stage(x))) + (("embed",) if probe.has_stage("embed") else ())
        reg = st.registry(c, req.preset, stages, req.allow_fallback, req.allow_paid, 500)
        if isinstance(reg, JSONResponse):
            return reg
        outline = st.contexts.get(req.context_id or "", {}).get("outline", "")
        ctx = context_block(outline, req.role.strip())
        claim = req.claim.strip()

        def work(say, job):
            job.result = {"queries": [], "sources": [], "drafts": [], "skipped": [], "spent_usd": 0.0}
            found = find_sources(reg, c, claim, context=ctx, want=max(1, min(5, req.cards)) + 2, say=say)
            for src in found.sources:
                st.docs[src.id] = src.doc
                st.source_info[src.id] = src.summary()
            job.result.update(queries=list(found.queries), sources=[x.summary() for x in found.sources])
            if req.sources_only:
                say("done")
                return job.result
            retriever = st.retriever(c, reg, say) if use_retrieval else None

            def on_draft(src, d):
                job.result["drafts"].append(draft_json(d, src.id, st.source_info[src.id]))
                job.result["spent_usd"] = round(st.costs.spent_usd, 4)

            def on_new(more):
                for src in more.sources:
                    st.docs[src.id] = src.doc
                    st.source_info[src.id] = src.summary()
                job.result["queries"] += more.queries
                job.result["sources"] += [x.summary() for x in more.sources]

            n = max(1, min(5, req.cards))
            _, skipped = auto_cut(reg, c, claim, found.sources, n_cards=n, context=ctx,
                                  retriever=retriever, cite_format=req.cite_format, say=say, on_draft=on_draft,
                                  search_more=more_sources(reg, c, claim, found, context=ctx, want=n + 2, say=say,
                                                           on_new=on_new))
            job.result["skipped"] = skipped
            if not job.result["drafts"]:
                raise ValueError("no card could be cut from the sources found; try another claim or pick a source")
            say("done")
            return job.result

        return st.submit("auto", work).view()

    @app.post("/api/suggest")
    def suggest(req: SuggestRequest):
        from cardcutter.cutter.context import outline_text, suggest_gaps

        ctx = st.contexts.get(req.context_id)
        if ctx is None:
            raise HTTPException(404, "case file not loaded")
        reg = st.registry(cfg, req.preset, ("tag",), req.allow_fallback, req.allow_paid, 400)
        if isinstance(reg, JSONResponse):
            return reg

        def work(say, job):
            say("reading the case outline")
            return {"gaps": suggest_gaps(reg, outline_text(Path(ctx["path"]), max_tokens=4000))}

        return st.submit("suggest", work).view()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        j = st.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "no such job")
        return j.view()

    # -- editing and export -------------------------------------------------------
    @app.post("/api/cite")
    def cite(req: CiteRequest) -> dict[str, Any]:
        fields = {k: v for k, v in req.meta.items() if k in CiteMeta.__dataclass_fields__}
        for k in ("authors", "quals", "sources"):
            if isinstance(fields.get(k), str):
                fields[k] = [x.strip() for x in fields[k].split(";") if x.strip()]
        meta = CiteMeta(**fields)
        res = build_cite(meta, req.format or cfg.get("cite.default_format", "inline"),
                         cutter=cfg.get("cite.cutter_name", ""), marker=cfg.get("cite.missing_marker", "[{field}?]"))
        return {"cite": res.cite.to_json(), "missing": res.missing, "meta": meta.to_json()}

    @app.post("/api/export")
    def export(req: ExportRequest) -> dict[str, Any]:
        from cardcutter.render.docx import append_under_heading, write_new
        from cardcutter.render.template import build_template

        if not req.cards:
            raise HTTPException(400, "no cards selected")
        cards: list[Card] = []
        policy = IntegrityPolicy.from_config(cfg)
        for k, obj in enumerate(req.cards, 1):
            try:
                card = Card.from_json(obj)
            except (KeyError, IndexError, TypeError, ValueError) as e:
                raise HTTPException(422, f"card {k} is malformed; nothing exported: {e}")
            off = card.meta.get("offset")
            if not isinstance(off, int):
                raise HTTPException(422, f"card {k} has no source offset")
            doc = st.docs.get(card.meta.get("doc_id") or req.doc_id)
            if doc is None:
                raise HTTPException(404, f"card {k}'s source is no longer loaded (the server may have restarted)")
            try:
                verify_body(card.body, doc.text, offset=off, policy=policy)
            except IntegrityError as e:
                raise HTTPException(422, f"card {k} failed the integrity check; nothing exported: " + "; ".join(e.problems))
            if not card.tag.text.strip():
                raise HTTPException(422, f"card {k} has no tag")
            cards.append(card)
        tpl = cfg.path_of("render.template_path")
        if not tpl.exists():
            build_template(cfg.path_of("render.template_source"), tpl)
        if req.mode == "append":
            target = Path(req.append_path).expanduser()
            if target.suffix.lower() != ".docx" or not target.exists():
                raise HTTPException(400, f"{target} is not an existing .docx file")
            if not req.heading.strip():
                raise HTTPException(400, "enter the heading to append under")
            try:
                path = append_under_heading(cards, target, req.heading.strip(), tpl)
            except (KeyError, ValueError) as e:
                raise HTTPException(422, str(e))
            return {"path": str(path), "download": None, "cards": len(cards)}
        name = SAFE_NAME.sub("_", Path(req.filename or "cards.docx").name) or "cards.docx"
        if not name.lower().endswith(".docx"):
            name += ".docx"
        out = cfg.root / "out" / name
        write_new(cards, tpl, out)
        return {"path": str(out), "download": f"/api/download/{name}", "cards": len(cards)}

    @app.get("/api/download/{name}")
    def download(name: str):
        path = cfg.root / "out" / SAFE_NAME.sub("_", Path(name).name)
        if not path.exists():
            raise HTTPException(404, "not found")
        return FileResponse(path, filename=path.name,
                            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    # -- blind tag A/B on eval runs ----------------------------------------------------
    votes_path = cfg.root / "data" / "ab_votes.jsonl"

    def votes(run: str) -> list[dict[str, Any]]:
        if not votes_path.exists():
            return []
        out = []
        for line in votes_path.read_text(encoding="utf-8").splitlines():
            v = json.loads(line)
            if v.get("run") == run:
                out.append(v)
        return out

    def tally(run: str) -> dict[str, int]:
        t = {"generated": 0, "human": 0, "tie": 0}
        for v in votes(run):
            t[v["winner"]] += 1
        return t

    def run_rows(run: str) -> list[dict[str, Any]]:
        path = cfg.path_of("eval.runs_dir") / SAFE_NAME.sub("_", run) / "rows.json"
        if not path.exists():
            return []
        return [r for r in json.loads(path.read_text(encoding="utf-8")) if r.get("tag_generated") and r.get("tag_human")]

    @app.get("/api/ab/runs")
    def ab_runs() -> list[dict[str, Any]]:
        root = cfg.path_of("eval.runs_dir")
        out = []
        for d in sorted(root.glob("*"), reverse=True) if root.exists() else []:
            n = len(run_rows(d.name))
            if n:
                out.append({"run": d.name, "cards": n, "tally": tally(d.name)})
        return out

    @app.get("/api/ab/next")
    def ab_next(run: str) -> dict[str, Any]:
        rows = run_rows(run)
        done = {v["id"] for v in votes(run)}
        todo = [r for r in rows if r["id"] not in done]
        if not todo:
            return {"done": True, "tally": tally(run), "total": len(rows)}
        row = random.choice(todo)
        gold = next((c for c in st.cards() if c.meta.get("id") == row["id"]), None)
        if gold is None:
            raise HTTPException(404, f"card {row['id']} is no longer in the corpus")
        order = ["generated", "human"]
        random.shuffle(order)
        key = uuid.uuid4().hex[:12]
        st.ab_pending[key] = {"run": run, "id": row["id"], "order": order}
        tags = {"generated": row["tag_generated"], "human": row["tag_human"]}
        return {"done": False, "key": key, "paragraphs": gold.body.text.split("\n"),
                "A": tags[order[0]], "B": tags[order[1]], "remaining": len(todo), "total": len(rows)}

    @app.post("/api/ab/vote")
    def ab_vote(req: VoteRequest) -> dict[str, Any]:
        p = st.ab_pending.pop(req.key, None)
        if p is None:
            raise HTTPException(404, "unknown or already-used vote key")
        if req.choice not in ("A", "B", "tie"):
            raise HTTPException(400, "choice must be A, B, or tie")
        winner = "tie" if req.choice == "tie" else p["order"][0 if req.choice == "A" else 1]
        votes_path.parent.mkdir(parents=True, exist_ok=True)
        with votes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"run": p["run"], "id": p["id"], "winner": winner, "at": time.time()}) + "\n")
        return {"winner": winner, "tally": tally(p["run"])}

    return app
