"""A rule applies to one seller/variant in one market. Timestamps are UTC Unix seconds."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

MODES = ("restock", "becomes_eligible")
COMPARISONS = ("lt", "lte")
MAX_PRICE = 10_000_000
MAX_GAP = 150  # project choice for a nominal 60-second poll
SOURCE = "approved_feed"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def integer(value, lo, hi):
    return type(value) is int and lo <= value <= hi


@dataclass(frozen=True)
class Rule:
    seller: str
    variant: str
    mode: str
    op: str
    cents: int

    def __post_init__(self):
        if not all(type(v) is str and 0 < len(v) <= 256 for v in (self.seller, self.variant)):
            raise ValueError("unbound source identity")
        if self.mode not in MODES or self.op not in COMPARISONS:
            raise ValueError("unsupported rule")
        if not integer(self.cents, 1, MAX_PRICE):
            raise ValueError("USD cap must be integer cents in the supported range")


@dataclass(frozen=True)
class Seen:
    seller: str
    variant: str
    available: bool | None
    cents: int | None
    currency: str
    t: int
    status: str = "ok"

    def issue(self, rule):
        if self.status != "ok":
            return "source_unknown"
        if (self.seller, self.variant) != (rule.seller, rule.variant):
            return "identity_mismatch"
        if self.currency != "USD":
            return "currency_unverified"
        if type(self.available) is not bool or not integer(self.cents, 0, 2**53-1):
            return "incomplete_observation"
        return None


def qualifies(rule, row):
    return row.available and (row.cents < rule.cents if rule.op == "lt" else row.cents <= rule.cents)


@dataclass(frozen=True)
class Verdict:
    alert: bool
    reason: str


def decide(rule, before, after):
    if type(after.t) is not int or after.t < 0:
        raise ValueError("invalid observation time")
    issue = after.issue(rule)
    if issue:
        return Verdict(False, issue)
    if before is None:
        return Verdict(False, "baseline")
    if before.issue(rule) or not 0 < after.t - before.t <= MAX_GAP:
        return Verdict(False, "continuity_lost")
    if not after.available:
        return Verdict(False, "currently_unavailable")
    if not qualifies(rule, after):
        return Verdict(False, "outside_price_limit")
    if rule.mode == "restock":
        return Verdict(not before.available, "stock_returned" if not before.available else "already_in_stock")
    entering = not qualifies(rule, before)
    return Verdict(entering, "entered_offer" if entering else "already_eligible")


def describe(rule):
    event = "returns to stock" if rule.mode == "restock" else "first enters the in-stock price range"
    bound = "below" if rule.op == "lt" else "at or below"
    return f"{event}, {bound} USD {rule.cents // 100}.{rule.cents % 100:02d}"


def rule_hash(rule):
    return digest({"version": 1, "source": SOURCE, "market": "US", "currency": "USD",
                   "first": "baseline", "max_gap_seconds": MAX_GAP, **asdict(rule)})
