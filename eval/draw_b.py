"""Held-out draw B: 40 new pairs, same strata and sampling rule as eval/bench.py sample, disjoint from draw A.

Seed = sha256 of eval/results/pairs-v1.json (immutable once written). Frozen to eval/results/freeze-b.json before
any model call; packets and gold stay in private/wdc/bench/b/.
"""
import gzip
import hashlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from bench import digest, put, read  # noqa: E402
from draw import SOURCES, WDC, sha_file  # noqa: E402

OUT = WDC / "bench" / "b"


def main():
    code_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    seed = sha_file(ROOT / "eval" / "results" / "pairs-v1.json")
    a_ids = {c["wdc_pair_id"] for c in json.loads((ROOT / "eval" / "results" / "freeze.json").read_text())["cases"]}
    rng = random.Random(seed)
    selected = []
    for name in ("computers", "cameras"):  # same order as bench.sample
        with gzip.open(WDC / f"{name}_gs.json.gz", "rt", encoding="utf8") as f:
            pool = [json.loads(line) for line in f if line.strip()]
        pool = [r for r in pool if r["pair_id"] not in a_ids]  # disjoint from draw A
        for label in (0, 1):
            rows = [r for r in pool if str(r["label"]) == str(label)]
            for row in rng.sample(sorted(rows, key=lambda r: str(r["pair_id"])), 10):
                packet = {}
                for side in ("left", "right"):
                    packet[side] = {k: row[k + "_" + side] for k in ("title", "description", "brand", "specTableContent")
                                    if row.get(k + "_" + side)}
                    if not packet[side].get("title"):
                        raise ValueError("missing listing title")
                selected.append((packet, "match" if label else "different", name, row["pair_id"]))
    rng.shuffle(selected)
    OUT.joinpath("in").mkdir(parents=True, exist_ok=True)
    OUT.joinpath("gold").mkdir(parents=True, exist_ok=True)
    packets, labels, cases, seen = [], [], [], set()
    for i, (data, label, cat, pid) in enumerate(selected):
        if digest(data) in seen:
            raise ValueError("duplicate content in draw; record collision, do not silently redraw")
        seen.add(digest(data))
        row = {"id": f"b-pair-{i+1:02d}", "task": "pair", "packet": data}
        packets.append(row)
        labels.append({"id": row["id"], "sha": digest(row), "expected": label})
        cases.append({"id": row["id"], "packet_sha": digest(row), "category": cat, "wdc_pair_id": pid})
    assert not ({c["wdc_pair_id"] for c in cases} & a_ids)
    inputs, gold = OUT / "in" / "pairs.jsonl", OUT / "gold" / "pairs.jsonl"
    put(inputs, packets)
    put(gold, labels)
    freeze = {"draw": "B (held-out)", "frozen_at": int(time.time()), "frozen_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "code_sha": code_sha, "seed": seed, "seed_rule": "sha256 of eval/results/pairs-v1.json",
              "sampler": "eval/draw_b.py: eval/bench.py sample rule (10 pos + 10 neg per category) on pools minus draw A pair ids",
              "corpus": "WDC Product Data Corpus v2, non-normalized gold standard (Peeters, Primpeli, Bizer)",
              "corpus_sources": SOURCES, "corpus_sha256": {n: sha_file(WDC / f"{n}_gs.json.gz") for n in SOURCES},
              "disjoint_from": "freeze.json (draw A)", "inputs_sha256": sha_file(inputs), "gold_sha256": sha_file(gold),
              "n": len(cases), "cases": cases}
    with (ROOT / "eval" / "results" / "freeze-b.json").open("x", encoding="utf8") as f:
        json.dump(freeze, f, indent=1)
    print(json.dumps({k: v for k, v in freeze.items() if k != "cases"}, indent=1))


if __name__ == "__main__":
    main()
