"""Insert the evaluation numbers into README.md from eval/results/summary.json. No hand-typed numbers.

  python scripts/readme_numbers.py            rewrite the block between the EVAL markers
  python scripts/readme_numbers.py --check    exit 1 if README.md differs from what the file says
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "eval" / "results" / "summary.json"
README = ROOT / "README.md"
START, END = "<!-- EVAL:START (written by scripts/readme_numbers.py; do not edit by hand) -->", "<!-- EVAL:END -->"
ARMS = (("baseline", "Baseline: one Gemini 4 Carbon call"),
        ("full", "System: Neon extraction + Carbon verdict + agreement + verbatim-quote check"))


def _arm_row(label, a):
    lat = a.get("latency") or {}
    return (f'| {label} | {a["correct"]}/{a["n"]} | {a["wrong"]} | {a["abstained"]} | {a["errors"]} | '
            f'{a["false_accepts"]}/{a["actual_negatives"]} | {a["false_rejects"]}/{a["actual_positives"]} | '
            f'{lat.get("median_ms", "unknown")} | {lat.get("p90_ms", "unknown")} |')


def block(summary):
    if summary is None:
        return "Not yet measured: `eval/results/summary.json` does not exist, so no number is shown here."
    f, p, b = summary.get("freeze") or {}, summary["paired"], summary.get("experiment_b") or {}
    lines = [
        f'**Experiment A — {summary["experiment"].split(": ", 1)[-1]}.** {summary["n"]} pairs, drawn before either arm ran '
        f'(frozen {f.get("frozen_at_iso", "unknown")}, seed `{str(f.get("seed", "unknown"))[:7]}`), results version '
        f'`{summary["latest_version"]}`, code `{summary["code_sha"][:7]}`, models {", ".join("`" + m + "`" for m in summary["model_ids"])}. '
        "An abstention counts as incorrect.",
        "",
        "| Arm | Correct | Wrong answers | Abstained | Runtime errors | False matches / actual different | "
        "False rejections / actual same | Median ms | p90 ms |",
        "|---|---|---|---|---|---|---|---|---|",
        *[_arm_row(label, summary["arms"][key]) for key, label in ARMS],
        "",
        f'Paired on the same {summary["n"]} pairs: both arms correct {p["both"]}, only the baseline correct {p["baseline_only"]}, '
        f'only the system correct {p["full_only"]}, neither {p["neither"]}.',
        "",
        "| Stratum | Baseline correct | Baseline abstained | System correct | System abstained |",
        "|---|---|---|---|---|",
        *[f'| {s["stratum"]} | {s["baseline"]["correct"]}/{s["baseline"]["n"]} | {s["baseline"]["abstained"]} | '
          f'{s["full"]["correct"]}/{s["full"]["n"]} | {s["full"]["abstained"]} |' for s in summary["by_stratum"]],
        "",
        f'**Experiment B — five fresh photo-to-offer cases:** status `{b.get("status", "unknown")}`. {b.get("statement", "")}'.rstrip(),
        "",
        "Source files: " + ", ".join(f"`eval/results/{name}`" for name in ["summary.json", *summary.get("generated_from", [])]) + ".",
    ]
    return "\n".join(lines)


def main():
    summary = json.loads(SUMMARY.read_text()) if SUMMARY.is_file() else None
    text = README.read_text()
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    new = head + START + "\n" + block(summary) + "\n" + END + tail
    if "--check" in sys.argv:
        print("README evaluation block matches" if new == text else "README evaluation block is OUT OF DATE",
              SUMMARY.name if summary else "(no summary.json: not yet measured)")
        raise SystemExit(0 if new == text else 1)
    README.write_text(new)
    print("README.md evaluation block written from", SUMMARY if summary else "nothing (not yet measured)")


if __name__ == "__main__":
    main()
