"""Finite behavioral questions for one exact binding. Search is local and deterministic."""
from dataclasses import asdict, dataclass, replace
from itertools import product
from core import MODES, COMPARISONS, Seen, decide, digest, rule_hash


@dataclass(frozen=True)
class World:
    before: Seen
    after: Seen

    @property
    def key(self):
        return digest({"kind": "hypothetical", **asdict(self)})

    def payload(self):
        return {"kind": "hypothetical", "id": self.key, **asdict(self)}


def alternatives(draft):
    # Wording can make both axes feel obvious. The user can still reject a concrete example.
    return [replace(draft, mode=mode, op=op) for mode, op in product(MODES, COMPARISONS)]


def worlds(rule):
    k = rule.cents
    # Nearest representable amounts make the boundary visible. Each pair changes one or two facts.
    for a, b, p, q in product((False, True), (False, True), (k-1, k, k+1), (k-1, k, k+1)):
        old = Seen(rule.seller, rule.variant, a, p, "USD", 100)
        yield World(old, replace(old, available=b, cents=q, t=160))


def predicts(rule, world):
    return decide(rule, world.before, world.after).alert


def question(family):
    if not family:
        raise ValueError("no compatible interpretation")
    base = family[0]
    if any((r.seller, r.variant, r.cents) != (base.seller, base.variant, base.cents) for r in family):
        raise ValueError("bind one identity and cap before asking behavioral questions")
    ranked = []
    for world in worlds(base):
        yes = sum(predicts(r, world) for r in family)
        if 0 < yes < len(family):
            changes = int(world.before.available != world.after.available) + int(world.before.cents != world.after.cents)
            # Prefer an in-stock price drop to illustrate an ambiguous event, then a one-field change.
            price_only = world.before.available and world.after.available and world.before.cents > world.after.cents
            span = abs(world.before.cents-world.after.cents)
            ranked.append((abs(2*yes-len(family)), not price_only, changes, span, world.key, world))
    if not ranked:
        return None
    return min(ranked, key=lambda item: item[:-1])[-1]


def resolve(draft, answers):
    if len(answers) > 3:
        raise ValueError("question budget exceeded")
    family = alternatives(draft)
    witnesses = []
    for answer in answers:
        world = question(family)
        if world is None or set(answer) != {"id", "notify"} or answer["id"] != world.key or type(answer["notify"]) is not bool:
            raise ValueError("stale or malformed witness answer")
        family = [r for r in family if predicts(r, world) == answer["notify"]]
        witnesses.append((world, answer["notify"]))
    return family, witnesses, question(family)


def safety_checks(rule):
    # Fixed semantic invariants with author-specified labels, separate from preference answers.
    valid = Seen(rule.seller, rule.variant, False, rule.cents-1, "USD", 100)
    match = replace(valid, available=True, t=160)
    outage = replace(valid, status="missing")
    checks = [
        ("first_match", None, match, False), ("witnessed_return", valid, match, True),
        ("already_matching", replace(valid, available=True), match, False),
        ("over_cap", valid, replace(match, cents=rule.cents+1), False),
        ("other_variant", valid, replace(match, variant=rule.variant+"-other"), False),
        ("other_seller", valid, replace(match, seller=rule.seller+"-other"), False),
        ("unknown_gap", outage, match, False), ("source_error", valid, replace(match, status="http_error"), False),
        ("other_currency", valid, replace(match, currency="CAD"), False),
        ("missing_price", valid, replace(match, cents=None), False),
        ("out_of_order", valid, replace(match, t=99), False), ("long_gap", valid, replace(match, t=260), False),
    ]
    rows = []
    for name, old, new, expected in checks:
        verdict = decide(rule, old, new)
        rows.append({"name": name, "expected": expected, "actual": verdict.alert, "reason": verdict.reason,
                     "pass": verdict.alert == expected, "before": asdict(old) if old else None, "after": asdict(new)})
    return rows


def verify(draft, answers, candidate):
    family, labeled, remaining = resolve(draft, answers)
    if remaining is not None or len(family) != 1 or family[0] != candidate:
        raise ValueError("unresolved preference or incompatible rule")
    rows = safety_checks(candidate)
    for w, label in labeled:
        actual = predicts(candidate, w)
        rows.append({"name": w.key, "expected": label, "actual": actual, "pass": actual == label,
                     "before": asdict(w.before), "after": asdict(w.after)})
    if not all(row["pass"] for row in rows):
        raise ValueError("verification failed")
    summary = {"suite": "witness-v1", "origin": "development_and_user_answers",
               "rule_hash": rule_hash(candidate), "rows": rows}
    return {**summary, "proof_hash": digest(summary)}
