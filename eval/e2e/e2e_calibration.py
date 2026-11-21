"""Calibration metrics for the listing screen. Pure functions over (probability, label) pairs; stdlib only."""
from __future__ import annotations

from statistics import median

THRESHOLDS = (0.5, 0.75, 0.9)
BINS = 5


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def at_threshold(pairs: list[tuple[float, bool]], threshold: float) -> dict:
    tp = sum(1 for p, y in pairs if p >= threshold and y)
    fp = sum(1 for p, y in pairs if p >= threshold and not y)
    fn = sum(1 for p, y in pairs if p < threshold and y)
    tn = sum(1 for p, y in pairs if p < threshold and not y)
    return {"threshold": threshold, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn), "false_accept_rate": _ratio(fp, fp + tn)}


def reliability(pairs: list[tuple[float, bool]], bins: int = BINS) -> list[dict]:
    """Equal-width bins over [0, 1]: mean predicted probability vs observed positive rate."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        inside = [(p, y) for p, y in pairs if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        out.append({"bin": f"{lo:.1f}-{hi:.1f}", "count": len(inside),
                    "mean_predicted": round(sum(p for p, _ in inside) / len(inside), 4) if inside else None,
                    "observed_positive_rate": _ratio(sum(1 for _, y in inside if y), len(inside))})
    return out


def roc_auc(pairs: list[tuple[float, bool]]) -> float | None:
    """Rank statistic (Mann-Whitney U / (positives x negatives)); tied scores share their average rank."""
    pos = sum(1 for _, y in pairs if y)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    ordered = sorted(pairs, key=lambda x: x[0])
    rank_sum, i = 0.0, 0
    while i < len(ordered):
        j = i
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        average_rank = (i + 1 + j) / 2
        rank_sum += average_rank * sum(1 for _, y in ordered[i:j] if y)
        i = j
    return round((rank_sum - pos * (pos + 1) / 2) / (pos * neg), 4)


def brier(pairs: list[tuple[float, bool]]) -> float | None:
    return round(sum((p - (1.0 if y else 0.0)) ** 2 for p, y in pairs) / len(pairs), 4) if pairs else None


def case_pairs(case: dict) -> list[tuple[float, bool]]:
    return [(j["p_same"], bool(j["positive"])) for j in case.get("judgments") or []]


def top1(case: dict) -> bool | None:
    """Is the highest-scored listing of this case a positive? None when the case has no positive or no score."""
    pairs = case_pairs(case)
    if not pairs or not any(y for _, y in pairs):
        return None
    best = max(p for p, _ in pairs)
    return all(y for p, y in pairs if p == best)


def metrics(cases: list[dict]) -> dict:
    """Every frozen case is counted: a failed call is an error case with no judgments."""
    ok = [c for c in cases if c.get("status") == "ok"]
    pairs = [pair for c in ok for pair in case_pairs(c)]
    times = [c["ms"] for c in ok if type(c.get("ms")) is int]
    tops = [t for t in (top1(c) for c in ok) if t is not None]
    positives = sum(1 for _, y in pairs if y)
    return {"cases": len(cases), "calls": sum(1 for c in cases if c.get("status") in ("ok", "error") and c.get("called", True)),
            "error_cases": len(cases) - len(ok), "judgments": len(pairs), "not_scored": sum(c.get("not_scored") or 0 for c in ok),
            "positives": positives, "negatives": len(pairs) - positives,
            "cases_without_positive": sum(1 for c in ok if not any(y for _, y in case_pairs(c))),
            "median_ms_per_call": int(median(times)) if times else None,
            "thresholds": [at_threshold(pairs, t) for t in THRESHOLDS],
            "false_accept_rate_at_0.9": at_threshold(pairs, 0.9)["false_accept_rate"],
            "reliability": reliability(pairs), "roc_auc": roc_auc(pairs), "brier": brier(pairs),
            "top1_positive_cases": sum(tops), "cases_with_positive": len(tops)}
