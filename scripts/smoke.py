"""Live smoke for the three model jobs through the app.models facade.

    .venv/bin/python scripts/smoke.py <image> [--note TEXT]

One real call each: view (gemini-4-neon), gate (gemini-decision-1), goal (gemini-4-carbon).
Prints one JSON line per job {job, model, id, ms, ok, summary}; exits 1 on any failure.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app  # noqa: E402,F401
from app import models  # noqa: E402
from app.models import io as model_io  # noqa: E402
from gemini_io import CARBON, DECISION, NEON  # noqa: E402
from live_smoke import clean_image  # noqa: E402


def line(job, model, ok, summary, id=None, ms=None):
    print(json.dumps({"job": job, "model": model, "id": id, "ms": ms, "ok": ok, "summary": summary}), flush=True)
    return ok


def failed(job, model, exc):
    if isinstance(exc, models.ModelCallError):
        return line(exc.job, exc.model, False, exc.detail)
    return line(job, model, False, model_io.detail_message(exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    with args.image.open("rb") as handle:
        image = clean_image(handle.read(5_000_001))
    ok = True
    try:
        seen = models.inspect(image, args.note)
    except Exception as exc:
        ok = failed("view+gate", f"{NEON}+{DECISION}", exc)
    else:
        visual, decision = seen["visual"], seen["decision"]
        for receipt in seen["receipts"]:
            if receipt["job"] == "view":
                summary = " · ".join(x for x in (visual["name"], visual["brand"], visual["part_number"],
                                                 "barcode " + visual["barcode"] if visual["barcode"] else "",
                                                 "target " + visual["target_count"]) if x)
            else:
                summary = f"{decision['choice']} · confidence {decision['confidence']:.2f} · pass {decision['pass']}"
            if receipt.get("attempt"):
                summary = f"second look ({'accepted' if seen['recovery']['accepted'] else 'not accepted'}) · " + \
                    (summary if seen["recovery"]["accepted"] else "first reading kept")
            # a completed, validated call is ok; an id exists only for a stored interaction (never for a decision)
            ok = line(receipt["job"], receipt["model"], True, summary, receipt["id"], receipt["ms"]) and ok
    try:
        goal = models.parse_goal(image, args.note)
    except Exception as exc:
        ok = failed("goal", CARBON, exc) and ok
    else:
        intent, receipt = goal["intent"], goal["receipt"]
        summary = f"{intent['operation']} · {intent['basis']} · {intent['fulfillment']} · market {intent['market']}"
        ok = line("goal", receipt["model"], True, summary, receipt["id"], receipt["ms"]) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
