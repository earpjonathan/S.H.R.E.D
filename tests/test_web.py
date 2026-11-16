"""The web UI's API, driven with the scripted fake backend (no model calls)."""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import cardcutter.app.web as web  # noqa: E402
from cardcutter.llm.registry import Registry  # noqa: E402
from tests.fakes import FakeBackend, make_config  # noqa: E402

ARTICLE = ("Jane Q. Doe is a professor of marine policy at Example University.\n\n"
           "Experts warn that disputes over marine protected areas (MPAs) will escalate. Without a treaty, "
           "marine protected areas collapse and biodiversity loss becomes irreversible.")


def respond(stage, prompt):
    return {
        "quals": {"authors": ["Jane Q. Doe"], "quals": ["professor of marine policy at Example University"],
                  "date": "", "publication": ""},
        "underline": {"plan": "p", "underline": [{"start": 12, "end": 40}]},
        "highlight": {"plan": "p", "highlight": [{"start": 12, "end": 17}], "partial": []},
        "emphasis": {"emphasis": [{"start": 14, "end": 14}]},
        "tag": {"plan": "p", "tag": "MPA disputes escalate", "underline": ["escalate"]},
        "readback": {"grammatical": 5, "coherent": 5, "proves_tag": 5, "power_tag": False, "feedback": "ok"},
    }[stage]


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = make_config(tmp_path, acronym_rule=False)
    examples = sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.docx"))
    cfg.data["render"]["template_source"] = str(examples[0]) if examples else "missing.docx"
    cfg.data["render"]["template_path"] = "template.docx"
    fake = FakeBackend("fake", respond)

    class FakeRegistry(Registry):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self._built["fake"] = fake

    monkeypatch.setattr(web, "Registry", FakeRegistry)
    return TestClient(web.create_app(cfg)), cfg, fake


def wait(c, job_id):
    for _ in range(200):
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_cut_edit_and_export_flow(client):
    c, cfg, fake = client
    assert c.get("/").status_code == 200
    info = c.get("/api/info").json()
    assert info["preset"] == "local" and info["shrink_half_points"] == 16

    doc = c.post("/api/source", data={"text": ARTICLE}).json()
    assert len(doc["paragraphs"]) == 2

    r = c.post("/api/cut", json={"doc_id": doc["doc_id"], "claim": "MPA disputes escalate", "retrieval": False,
                                 "overrides": {"date": "2026-01-07"}})
    assert r.status_code == 200, r.text
    job = wait(c, r.json()["id"])
    assert job["status"] == "done", job["error"]
    [draft] = job["result"]["drafts"]
    card = draft["card"]
    assert card["tag"]["text"] == "MPA disputes escalate"
    assert any("tag" in line or "underline" in line for line in job["log"])

    cite = c.post("/api/cite", json={"meta": {**card["meta"]["cite_meta"], "title": "Ocean Treaties",
                                              "publication": "Example Review", "url": "https://example.org"},
                                     "format": "inline"}).json()
    assert cite["missing"] == [] and '"Ocean Treaties," Example Review' in cite["cite"]["text"]

    # hand edit: highlight one more word, as the page would, then export
    from cardcutter.card import Card, FText

    body = FText.from_json(card["body"])
    k = body.text.index("treaty")
    body.set_span("highlight", k, k + 6, "yellow")
    edited = {**card, "body": body.to_json(), "cite": cite["cite"]}
    if (Path(cfg.data["render"]["template_source"])).exists():
        r = c.post("/api/export", json={"doc_id": doc["doc_id"], "cards": [edited], "filename": "t.docx"})
        assert r.status_code == 200, r.text
        assert r.json()["download"] == "/api/download/t.docx"
        got = c.get("/api/download/t.docx")
        assert got.status_code == 200 and zipfile.is_zipfile(__import__("io").BytesIO(got.content))
        assert "treaty" in Card.from_json(edited).body.read_aloud()

    # tampering with the words is refused; nothing is written
    import shutil
    shutil.rmtree(cfg.root / "out", ignore_errors=True)
    bad = {**card, "body": {**card["body"], "text": card["body"]["text"].replace("escalate", "escalade")}}
    r = c.post("/api/export", json={"doc_id": doc["doc_id"], "cards": [bad]})
    assert r.status_code == 422 and "integrity" in r.json()["detail"]
    short = {**card, "body": {**card["body"], "text": card["body"]["text"].replace("escalate", "explode")}}
    r = c.post("/api/export", json={"doc_id": doc["doc_id"], "cards": [short]})
    assert r.status_code == 422 and "malformed" in r.json()["detail"]
    assert not (cfg.root / "out").exists()


def test_paid_backend_needs_confirmation(client):
    c, cfg, fake = client
    doc = c.post("/api/source", data={"text": ARTICLE}).json()
    r = c.post("/api/cut", json={"doc_id": doc["doc_id"], "claim": "x", "preset": "api", "retrieval": False})
    assert r.status_code in (402, 503)  # 402 asks first; 503 if the paid backend has no key at all
    if r.status_code == 402:
        assert "PAID API" in r.json()["question"]
    assert not fake.calls


def test_local_down_offers_fallback_and_never_switches_silently(client):
    c, cfg, fake = client
    fake.available = False
    doc = c.post("/api/source", data={"text": ARTICLE}).json()
    r = c.post("/api/cut", json={"doc_id": doc["doc_id"], "claim": "x", "retrieval": False})
    assert r.status_code in (409, 503)
    assert not fake.calls
