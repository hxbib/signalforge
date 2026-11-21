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
    if len(parts) >= 4 and parts[0].startswith("pair-") and parts[1] in ("baseline", "full"):
        done.setdefault(parts[0], {})[parts[1]] = (parts[2], parts[3].removeprefix("err="))
gold = {g["id"]: g["expected"] for g in read(ROOT / "private" / "wdc" / "bench" / "gold" / "pairs.jsonl")}
both = sorted(k for k, v in done.items() if len(v) == 2)
c = {a: Counter() for a in ("baseline", "full")}
for k in both:
    for a in c:
        verdict, err = done[k][a]
        c[a]["correct"] += verdict == gold[k]
        c[a]["abstained"] += verdict == "abstain"
        c[a]["errors"] += err != "None"
        c[a]["false_accepts"] += gold[k] == "different" and verdict == "match"
print(f"interim n={len(both)}: " + "; ".join(f"{a} correct {c[a]['correct']}/{len(both)} abstain {c[a]['abstained']} "
                                            f"errors {c[a]['errors']} false-accepts {c[a]['false_accepts']}" for a in c))
