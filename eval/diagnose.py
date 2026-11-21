"""POST-HOC DIAGNOSTIC (not a score): re-run the system arm's two calls on the v1 system-arm failures and record
which abstention rule fired. Same prompts/schema/call path as app.models.pair. Quotes stay in private/;
eval/results/diagnostic-v1.json carries only the per-case rule that fired."""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "eval"))
import app  # noqa: F401,E402
from bench import read  # noqa: E402
from app.models import _side  # noqa: E402
from app.models.io import structured, text_part  # noqa: E402
from deal_models import EXTRACT, PAIR, Pair  # noqa: E402
from gemini_io import CARBON, NEON  # noqa: E402


def one(cid, packet):
    pk = {"left": _side(packet["left"]), "right": _side(packet["right"])}
    data = text_part(pk)
    hints, _ = structured("pair_extract", NEON, EXTRACT, Pair, data)
    final, _ = structured("pair_verdict", CARBON, PAIR, Pair, data + text_part({"inspection": hints.model_dump()}))
    quote_ok = {}
    for name, ins in (("neon", hints), ("carbon", final)):
        for side in ("left", "right"):
            q = getattr(ins, side + "_quote").strip().casefold()
            quote_ok[f"{name}_{side}"] = bool(q) and any(q in str(v).casefold() for v in pk[side].values())
    rules = []
    if hints.verdict != final.verdict:
        rules.append("models_disagree")
    if final.verdict == "abstain":
        rules.append("carbon_abstained")
    if not all(quote_ok.values()):
        rules.append("quote_not_verbatim")
    priv = {"id": cid, "neon": hints.model_dump(), "carbon": final.model_dump(), "quote_ok": quote_ok, "rules": rules}
    return {"id": cid, "neon_verdict": hints.verdict, "carbon_verdict": final.verdict, "quote_ok": quote_ok,
            "rules_fired": rules}, priv


def main():
    doc = json.loads((ROOT / "eval/results/pairs-v1.json").read_text())
    fails = [c["id"] for c in doc["cases"] if c["arm"] == "full" and not c["correct"]]
    packets = {r["id"]: r["packet"] for r in read(ROOT / "private/wdc/bench/in/pairs.jsonl")}
    with ThreadPoolExecutor(max_workers=1) as pool:  # each job holds <=1 call in flight; 2 jobs => <=2 calls
        pass
    with ThreadPoolExecutor(max_workers=2) as pool:
        out = list(pool.map(lambda i: one(i, packets[i]), fails))
    (ROOT / "private/wdc/bench/diagnostic-v1-private.json").write_text(json.dumps([p for _, p in out], indent=1, ensure_ascii=False))
    res = {"kind": "post-hoc diagnostic, not a score", "of": "pairs-v1.json system-arm failures", "ran_at": int(time.time()),
           "note": "independent re-run of the same two calls; model outputs can differ from the scored v1 run",
           "cases": [r for r, _ in out]}
    with (ROOT / "eval/results/diagnostic-v1.json").open("x") as f:
        json.dump(res, f, indent=1)
    for r in res["cases"]:
        print(r["id"], r["neon_verdict"], r["carbon_verdict"], r["rules_fired"], [k for k, v in r["quote_ok"].items() if not v])


if __name__ == "__main__":
    main()
