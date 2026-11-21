"""Deterministic offer eligibility: the reference buyer.evaluate rule set with tiered identity.

Identity tiers (docs/CONTRACT.md section 0):
  merchant_barcode  the row publishes a barcode equal to the target GTIN
  catalog_product   the row belongs to the bound Shop universal product (row.product == target.upid)
  model_number      set on the row by compare.run_comparison only after its brand / model / pair checks
A row that publishes a valid barcode different from a known target GTIN is never the target item.
Everything else is unchanged from reference/buyer.py.
"""
from collections import defaultdict

import app  # noqa: F401  (puts reference/ on sys.path)
from buyer import amount, canonical_link
from compare_offers import gtin, same_gtin

TRUSTED_SOURCES = ("shop_native", "merchant_api", "merchant_page")


def _valid_gtin(value):
    try:
        gtin(value)
        return True
    except ValueError:
        return False


def _target(target):
    if isinstance(target, str):
        target = {"gtin": target}
    if not isinstance(target, dict):
        raise ValueError("target must be a dict")
    target_gtin = target.get("gtin")
    if target_gtin is not None:
        gtin(target_gtin)  # same validation buyer.evaluate applies; raises ValueError
    return target_gtin, target.get("upid")


def identity_basis(row, target_gtin, upid):
    """The tier that makes this row the target item, or None."""
    row_gtin = row.get("gtin")
    if target_gtin is not None and _valid_gtin(row_gtin):
        return "merchant_barcode" if same_gtin(row_gtin, target_gtin) else None
    basis = row.get("identity_basis")
    if basis == "catalog_product" and upid and row.get("product") == upid:
        return "catalog_product"
    if basis == "model_number":
        return "model_number"
    return None


def evaluate(target, goal, offers, as_of):
    """Server-owned normalized rows. Return all tied seller IDs. Do not infer location from a request."""
    target_gtin, upid = _target(target)
    mode = goal["fulfillment"]
    basis = goal["basis"]
    if basis not in ("item", "delivered") or not (mode == "online_us" or mode.startswith(("ship:", "pickup:"))):
        raise ValueError("unsupported comparison")
    if mode.startswith("ship:") and not (len(mode) == 10 and mode[5:].isascii() and mode[5:].isdigit()):
        raise ValueError("US ZIP required")
    if mode.startswith("pickup:") and not mode[7:].strip():
        raise ValueError("store location required")
    if basis == "delivered" and not mode.startswith("ship:"):
        raise ValueError("delivered total requires a destination")
    if type(as_of) is not int or as_of < 0:
        raise ValueError("invalid observation clock")
    groups = defaultdict(list)
    excluded, eligible = [], []
    for row in offers:
        seller = row.get("seller")
        listing = row.get("listing")
        reason = None
        if row.get("source") not in TRUSTED_SOURCES:
            reason = "discovery_only"
        elif not seller or not listing or not canonical_link(row.get("url")):
            reason = "missing_seller_or_listing"
        elif type(row.get("read_at")) is not int or not 0 <= as_of - row["read_at"] <= 120:
            reason = "stale"
        if reason:
            excluded.append({"seller": seller, "listing": listing, "reason": reason})
        else:
            groups[seller, listing].append(row)

    for (seller, listing), rows in sorted(groups.items()):
        row = max(rows, key=lambda x: x["read_at"])
        evidence_fields = ("gtin", "condition", "stock", "fulfillment", "unconditional", "currency", "item_cents")
        if basis == "delivered":
            evidence_fields += ("shipping_cents", "tax_cents", "fees_cents")
        reasons = []
        if any(any(r.get(k) != row.get(k) for k in evidence_fields) for r in rows):
            reasons.append("conflicting_sources")
        tier = identity_basis(row, target_gtin, upid)
        if tier is None:
            reasons.append("different_or_unverified_item")
        if row.get("condition") != "new":
            reasons.append("condition_unverified_or_used")
        if row.get("stock") != "in_stock":
            reasons.append({"out_of_stock": "sold_out", "sold_out": "sold_out", "orderable": "orderable_only"}.get(
                row.get("stock"), "stock_unverified"))
        fulfillment = row.get("fulfillment")
        if fulfillment != mode and not (mode == "online_us" and isinstance(fulfillment, str) and fulfillment.startswith("ship:")):
            reasons.append("fulfillment_mismatch")
        if row.get("unconditional") is not True:
            reasons.append("unverified_or_conditional_price")
        if row.get("currency") != "USD" or not amount(row.get("item_cents")):
            reasons.append("price_unverified")
        extras = [row.get(k) for k in ("shipping_cents", "tax_cents", "fees_cents")]
        if basis == "delivered" and not all(amount(v) for v in extras):
            reasons.append("total_unknown")
        if reasons:
            excluded.append({"seller": seller, "listing": listing, "reason": reasons[0], "reasons": reasons})
            continue
        cost = row["item_cents"] + (sum(extras) if basis == "delivered" else 0)
        eligible.append({"seller": seller, "listing": listing, "cents": cost, "url": row["url"],
                         "read_at": row["read_at"], "identity_basis": tier})
    eligible.sort(key=lambda r: (r["cents"], r["seller"], r["listing"]))
    floor = eligible[0]["cents"] if eligible else None
    winners = sorted({r["seller"] for r in eligible if r["cents"] == floor})
    return {"status": "winner" if winners else "abstain", "winners": winners, "price_cents": floor,
            "basis": basis, "fulfillment": mode, "eligible": eligible, "excluded": excluded}
