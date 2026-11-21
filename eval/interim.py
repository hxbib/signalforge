"""Interim tally from a running eval/run_pairs.py log (pairs where both arms finished). Gold is read after the answers."""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from bench import read  # noqa: E402

log = Path(sys.argv[1])
done = {}
for line in log.read_text().splitlines():
    parts = line.split()
    if len(parts) >= 4 and "pair-" in parts[0] and parts[1] in ("baseline", "full"):
        done.setdefault(parts[0], {})[parts[1]] = (parts[2], parts[3].removeprefix("err="))
prefix = sys.argv[2] if len(sys.argv) > 2 else "pair-"
gdir = ROOT / "private" / "wdc" / "bench" / ("b" if prefix.startswith("b-") else "")
gold = {g["id"]: g["expected"] for g in read(gdir / "gold" / "pairs.jsonl")}
done = {k: v for k, v in done.items() if k.startswith(prefix)}
arms = sorted({a for v in done.values() for a in v})
both = sorted(k for k, v in done.items() if len(v) == len(arms))
c = {a: Counter() for a in arms}
for k in both:
    for a in c:
        verdict, err = done[k][a]
        c[a]["correct"] += verdict == gold[k]
        c[a]["abstained"] += verdict == "abstain"
        c[a]["errors"] += err != "None"
        c[a]["false_accepts"] += gold[k] == "different" and verdict == "match"
print(f"interim n={len(both)}: " + "; ".join(f"{a} correct {c[a]['correct']}/{len(both)} abstain {c[a]['abstained']} "
                                            f"errors {c[a]['errors']} false-accepts {c[a]['false_accepts']}" for a in c))
