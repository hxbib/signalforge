"""Draw and freeze the 40-pair WDC v2 sample exactly as eval/PROTOCOL.md / eval/bench.py specify.

Raw corpus, packets and gold stay in private/wdc/ (gitignored). Only the freeze record (seed, file
digests, code SHA, per-case packet digests and strata) is written to eval/results/freeze.json.
"""
import gzip
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from bench import sample, read, digest  # noqa: E402

WDC = ROOT / "private" / "wdc"
BENCH = WDC / "bench"
SOURCES = {
    "computers": "https://data.dws.informatik.uni-mannheim.de/largescaleproductcorpus/data/v2_nonnorm/goldstandards/computers_gs.json.gz",
    "cameras": "https://data.dws.informatik.uni-mannheim.de/largescaleproductcorpus/data/v2_nonnorm/goldstandards/cameras_gs.json.gz",
}


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def packet_of(row):
    return {side: {k: row[k + "_" + side] for k in ("title", "description", "brand", "specTableContent")
                   if row.get(k + "_" + side)} for side in ("left", "right")}


def main():
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    files = {name: WDC / f"{name}_gs.json.gz" for name in SOURCES}
    (BENCH / "in").mkdir(parents=True, exist_ok=True)
    (BENCH / "gold").mkdir(parents=True, exist_ok=True)
    inputs, gold = BENCH / "in" / "pairs.jsonl", BENCH / "gold" / "pairs.jsonl"
    sample(files["computers"], files["cameras"], sha, inputs, gold)  # exclusive mode: refuses to overwrite
    # Strata (category x gold label) recovered by packet digest, for the by-stratum table only.
    lookup = {}
    for name, path in files.items():
        with gzip.open(path, "rt", encoding="utf8") as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    lookup[digest(packet_of(r))] = (name, "match" if str(r["label"]) == "1" else "different", r["pair_id"])
    labels = {g["id"]: g for g in read(gold)}
    cases = []
    for row in read(inputs):
        cat, lab, pair_id = lookup[digest(row["packet"])]
        assert lab == labels[row["id"]]["expected"]
        cases.append({"id": row["id"], "packet_sha": digest(row), "category": cat, "wdc_pair_id": pair_id})
    freeze = {
        "frozen_at": int(time.time()),
        "frozen_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "code_sha": sha,
        "seed": sha,
        "sampler": "eval/bench.py sample (10 positive + 10 negative per category, random.Random(seed))",
        "corpus": "WDC Product Data Corpus v2, non-normalized gold standard (Peeters, Primpeli, Bizer)",
        "corpus_sources": SOURCES,
        "corpus_sha256": {n: sha_file(p) for n, p in files.items()},
        "inputs_sha256": sha_file(inputs),
        "gold_sha256": sha_file(gold),
        "code_sha256": {p: sha_file(ROOT / p) for p in ("eval/bench.py", "eval/draw.py", "reference/deal_models.py", "reference/gemini_io.py")},
        "n": len(cases),
        "cases": cases,
    }
    out = ROOT / "eval" / "results" / "freeze.json"
    with out.open("x", encoding="utf8") as f:
        json.dump(freeze, f, indent=1)
    print(json.dumps({k: v for k, v in freeze.items() if k != "cases"}, indent=1))


if __name__ == "__main__":
    main()
