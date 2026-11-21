"""GET /api/proof/eval — the recorded evaluation, read from eval/results/ at request time (never cached, never invented)."""
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
RESULTS = Path(__file__).resolve().parents[2] / "eval" / "results"


def load(results: Path = RESULTS):
    results = Path(results)
    raw = sorted(results.glob("pairs-*.json"))
    summary_path = results / "summary.json"
    if not raw or not summary_path.exists():
        return None
    summary = json.loads(summary_path.read_text())
    cases = []
    for path in raw:
        doc = json.loads(path.read_text())
        for c in doc["cases"]:
            cases.append({"run": doc["name"], "version": doc["version"], "draw": doc.get("draw", "A"),
                          "code_sha": doc["code_sha"],
                          **{k: c.get(k) for k in ("id", "wdc_pair_id", "category", "arm", "verdict", "gold", "correct",
                                                   "abstained", "error", "ms")}})
    names = [p.name for p in raw] + ["summary.json"]
    extra = [p.name for p in (results / "freeze.json", results / "freeze-b.json", results / "baskets-gap.json",
                              results / "e2e-summary.json") if p.exists()]
    e2e = results / "e2e-summary.json"
    return {"generated_from": names + extra, "summary": summary, "cases": cases,
            "e2e": json.loads(e2e.read_text()) if e2e.exists() else None}


@router.get("/api/proof/eval")
def proof_eval():
    doc = load()
    if doc is None:
        return JSONResponse(status_code=404, content={"error": {"code": "no_eval", "message": "No evaluation run recorded yet"}})
    return doc
