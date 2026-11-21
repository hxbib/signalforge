"""Share this exact eligibility function between the web route and the frozen benchmark."""
from collections import defaultdict
from urllib.parse import urlsplit
from compare_offers import same_gtin


def canonical_link(value):
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password


def amount(value):
    return type(value) is int and 0 <= value <= 100_000_000


def evaluate(target, goal, offers, as_of):
    """Server-owned normalized rows. Return all tied seller IDs. Do not infer location from a request."""
    from compare_offers import gtin
    gtin(target)
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
        if row.get("source") not in ("shop_native", "merchant_api", "merchant_page"):
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
        evidence_fields = ("gtin","condition","stock","fulfillment","unconditional","currency","item_cents")
        if basis == "delivered":
            evidence_fields += ("shipping_cents","tax_cents","fees_cents")
        reasons = []
        if any(any(r.get(k) != row.get(k) for k in evidence_fields) for r in rows):
            reasons.append("conflicting_sources")
        if not same_gtin(row.get("gtin"), target):
            reasons.append("different_or_unverified_item")
        if row.get("condition") != "new":
            reasons.append("condition_unverified_or_used")
        if row.get("stock") != "in_stock":
            reasons.append({"out_of_stock":"sold_out","sold_out":"sold_out","orderable":"orderable_only"}.get(row.get("stock"), "stock_unverified"))
        fulfillment = row.get("fulfillment")
        if fulfillment != mode and not (mode == "online_us" and isinstance(fulfillment,str) and fulfillment.startswith("ship:")):
            reasons.append("fulfillment_mismatch")
        if row.get("unconditional") is not True:
            reasons.append("unverified_or_conditional_price")
        if row.get("currency") != "USD" or not amount(row.get("item_cents")):
            reasons.append("price_unverified")
        extras = [row.get(k) for k in ("shipping_cents","tax_cents","fees_cents")]
        if basis == "delivered" and not all(amount(v) for v in extras):
            reasons.append("total_unknown")
        if reasons:
            excluded.append({"seller":seller, "listing":listing, "reason":reasons[0], "reasons":reasons})
            continue
        cost = row["item_cents"] + (sum(extras) if basis == "delivered" else 0)
        eligible.append({"seller":seller,"listing":listing,"cents":cost,"url":row["url"],"read_at":row["read_at"]})
    eligible.sort(key=lambda r: (r["cents"],r["seller"],r["listing"]))
    floor = eligible[0]["cents"] if eligible else None
    winners = sorted({r["seller"] for r in eligible if r["cents"] == floor})
    return {"status":"winner" if winners else "abstain","winners":winners,"price_cents":floor,
            "basis":basis,"fulfillment":mode,"eligible":eligible,"excluded":excluded}

