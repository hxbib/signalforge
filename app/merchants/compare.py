"""Merchant fan-out, tiered identity, ranking and the shopper-facing result for one bound target.

run_comparison(target, goal, rows) takes shop_native Quote rows, asks each merchant's own storefront to
confirm (chains.confirm), runs the two-model listing check for model-number candidates (models.pair),
ranks with ranking.evaluate and builds the Result / Coverage view-models of docs/CONTRACT.md section 4.
Deterministic code decides; a model verdict only ever grants the model_number identity tier.
"""
from concurrent.futures import ThreadPoolExecutor, wait
import json
import re
import sys
import time

from app.merchants.ranking import evaluate, _valid_gtin
from compare_offers import gtin as normalize_gtin, same_gtin
from buyer import amount

MAX_CONFIRM_MERCHANTS = 12
CONFIRM_WORKERS = 6
MAX_PAIR_CALLS = 4
PAIR_WORKERS = 4
SCREEN_PAIR_MIN = 0.5          # Decision 1 screen: a candidate needs p >= 0.5 (when screened) to get a listing check
SCREEN_DIFFERENT_MAX = 0.10    # ... and p <= 0.10 keeps it a lead outright. The screen never grants identity.
CONFIRM_PHASE_S = 9.0          # merchant confirms and listing checks run side by side, each with its own cap
PAIR_PHASE_S = 9.0
PAIR_LATE = "identity check did not finish in time"

HEADLINE_MANY = "Lowest verified among the stores checked"
HEADLINE_ONE = "One verified offer"
HEADLINE_NONE = "No verified offer yet"

SENTENCES = {
    "sold_out": "Sold out",
    "orderable_only": "Orderable only, not confirmed in stock",
    "stock_unverified": "Stock not confirmed",
    "condition_unverified_or_used": "Used or condition not confirmed as new",
    "conflicting_sources": "The catalog and the merchant's own page disagree",
    "fulfillment_mismatch": "Does not offer the requested delivery",
    "stale": "Price reading is too old to trust",
    "different_or_unverified_item": "Different item or identity not confirmed",
    "unverified_or_conditional_price": "Price needs a membership, coupon or other condition",
    "price_unverified": "Price not confirmed",
    "total_unknown": "Shipping, tax or fees unknown for this destination",
    "discovery_only": "Discovery listing, not a merchant source",
    "missing_seller_or_listing": "Seller, listing or secure link missing",
}

# Reasons whose price is still a genuine, current, same-item observation (usable for "$X less, but ...").
NEAR_MISS_REASONS = ("sold_out", "orderable_only", "stock_unverified", "condition_unverified_or_used",
                     "fulfillment_mismatch", "unverified_or_conditional_price")


# ---------------------------------------------------------------- helpers

def _tokens(text):
    return set(re.findall(r"[0-9a-z]+(?:[-/.][0-9a-z]+)*", str(text or "").casefold()))


def title_carries(title, brand, mpn):
    """The listing's own title names the brand (token or substring) and the exact model number as a token."""
    if not brand or not mpn or not title:
        return False
    low = str(title).casefold()
    b = brand.strip().casefold()
    return (b in low or b in _tokens(title)) and mpn.strip().casefold() in _tokens(title)


def _money(cents):
    return "${:,}.{:02d}".format(abs(cents) // 100, abs(cents) % 100)


def _key(row):
    return row.get("seller"), row.get("listing")


def _clip(text, n=160):
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def _offer(row, *, verified=False, confirmed_by=(), identity_basis=None):
    return {
        "seller": row.get("seller"), "seller_name": row.get("seller_name") or row.get("seller_domain") or "Unknown merchant",
        "listing": row.get("listing"), "title": row.get("title") or "", "url": row.get("url"),
        "image_url": row.get("image_url"),
        "item_cents": row.get("item_cents") if amount(row.get("item_cents")) and row.get("currency") else None,
        "currency": row.get("currency"), "condition": row.get("condition") or "unknown",
        "stock": row.get("stock") or "unknown", "fulfillment": row.get("fulfillment"),
        "source": row.get("source") if row.get("source") in ("shop_native", "merchant_api", "merchant_page") else "discovery",
        "read_at": row.get("read_at"), "verified": bool(verified), "confirmed_by": list(confirmed_by),
        "identity_basis": identity_basis, "gtin": row.get("gtin") if _valid_gtin(row.get("gtin")) else None,
        "screen_p": _screen_p(row),
    }


CAUTION_MIN_SELLERS = 3
CAUTION_PERCENT = 40


def price_caution(eligible, floor):
    """Deterministic outlier note. Median over each eligible seller's cheapest price; integer cents only.
    Nothing is hidden or re-ranked: the winner stays the winner."""
    if floor is None:
        return None
    best = {}
    for e in eligible:
        if e["seller"] not in best or e["cents"] < best[e["seller"]]:
            best[e["seller"]] = e["cents"]
    k = len(best)
    if k < CAUTION_MIN_SELLERS:
        return None
    prices = sorted(best.values())
    mid = k // 2
    median = prices[mid] if k % 2 else (prices[mid - 1] + prices[mid]) // 2
    if median <= 0 or (median - floor) * 100 < CAUTION_PERCENT * median:
        return None
    pct = (median - floor) * 100 // median
    sentence = ("This price is {}% below the median of the {} verified offers ({}). Verified means the merchant's own "
                "storefront states this price and stock for this exact item \u2014 it is not a rating of the merchant."
                ).format(pct, k, _money(median))
    return {"code": "price_outlier", "sentence": sentence, "median_cents": median, "eligible_sellers": k,
            "percent_below": pct}


def _screen_p(row):
    p = row.get("screen_p")
    return float(p) if type(p) in (int, float) and 0.0 <= p <= 1.0 else None


def _basis_label(goal):
    mode = goal["fulfillment"]
    if goal["basis"] == "delivered":
        return "Delivered total to {} · USD · shipping, tax & fees included".format(mode[5:])
    where = ""
    if mode.startswith("ship:"):
        where = " · ships to " + mode[5:]
    elif mode.startswith("pickup:"):
        where = " · pickup at " + mode[7:]
    return "Item price · USD · before shipping & tax" + where


def _price_sentence(row):
    if row.get("currency") not in (None, "USD"):
        return "Not priced in USD"
    return SENTENCES["price_unverified"]


def _fragment(reason, row):
    if reason == "fulfillment_mismatch":
        f = str(row.get("fulfillment") or "")
        return "pickup only" if f.startswith("pickup:") else "does not offer the requested delivery"
    return SENTENCES.get(reason, reason).lower() if reason != "condition_unverified_or_used" else \
        "used or condition not confirmed as new"


def _start(fn, jobs, workers):
    """Submit fn(job) for every job right away; returns (executor, futures) or (None, {})."""
    if not jobs:
        return None, {}
    ex = ThreadPoolExecutor(max_workers=max(1, min(workers, len(jobs))))
    return ex, {ex.submit(fn, job): i for i, job in enumerate(jobs)}


def _collect(ex, futures, until):
    """Wait until the monotonic time `until`; return {index: ("ok", v) | ("error", name) | ("timeout", None)}."""
    out = {}
    if ex is None:
        return out
    done, _ = wait(futures, timeout=max(0.0, until - time.monotonic()))
    for fut, i in futures.items():
        if fut in done:
            exc = fut.exception()
            out[i] = ("error", type(exc).__name__) if exc else ("ok", fut.result())
        else:
            out[i] = ("timeout", None)
    ex.shutdown(wait=False, cancel_futures=True)   # a slow lane never blocks the result
    return out


def _default_confirm():
    try:
        from app.merchants.chains import confirm
        return confirm
    except Exception:  # lane not ready / import failure: surfaced as not_connected coverage
        return None


def _default_pair():
    try:
        from app.models import pair
        return pair
    except Exception:
        return None


# ---------------------------------------------------------------- main entry

def run_comparison(target, goal, rows, *, deadline_s=20.0, as_of=None, confirm=None, pair=None,
                   catalog_ms=None):
    started = time.monotonic()
    deadline = started + deadline_s
    clock_given = as_of is not None
    as_of = int(time.time()) if as_of is None else as_of
    confirm = confirm if confirm is not None else _default_confirm()
    pair = pair if pair is not None else _default_pair()
    upid = target.get("upid")
    brand = (target.get("brand") or "").strip()
    mpn = (target.get("mpn") or "").strip()
    given_gtin = target.get("gtin") if _valid_gtin(target.get("gtin")) else None

    rows = [dict(r) for r in rows]
    cluster, candidates, leads = [], [], []          # leads: (row, reason)
    passthrough = []                                  # non-shop_native rows handed in: ranked as-is
    for r in rows:
        if r.get("source") != "shop_native":
            passthrough.append(r)
        elif upid and r.get("product") == upid:
            r["identity_basis"] = "catalog_product"
            cluster.append(r)
        elif title_carries(r.get("title"), brand, mpn):
            r.pop("identity_basis", None)
            candidates.append(r)
        else:
            r.pop("identity_basis", None)
            leads.append((r, "Not the bound catalog product and the listing title does not carry the brand and exact model number"))

    # ---- b + c. merchant confirms and two-model listing checks start together
    to_check = cluster + candidates
    by_seller = {}
    for r in to_check:
        by_seller.setdefault(r.get("seller"), []).append(r)
    order = sorted(by_seller, key=lambda s: min((x["item_cents"] if amount(x.get("item_cents")) else 10**12)
                                                for x in by_seller[s]))
    chosen = order[:MAX_CONFIRM_MERCHANTS]
    skipped_sellers = order[MAX_CONFIRM_MERCHANTS:]
    jobs = [r for s in chosen for r in by_seller[s]] if confirm else []
    cluster_ids = {id(r) for r in cluster}

    # listing checks only where they can change the answer: candidates cheaper than the cheapest eligible
    # catalog-cluster offer (or any, when the cluster has none), and not screened out by Decision 1
    cluster_floor = evaluate({"upid": upid, "gtin": given_gtin}, goal, cluster, as_of)["price_cents"]
    skip_reason = {}
    pair_pool = []
    for r in sorted(candidates, key=lambda r: r["item_cents"] if amount(r.get("item_cents")) else 10**12):
        sp = _screen_p(r)
        if sp is not None and sp <= SCREEN_DIFFERENT_MAX:
            skip_reason[id(r)] = "Decision 1 screen: different item (p {:.2f})".format(sp)
        elif cluster_floor is not None and not (amount(r.get("item_cents")) and r.get("currency") == "USD"
                                                and r["item_cents"] < cluster_floor):
            skip_reason[id(r)] = "Not checked: not cheaper than the verified catalog offer"
        elif sp is not None and sp < SCREEN_PAIR_MIN:
            skip_reason[id(r)] = "Not checked: Decision 1 screen below 0.5 (p {:.2f})".format(sp)
        else:
            pair_pool.append(r)
    pair_jobs = pair_pool[:MAX_PAIR_CALLS] if pair else []
    pair_over = pair_pool[MAX_PAIR_CALLS:] if pair else []
    target_summary = {"name": target.get("name") or "", "brand": brand, "mpn": mpn,
                      "variant_details": list(target.get("variant_details") or []), "gtin": given_gtin}

    def listing_fields(r):
        return {k: r.get(k) for k in ("title", "seller_name", "seller_domain", "sku", "handle", "url") if r.get(k)}

    t_phase = time.monotonic()
    c_ex, c_fut = _start(lambda r: confirm(r, as_of), jobs, CONFIRM_WORKERS)
    p_ex, p_fut = _start(lambda r: pair(target_summary, listing_fields(r)), pair_jobs, PAIR_WORKERS)
    outcomes = _collect(c_ex, c_fut, min(t_phase + CONFIRM_PHASE_S, deadline))
    confirm_ms = int((time.monotonic() - t_phase) * 1000)
    pair_out = _collect(p_ex, p_fut, min(t_phase + PAIR_PHASE_S, deadline))
    pair_ms = int((time.monotonic() - t_phase) * 1000)

    receipts = []
    confirmed_by = {}            # (seller, listing) -> ["merchant_api"]
    mismatched = set()
    merchant_quotes = []
    per_merchant = {}            # seller -> status
    published = {}               # cluster (seller, listing) -> normalized barcode
    for i, r in enumerate(jobs):
        kind, value = outcomes[i]
        seller = r.get("seller")
        if kind != "ok" or not isinstance(value, dict):
            status = "timeout" if kind == "timeout" else "failed"
            per_merchant.setdefault(seller, status)
            receipts.append({"source": "merchant_storefront", "seller": seller, "listing": r.get("listing"),
                             "status": status, "error": value if kind == "error" else None})
            continue
        status = value.get("status")
        if status not in ("confirmed", "mismatch", "unavailable", "not_permitted"):
            status = "failed"
        prev = per_merchant.get(seller)
        if prev not in ("confirmed", "mismatch"):
            per_merchant[seller] = status
        receipt = dict(value.get("receipt") or {})
        receipt.update({"seller": seller, "listing": r.get("listing"), "status": status, "note": _clip(value.get("note"))})
        receipts.append(receipt)
        q = value.get("quote")
        if status in ("confirmed", "mismatch") and isinstance(q, dict) and _key(q) == _key(r):
            q = dict(q)
            q["source"] = q.get("source") or "merchant_api"
            q.setdefault("product", r.get("product"))
            for k in ("seller_name", "seller_domain", "title", "image_url", "screen_p"):
                if not q.get(k) and r.get(k):
                    q[k] = r[k]
            if "identity_basis" in r:
                q["identity_basis"] = r["identity_basis"]
            if _valid_gtin(q.get("gtin")):
                if id(r) in cluster_ids:
                    published[_key(r)] = normalize_gtin(q["gtin"])
                if status == "confirmed":
                    r["gtin"] = q["gtin"]       # the merchant-published barcode becomes the row's barcode
            merchant_quotes.append(q)
            if status == "confirmed":
                confirmed_by[_key(r)] = ["merchant_api"]
            else:
                mismatched.add(_key(r))
        elif status == "mismatch":
            mismatched.add(_key(r))
    for s in (skipped_sellers if confirm else []):
        per_merchant[s] = "not_checked"

    # barcode agreement inside the cluster decides the run's GTIN
    codes = set(published.values())
    barcode_conflict = len(codes) > 1
    run_gtin, gtin_source = given_gtin, ("target" if given_gtin else None)
    if len(codes) == 1:
        agreed = next(iter(codes))
        if given_gtin is None:
            run_gtin, gtin_source = agreed, "cluster_merchant_barcode"
        elif not same_gtin(agreed, given_gtin):
            barcode_conflict = True
    elif barcode_conflict and given_gtin is None:
        # independent sellers per barcode: a strict majority of >= 2 sellers sets the run GTIN; the minority rows
        # then fail the identity predicate on their own (valid barcode differing from a known target GTIN)
        sellers_per = {}
        for (seller, _listing), code in published.items():
            sellers_per.setdefault(code, set()).add(seller)
        total = len({seller for seller, _ in published})
        top = max(sellers_per, key=lambda c: len(sellers_per[c]))
        n_top = len(sellers_per[top])
        if n_top >= 2 and n_top * 2 > total and sum(1 for c in sellers_per if len(sellers_per[c]) == n_top) == 1:
            run_gtin, gtin_source = top, "cluster_merchant_barcode_majority"
    if barcode_conflict and given_gtin is None and run_gtin is None:
        # the cluster mixes barcodes and nothing says which is the target: barcode rows lose identity
        for r in cluster:
            if _key(r) in published:
                r["identity_basis"] = None
        for q in merchant_quotes:
            if _key(q) in published:
                q["identity_basis"] = None

    # model-number candidates: an agreeing published barcode decides first, else the listing check verdict
    quote_by_key = {_key(q): q for q in merchant_quotes}
    pair_index = {id(r): i for i, r in enumerate(pair_jobs)}
    over_ids = {id(r) for r in pair_over}
    ranked_candidates = []
    pair_results = []
    for r in candidates:
        q = quote_by_key.get(_key(r))
        code = q.get("gtin") if q and _valid_gtin(q.get("gtin")) else (r.get("gtin") if _valid_gtin(r.get("gtin")) else None)
        i = pair_index.get(id(r))
        kind, value = pair_out.get(i, (None, None)) if i is not None else (None, None)
        verdict = value.get("verdict") if kind == "ok" and isinstance(value, dict) else None
        if i is not None:
            if kind == "ok" and isinstance(value, dict):
                for rec in value.get("receipts") or []:
                    receipts.append(dict(rec, seller=r.get("seller"), listing=r.get("listing")))
            else:
                receipts.append({"job": "pair", "seller": r.get("seller"), "listing": r.get("listing"),
                                 "status": kind, "error": value if kind == "error" else None})
            pair_results.append({"seller": r.get("seller"), "listing": r.get("listing"),
                                 "verdict": verdict or kind, "error": value if kind == "error" else None})
        if code and run_gtin:
            if same_gtin(code, run_gtin):
                r["gtin"] = code
                ranked_candidates.append(r)      # merchant_barcode tier inside evaluate
            else:
                leads.append((r, "Published barcode differs from the target"))
        elif i is not None and verdict == "match":
            r["identity_basis"] = "model_number"
            if q:
                q["identity_basis"] = "model_number"
            ranked_candidates.append(r)
        elif i is not None:
            why = {"different": "Listing check found a different item",
                   "abstain": "Listing check could not confirm the same item",
                   "timeout": PAIR_LATE}.get(verdict or kind, "Listing check failed")
            leads.append((r, why))
        elif id(r) in skip_reason:
            leads.append((r, skip_reason[id(r)]))
        elif id(r) in over_ids:
            leads.append((r, "Listing check limit reached"))
        else:
            leads.append((r, "Listing check not available"))
    lead_keys = {_key(r) for r, _ in leads}

    # ---- d. rank
    ranked = cluster + ranked_candidates + [q for q in merchant_quotes if _key(q) not in lead_keys] + passthrough
    eff_target = {"upid": upid, "gtin": run_gtin}
    if not clock_given:
        as_of = max(as_of, int(time.time()))    # storefront reads happen during the fan-out: judge them after it
    ev = evaluate(eff_target, goal, ranked, as_of)
    # a merchant 'mismatch' without a differing field still must never let the seller win
    if mismatched:
        keep = []
        for e in ev["eligible"]:
            if (e["seller"], e["listing"]) in mismatched:
                ev["excluded"].append({"seller": e["seller"], "listing": e["listing"], "reason": "conflicting_sources",
                                       "reasons": ["conflicting_sources"]})
            else:
                keep.append(e)
        ev["eligible"] = keep
        floor = keep[0]["cents"] if keep else None
        ev["winners"] = sorted({e["seller"] for e in keep if e["cents"] == floor})
        ev["price_cents"] = floor
        ev["status"] = "winner" if ev["winners"] else "abstain"

    newest = {}
    for r in ranked:
        k = _key(r)
        if k not in newest or (type(r.get("read_at")) is int and r["read_at"] > (newest[k].get("read_at") or -1)):
            newest[k] = r

    def offer_for(seller, listing, verified=False, basis=None):
        row = newest.get((seller, listing)) or {"seller": seller, "listing": listing}
        return _offer(row, verified=verified, confirmed_by=confirmed_by.get((seller, listing), ()),
                      identity_basis=basis if basis is not None else row.get("identity_basis"))

    floor = ev["price_cents"]
    eligible_sellers = {e["seller"] for e in ev["eligible"]}
    winners = [offer_for(e["seller"], e["listing"], True, e["identity_basis"]) for e in ev["eligible"]
               if e["cents"] == floor]
    for w, e in zip(winners, [e for e in ev["eligible"] if e["cents"] == floor]):
        w["total_cents"] = e["cents"]
    if not eligible_sellers:
        status, headline = "no_verified_offer", HEADLINE_NONE
    elif len(eligible_sellers) == 1:
        status, headline = "single_offer", HEADLINE_ONE
    else:
        status = "tie" if len({w["seller"] for w in winners}) > 1 else "winner"
        headline = HEADLINE_MANY

    rejected, seen = [], set()
    eligible_keys = {(e["seller"], e["listing"]) for e in ev["eligible"]}
    for x in ev["excluded"]:
        k = (x["seller"], x["listing"], x["reason"])
        if k in seen or k[:2] in eligible_keys:   # an older duplicate of an eligible listing is not a rejection
            continue
        seen.add(k)
        row = newest.get((x["seller"], x["listing"])) or next(
            (r for r in ranked if _key(r) == (x["seller"], x["listing"])), {"seller": x["seller"], "listing": x["listing"]})
        sentence = _price_sentence(row) if x["reason"] == "price_unverified" else SENTENCES.get(x["reason"], x["reason"])
        rejected.append({"offer": _offer(row, confirmed_by=confirmed_by.get(_key(row), ())), "reason": x["reason"],
                         "reasons": x.get("reasons", [x["reason"]]), "sentence": sentence, "_row": row})

    near_miss = None
    if floor is not None:
        pool = []
        for rej in rejected:
            row = rej["_row"]
            reasons = rej["reasons"]
            if any(r not in NEAR_MISS_REASONS for r in reasons):
                continue        # different item, disputed, stale or unpriced: no honest comparison
            if row.get("currency") != "USD" or not amount(row.get("item_cents")):
                continue
            cost = row["item_cents"]
            if goal["basis"] == "delivered":
                extras = [row.get(k) for k in ("shipping_cents", "tax_cents", "fees_cents")]
                if not all(amount(v) for v in extras):
                    continue
                cost += sum(extras)
            pool.append((cost - floor, rej))
        below = sorted([p for p in pool if p[0] < 0], key=lambda p: p[0])
        above = sorted([p for p in pool if p[0] >= 0], key=lambda p: p[0])
        pick = below[0] if below else (above[0] if above else None)
        if pick:
            delta, rej = pick
            frag = _fragment(rej["reason"], rej["_row"])
            if delta < 0:
                sentence = "{} less, but {}".format(_money(delta), frag)
            elif delta == 0:
                sentence = "Same price, but {}".format(frag)
            else:
                sentence = "{} more, and {}".format(_money(delta), frag)
            near_miss = {"offer": rej["offer"], "reason": rej["reason"], "sentence": sentence, "delta_cents": delta}
    for rej in rejected:
        rej.pop("_row", None)

    # runner-up: the cheapest eligible offer from a seller that is not among the winners
    winner_sellers = {w["seller"] for w in winners}
    runner_up = None
    for e in ev["eligible"]:                       # already sorted by (cents, seller, listing)
        if e["seller"] not in winner_sellers:
            runner_up = offer_for(e["seller"], e["listing"], True, e["identity_basis"])
            runner_up["total_cents"] = e["cents"]
            break

    result = {
        "status": status, "headline": headline, "price_cents": floor, "currency": "USD" if floor is not None else None,
        "basis_label": _basis_label(goal), "winners": winners, "runner_up": runner_up,
        "caution": price_caution(ev["eligible"], floor),
        "near_miss": near_miss, "rejected": rejected,
        "leads": sorted((dict(_offer(r), lead_reason=why) for r, why in leads),   # cheapest first, unpriced last
                        key=lambda o: (o["item_cents"] is None or o["currency"] != "USD", o["item_cents"] or 0,
                                       o["seller_name"] or "")),
        "item_price_view": None,
    }
    if goal["basis"] == "delivered" and floor is None:
        item_goal = {"basis": "item", "fulfillment": goal["fulfillment"]}
        iv = evaluate(eff_target, item_goal, ranked, as_of)
        iv_el = [e for e in iv["eligible"] if (e["seller"], e["listing"]) not in mismatched]
        if iv_el:
            f = iv_el[0]["cents"]
            result["item_price_view"] = {
                "heading": "Verified item prices (delivered total could not be compared)",
                "basis_label": _basis_label(item_goal), "price_cents": f, "currency": "USD",
                "winners": [offer_for(e["seller"], e["listing"], True, e["identity_basis"]) for e in iv_el if e["cents"] == f],
                "eligible": [offer_for(e["seller"], e["listing"], True, e["identity_basis"]) for e in iv_el],
            }

    # ---- e. coverage
    shop_rows = [r for r in rows if r.get("source") == "shop_native"]
    shop_sellers = {r.get("seller") for r in shop_rows if r.get("seller")}
    counts = {}
    for s, st in per_merchant.items():
        counts[st] = counts.get(st, 0) + 1
    checked = [s for s, st in per_merchant.items() if st != "not_checked"]
    responded_m = [s for s in checked if per_merchant[s] in ("confirmed", "mismatch")]
    if confirm is None:
        m_status, m_note = "not_connected", "Merchant storefront checks are not connected"
    elif not checked:
        m_status, m_note = "ok", "No catalog seller to confirm"
    elif len(responded_m) == len(checked):
        m_status, m_note = "ok", ""
    elif not responded_m:
        m_status = "timeout" if counts.get("timeout") else "failed"
        m_note = "No merchant storefront answered"
    else:
        m_status = "limited"
        m_note = ""
    if confirm is not None and checked:
        parts = ["{} of {} merchants confirmed".format(counts.get("confirmed", 0), len(checked))]
        for st, label in (("mismatch", "disagreed"), ("unavailable", "unavailable"), ("not_permitted", "not permitted"),
                          ("timeout", "timed out"), ("failed", "failed")):
            if counts.get(st):
                parts.append("{} {}".format(counts[st], label))
        if skipped_sellers:
            parts.append("{} not checked (limit)".format(len(skipped_sellers)))
        m_note = "; ".join(parts)
    late_pairs = sum(1 for x in pair_results if x["verdict"] == "timeout")
    failed_pairs = sum(1 for x in pair_results if x["verdict"] == "error")
    granted = sum(1 for r in ranked_candidates if r.get("identity_basis") == "model_number")
    if not pair:
        l_status, l_note = "not_connected", "Listing check not available"
    elif not pair_jobs:
        l_status = "ok"
        l_note = ("{} candidates not checked: none cheaper than the verified catalog offer or screened out".format(len(skip_reason))
                  if skip_reason else "No model-number candidates to check")
    else:
        answered = len(pair_jobs) - late_pairs - failed_pairs
        l_status = "ok" if answered == len(pair_jobs) and not pair_over else ("failed" if answered == 0 and not late_pairs else "limited")
        parts = ["{} checked, {} matched".format(len(pair_jobs), granted)]
        if late_pairs:
            parts.append("{} did not finish in time".format(late_pairs))
        if failed_pairs:
            parts.append("{} failed".format(failed_pairs))
        if pair_over:
            parts.append("{} not checked (limit)".format(len(pair_over)))
        l_note = "; ".join(parts)
    if m_status == "ok" and (counts.get("timeout") or skipped_sellers) and confirm is not None:
        m_status = "limited"
    sources = [
        {"id": "shop_catalog", "name": "Shop catalog (shop.dev)", "kind": "catalog",
         "status": "ok" if shop_rows else "limited", "offers": len(shop_rows),
         "eligible": len({e["seller"] for e in ev["eligible"]
                          if any(_key(r) == (e["seller"], e["listing"]) for r in shop_rows)}),
         "ms": catalog_ms, "note": "" if shop_rows else "No catalog rows for this item"},
        {"id": "merchant_storefronts", "name": "Merchant storefronts", "kind": "merchant", "status": m_status,
         "offers": len(merchant_quotes), "eligible": len({e["seller"] for e in ev["eligible"] if e["seller"] in responded_m}),
         "ms": confirm_ms if jobs else None, "note": m_note,
         "merchants": {"attempted": len(checked), "responded": len(responded_m), **counts}},
        {"id": "listing_checks", "name": "Listing check (Gemini 4 Carbon, verified quotes)", "kind": "model",
         "status": l_status, "offers": len(pair_jobs), "eligible": granted,
         "ms": pair_ms if pair_jobs else None, "note": l_note},
    ]
    attempted = 1 + (1 if jobs else 0)
    responded = (1 if shop_rows else 0) + (1 if responded_m else 0)
    coverage = {"attempted": attempted, "responded": responded,
                "sellers_seen": len({r.get("seller") for r in rows if r.get("seller")} | shop_sellers),
                "sellers_eligible": len(eligible_sellers), "checked_at": as_of, "sources": sources}

    evaluation = dict(ev)
    evaluation.update({
        "target_gtin": run_gtin, "gtin_source": gtin_source, "barcode_conflict": barcode_conflict,
        "published_barcodes": sorted(codes), "identity_counts": {
            "catalog_product": len(cluster), "model_number_candidates": len(candidates),
            "model_number_granted": sum(1 for r in ranked_candidates if r.get("identity_basis") == "model_number"),
            "leads": len(leads)},
        "pair_checks": pair_results, "pair_ms": pair_ms if pair_jobs else None,
        "confirm_ms": confirm_ms if jobs else None,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    })
    return {"result": result, "coverage": coverage, "receipts": receipts, "evaluation": evaluation}


# ---------------------------------------------------------------- CLI

def _cli(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m app.merchants.compare '<target json>' ['<goal json>']")
        return 2
    target = json.loads(argv[0])
    goal = json.loads(argv[1]) if len(argv) > 1 else {"basis": "item", "fulfillment": "online_us"}
    from app.merchants import shop
    visual = {"name": target.get("name") or "", "brand": target.get("brand") or "",
              "part_number": target.get("mpn") or "", "barcode": target.get("gtin") or "",
              "variant_details": target.get("variant_details") or [],
              "query": " ".join(x for x in (target.get("brand"), target.get("mpn"), target.get("name")) if x)}
    t_shop = time.monotonic()
    found = shop.anchors_for(visual, None)
    catalog_ms = int((time.monotonic() - t_shop) * 1000)
    anchors = found.get("anchors") or []
    if not target.get("upid"):
        pick = next((a for a in anchors if title_carries(a.get("title"), target.get("brand"), target.get("mpn"))), None)
        if pick:
            target = dict(target, upid=pick["upid"], gtin=target.get("gtin") or pick.get("gtin"))
    print("anchors:", len(anchors), "| rows:", len(found.get("rows") or []), "| bound upid:", target.get("upid"))
    out = run_comparison({**{"upid": None, "gtin": None, "brand": "", "mpn": "", "name": "", "variant_details": []},
                          **target}, goal, found.get("rows") or [], catalog_ms=catalog_ms)
    r = out["result"]
    print(json.dumps({"result": {k: r[k] for k in ("status", "headline", "price_cents", "currency", "basis_label")},
                      "winners": [(w["seller_name"], w["item_cents"], w["identity_basis"], w["confirmed_by"], w["url"])
                                  for w in r["winners"]],
                      "near_miss": r["near_miss"] and {"seller": r["near_miss"]["offer"]["seller_name"],
                                                       "sentence": r["near_miss"]["sentence"]},
                      "runner_up": r["runner_up"] and (r["runner_up"]["seller_name"], r["runner_up"]["item_cents"]),
                      "caution": r["caution"],
                      "rejected": [(x["offer"]["seller_name"], x["offer"]["item_cents"], x["sentence"]) for x in r["rejected"]],
                      "leads": [(x["seller_name"], x["title"][:60], x["lead_reason"]) for x in r["leads"]][:10],
                      "coverage": out["coverage"],
                      "target_gtin": out["evaluation"]["target_gtin"],
                      "timing_ms": {k: out["evaluation"].get(k) for k in ("confirm_ms", "pair_ms", "elapsed_ms")},
                      "identity_counts": out["evaluation"]["identity_counts"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
