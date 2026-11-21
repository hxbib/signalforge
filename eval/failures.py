"""Print the system-arm failures of one run with their packets (local reading only; packets stay in private/)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from bench import read  # noqa: E402

version = sys.argv[1] if len(sys.argv) > 1 else "v1"
doc = json.loads((ROOT / "eval" / "results" / f"pairs-{version}.json").read_text())
packets = {r["id"]: r["packet"] for r in read(ROOT / "private" / "wdc" / "bench" / "in" / "pairs.jsonl")}
base = {c["id"]: c for c in doc["cases"] if c["arm"] == "baseline"}
for c in doc["cases"]:
    if c["arm"] != "full" or c["correct"]:
        continue
    print(f'=== {c["id"]} {c["category"]} gold={c["gold"]} system={c["verdict"]} err={c["error"]} baseline={base[c["id"]]["verdict"]}')
    for side in ("left", "right"):
        p = packets[c["id"]][side]
        print(f'  {side}: {p.get("title","")[:160]} | brand={p.get("brand","")} | desc={p.get("description","")[:200]}')
