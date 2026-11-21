"""Insert the evaluation numbers into README.md from eval/results/summary.json (and e2e-summary.json). No hand-typed numbers.

  python scripts/readme_numbers.py            rewrite the block between the EVAL markers
  python scripts/readme_numbers.py --check    exit 1 if README.md differs from what the file says
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "eval" / "results" / "summary.json"
E2E = ROOT / "eval" / "results" / "e2e-summary.json"
README = ROOT / "README.md"
START, END = "<!-- EVAL:START (written by scripts/readme_numbers.py; do not edit by hand) -->", "<!-- EVAL:END -->"
ARM_NAMES = {"baseline": "Baseline: one Gemini 4 Carbon call", "full": "System listing check", "system": "SignalForge full system, zero taps",
             "system_one_tap": "SignalForge with ONE tap: a simulated buyer answers one choice question using the gold label"}


def _v(value):
    return "unknown" if value is None else str(value)


def _pair_row(label, arm, a):
    lat = a.get("latency") or {}
    return (f'| {label} | {arm} | {_v(a.get("correct"))}/{_v(a.get("n"))} | {_v(a.get("wrong"))} | {_v(a.get("abstained"))} | '
            f'{_v(a.get("errors"))} | {_v(a.get("false_accepts"))}/{_v(a.get("actual_negatives"))} | '
            f'{_v(lat.get("median_ms"))} | {_v(lat.get("p90_ms"))} |')


def _freeze(name, f):
    if not f:
        return None
    return (f'- Draw {name}: {_v(f.get("n"))} pairs, frozen {_v(f.get("frozen_at_iso"))} before any arm ran on it, seed '
            f'`{_v(f.get("seed"))[:12]}`' + (f' ({f["seed_rule"]})' if f.get("seed_rule") else "") + ".")


def _pairs(summary):
    out = [f'**Experiment A: {summary.get("experiment", "listing identity on WDC v2 product pairs")}** '
           "An abstention or an error counts as incorrect.", ""]
    out += [line for line in (_freeze("A", summary.get("freeze")), _freeze("B", summary.get("freeze_b"))) if line]
    out += ["", "| Run | Arm | Correct | Wrong answers | Abstained | Runtime errors | False matches / actual different | Median ms | p90 ms |",
            "|---|---|---|---|---|---|---|---|---|"]
    for v in summary.get("versions") or []:
        label = v.get("label") or v.get("version") or v.get("name")
        ran = v.get("arms_run") or list(v.get("arms") or {})
        for key in ("baseline", "full"):
            if key not in (v.get("arms") or {}):
                continue
            name = ARM_NAMES[key] + (f' (`{v["system_arm"]}`)' if key == "full" and v.get("system_arm") else "")
            if key not in ran:
                name += f' (from {v.get("baseline_source", "an earlier run, not re-run")})'
            out.append(_pair_row(f'{label}, code `{_v(v.get("code_sha"))[:7]}`', name, v["arms"][key]))
    out.append("")
    for v in summary.get("versions") or []:
        p = v.get("paired")
        if p:
            out.append(f'- {v.get("label") or v.get("version")}: paired on the same pairs, both correct {_v(p.get("both"))}, only the '
                       f'baseline {_v(p.get("baseline_only"))}, only the system {_v(p.get("full_only"))}, neither {_v(p.get("neither"))}.')
    if summary.get("reading"):
        out += ["", "Reading written by `eval/summarize.py`:", ""] + [f"- {line}" for line in summary["reading"]]
    if summary.get("latency_note"):
        out += ["", f'Latency: {summary["latency_note"]}.']
    return out


def _e2e_row(label, key, a, n, note=""):
    return (f'| {label} | {ARM_NAMES[key]}{note} | {_v(a.get("correct"))}/{_v(n)} | {_v(a.get("false_accepts"))}/{_v(n)} | '
            f'{_v(a.get("abstained"))}/{_v(n)} | {_v(a.get("errors"))}/{_v(n)} | {_v(a.get("invalid_offers"))}/{_v(a.get("answered"))} | '
            f'{_v(a.get("cheapest_agreement"))}/{_v(n)} | {_v(a.get("median_ms"))} |')


def _screen(name, sc):
    out = ["", f'**Decision 1 listing screen, calibration{name}** (run `{_v(sc.get("run"))}`, `{_v(sc.get("model"))}`, one call per case, '
               f'{_v(sc.get("cases"))} cases): {_v(sc.get("judgments"))} listing judgments ({_v(sc.get("positives"))} positive, '
               f'{_v(sc.get("negatives"))} negative), {_v(sc.get("error_cases"))} failed calls, ROC AUC {_v(sc.get("roc_auc"))}, '
               f'Brier {_v(sc.get("brier"))}, top-scored listing is the drawn product in '
               f'{_v(sc.get("top1_positive_cases"))}/{_v(sc.get("cases_with_positive"))} cases that had one.', "",
           "| Threshold | True accepts | False accepts | Missed | Precision | Recall | False-accept rate |", "|---|---|---|---|---|---|---|"]
    return out + [f'| p >= {_v(t.get("threshold"))} | {_v(t.get("tp"))} | {_v(t.get("fp"))} | {_v(t.get("fn"))} | {_v(t.get("precision"))} | '
                  f'{_v(t.get("recall"))} | {_v(t.get("false_accept_rate"))} |' for t in sc.get("thresholds") or []]


def _e2e(e):
    if not e:
        return ["**End-to-end benchmark (photo in, offer out):** not yet measured; `eval/results/e2e-summary.json` does not exist."]
    runs = e.get("runs") or [{"name": e.get("run"), "label": f'run {_v(e.get("run"))} · draw A', "code_sha": e.get("code_sha"),
                              "tree_dirty": e.get("tree_dirty"), "pair_version": e.get("pair_version"), "n": e.get("n"),
                              "arms": e.get("arms"), "reading": e.get("reading")}]
    out = ["**End-to-end benchmark (photo in, offer out):** catalog photos drawn blind and frozen before any arm ran on them, blank note, "
           "no follow-up answer given, so a question from the app counts as no answer. Baseline: one Gemini 4 Carbon call handed the "
           "photo and a list of listings. Protocol: `eval/e2e/README.md`."
           + (f' Headline run: `{e["headline_run"]}`.' if e.get("headline_run") else ""), "",
           "| Run | Arm | Correct product | Wrong product | Stopped without an answer | Errors | Invalid offers / answered | "
           "Cheapest agrees with reference | Median ms |", "|---|---|---|---|---|---|---|---|---|"]
    for r in runs:
        kind = "held-out" if r.get("held_out") else "in-sample" if r.get("in_sample") else "first run"
        label = (f'{_v(r.get("label") or r.get("name"))}{"" if kind == "first run" else f" ({kind})"}, code `{_v(r.get("code_sha"))[:7]}`'
                 + (" plus uncommitted changes" if r.get("tree_dirty") else "")
                 + (f', visual_match {"on" if r["visual_match"] else "off"}' if "visual_match" in r else ""))
        for key in ("system", "system_one_tap", "baseline"):
            a = (r.get("arms") or {}).get(key)
            if a:
                copied = key == "baseline" and r.get("baseline_source") and "not re-run" in str(r["baseline_source"])
                note, t = (f' (from {r["baseline_source"]})' if copied else ""), r.get("one_tap") or {}
                if key == "system_one_tap" and t.get("code_sha"):   # this arm can run on a later commit than the row's other arms
                    note = (f' (this arm: code `{str(t["code_sha"])[:7]}`' + (" plus uncommitted changes" if t.get("tree_dirty") else "")
                            + (f', visual_match {"on" if t["visual_match"] else "off"}' if "visual_match" in t else "") + ")")
                out.append(_e2e_row(label, key, a, r.get("n"), note))
    for r in runs:
        t = r.get("one_tap")
        if t:
            keys = ("cases", "choice_questions", "text_questions", "answered_without_question", "gold_offered", "gold_first",
                    "answered_gold", "answered_none")
            out += ["", f'One-tap arm of {_v(r.get("label") or r.get("name"))}: '
                        + ", ".join(f"{k.replace('_', ' ')} {_v(t[k])}" for k in keys if k in t) + "."
                        + (f' Note recorded with the run: {t["note"]}' if t.get("note") else "")]
    for r in runs:
        if r.get("reading"):
            out += ["", f'Reading of {_v(r.get("label") or r.get("name"))}, written by the benchmark script itself (`eval/e2e`):', ""]
            out += [f"- {line}" for line in r["reading"]]
    screens = e.get("screens") or ({"a": e["screen"]} if e.get("screen") else {})
    for name, sc in screens.items():
        if sc:
            out += _screen(f" on draw {str(name).upper()}", sc)
    return out


def _headline(summary, e):
    """Strongest and weakest measured results, side by side, from the held-out rows where they exist."""
    out, vs = [], summary.get("versions") or []
    arms = [(v, k, a) for v in vs for k, a in (v.get("arms") or {}).items() if k in (v.get("arms_run") or [k])]
    if arms and all(a.get("false_accepts") == 0 for _, _, a in arms):
        out.append(f'Strongest: 0 false matches in every arm of every listing-check run ({len(arms)} arm runs, each 0/'
                   f'{_v(arms[0][2].get("actual_negatives"))} on the actually different pairs).')
    sc = ((e or {}).get("screens") or {}).get("a") or (e or {}).get("screen")
    if sc:
        out.append(f'Decision 1 listing screen on the first blind draw: ROC AUC {_v(sc.get("roc_auc"))} over {_v(sc.get("judgments"))} '
                   f'listing judgments, median {_v(sc.get("median_ms_per_call"))} ms per call.')
    held = [v for v in vs if v.get("held_out")]
    if held:
        f, b = held[-1]["arms"]["full"], held[-1]["arms"]["baseline"]
        out.append(f'Weakest, stated as plainly: on the held-out pair draw the listing check is correct on {_v(f.get("correct"))}/{_v(f.get("n"))} '
                   f'against {_v(b.get("correct"))}/{_v(b.get("n"))} for one Carbon call.')
    runs = [r for r in ((e or {}).get("runs") or []) if r.get("held_out")] or ((e or {}).get("runs") or [])[:1]
    if runs:
        r = runs[-1]
        sy, ba, tap = r["arms"].get("system") or {}, r["arms"].get("baseline") or {}, r["arms"].get("system_one_tap")
        out.append(f'End to end on {"held-out " if r.get("held_out") else ""}blind catalog photos one Carbon call names the right product on '
                   f'{_v(ba.get("correct"))}/{_v(r.get("n"))}; the full system on {_v(sy.get("correct"))}/{_v(r.get("n"))} with zero taps'
                   + (f' and {_v(tap.get("correct"))}/{_v(r.get("n"))} with one simulated confirming tap' if tap else "") + ".")
    return ["**Summary.** " + " ".join(out), ""] if out else []


def block(summary):
    if summary is None:
        return "Not yet measured: `eval/results/summary.json` does not exist, so no number is shown here."
    b = summary.get("experiment_b") or {}
    files = list(summary.get("generated_from", []))
    e2e = json.loads(E2E.read_text()) if E2E.is_file() else summary.get("e2e")   # the file is the source of summary["e2e"]
    if e2e:
        files += [name for name in ("e2e-summary.json", "e2e-freeze.json", "e2e-freeze-b.json") if name not in files and (E2E.parent / name).is_file()]
    lines = _headline(summary, e2e) + _pairs(summary) + [""] + _e2e(e2e) + [
        "",
        f'**Experiment B (five rights-cleared photo-to-offer cases):** status `{b.get("status", "unknown")}`. {b.get("statement", "")}'.rstrip(),
        "",
        "Source files: " + ", ".join(f"`eval/results/{name}`" for name in ["summary.json", *files]) + ".",
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
