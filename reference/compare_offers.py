"""Compare one exact trade item on a buyer's request. No polling, database or API key."""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
import sys
from urllib.parse import urlsplit
from urllib.error import URLError, HTTPError
import time

from shop import call


def gtin(value):
    if not isinstance(value,str) or len(value) not in (8,12,13,14) or not value.isascii() or not value.isdigit():
        raise ValueError("use a GTIN-8, UPC-A, EAN-13 or GTIN-14")
    if int(value)==0:
        raise ValueError("empty trade identity")
    digits=list(map(int,value))
    weighted=sum(n*(3 if i%2==0 else 1) for i,n in enumerate(reversed(digits[:-1])))
    if (weighted+digits[-1])%10:
        raise ValueError("barcode check digit failed")
    return value.zfill(14)


def same_gtin(a,b):
    try:
        return gtin(a)==gtin(b)
    except ValueError:
        return False


@dataclass(frozen=True)
class Offer:
    seller: str
    seller_name: str
    variant: str
    url: str
    cents: int | None
    decision: str


def inspect_variant(variant,barcode,warnings):
    seller=variant.get("seller") or {}
    vid=variant.get("id","")
    sid=seller.get("id","")
    link=variant.get("url","")
    price=variant.get("price") or {}
    amount=price.get("amount")
    availability=variant.get("availability") or {}
    required=variant.get("requires") or {}
    source=variant.get("source")
    if source=="external" or vid.startswith("gid://catalog/ExternalVariant/"):
        reason="external_stock_unconfirmed"
    elif source not in (None,"shopify") or not vid.startswith("gid://shopify/ProductVariant/") or not sid.startswith("gid://shopify/Shop/"):
        reason="unverified_source"
    elif not any(i.get("match")=="exact" and same_gtin(i.get("id"),barcode) for i in variant.get("inputs",[])):
        reason="identity_unconfirmed"
    elif warnings:
        reason="catalog_warning_review"
    elif variant.get("condition")!=["new"]:
        reason="condition_unconfirmed_or_used"
    elif required.get("components") is True or required.get("selling_plan") is True:
        reason="conditional_offer"
    elif availability.get("available") is False:
        reason="sold_out"
    elif availability.get("available") is not True or availability.get("status")!="in_stock":
        reason="stock_unconfirmed"
    elif price.get("currency")!="USD" or type(amount) is not int or amount<0:
        reason="price_unconfirmed"
    elif not link or urlsplit(link).scheme!="https" or not urlsplit(link).hostname:
        reason="merchant_link_missing"
    else:
        reason="eligible"
    return Offer(sid,seller.get("name") or seller.get("domain") or "Unknown merchant",vid,link,amount,reason)


def compare(doc,barcode):
    gtin(barcode)
    if not isinstance(doc.get("products"),list):
        raise ValueError("missing catalog products document")
    warnings=[m for m in doc.get("messages",[]) if m.get("type") in ("warning","error")]
    groups=defaultdict(list)
    seen_sellers=set()
    for product in doc["products"]:
        for v in product.get("variants",[]):
            offer=inspect_variant(v,barcode,warnings)
            groups[offer.seller,offer.variant].append(offer)
            if offer.seller:
                seen_sellers.add(offer.seller)
    offers=[]
    exclusions=[]
    for group in groups.values():
        first=group[0]
        if len({(o.decision,o.cents) for o in group})>1:
            exclusions.append((first.seller_name,"conflicting_duplicate"))
        elif first.decision=="eligible":
            offers.append(first)
        else:
            exclusions.append((first.seller_name,first.decision))
    offers.sort(key=lambda o:(o.cents,o.seller,o.variant))
    floor=offers[0].cents if offers else None
    winners=[o for o in offers if o.cents==floor]
    return {"price_cents":floor,"seller_count_returned":len(seen_sellers),
            "seller_count_eligible":len({o.seller for o in offers}),"offers":[o.__dict__ for o in offers],
            "winning_sellers":sorted({o.seller for o in winners}),"excluded":exclusions,"warnings":warnings,
            "basis":"USD item price; US shipping market; tax and shipping cost excluded"}


def compare_now(barcode, stores=()):
    gtin(barcode)
    roster=list(dict.fromkeys(s.strip().lower() for s in stores))
    if len(roster)>6 or any(not s or "/" in s or "." not in s for s in roster):
        raise ValueError("choose up to six merchant domains")
    filters={"condition":["new"],"ships_to":{"country":"US"},"available":False}
    merged={"products":[]}
    receipts=[]
    notes=[]
    attempted=[]
    for store in roster or [None]:
        args={"ids":[barcode],"filters":{**filters,**({"shops":[store]} if store else {})}}
        attempted.append(store or "global catalog")
        try:
            doc,receipt=call("lookup_catalog",args)
        except (ValueError,URLError,OSError) as exc:
            notes.append({"scope":store,"state":"unavailable","error":type(exc).__name__})
            if isinstance(exc,HTTPError) and exc.code==429:
                break
            continue
        receipts.append({**receipt,"scope":store})
        if any(m.get("type") in ("warning","error") for m in doc.get("messages",[])):
            notes.append({"scope":store,"state":"manual_review","messages":doc["messages"]})
            continue
        if not isinstance(doc.get("products"),list):
            notes.append({"scope":store,"state":"unknown_response"});continue
        rows=[]
        for product in doc["products"]:
            for variant in product.get("variants",[]):
                # Some providers relax filters. Never attribute another shop's row to the requested domain.
                if store and (variant.get("seller") or {}).get("domain","").lower()!=store:
                    continue
                rows.append(variant)
        merged["products"].append({"variants":rows})
        notes.append({"scope":store,"state":"returned" if rows else "no_exact_result_yet","variant_rows":len(rows)})
    if receipts and time.time()-min(r.get("effective_at",r["read_at"]) for r in receipts)>120:
        raise ValueError("comparison window expired; refresh")
    return {**compare(merged,barcode),"scheduled_stores":roster,"attempted_scopes":attempted,
            "scope_results":notes,"read_receipts":receipts}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("barcode")
    p.add_argument("--store",action="append",default=[],help="actual catalog-returned seller domain; repeat for comparison")
    args=p.parse_args()
    try:
        print(json.dumps(compare_now(args.barcode,args.store),indent=2))
    except (ValueError,TypeError,KeyError,AttributeError,URLError,OSError) as exc:
        sys.exit("Could not verify catalog comparison: "+type(exc).__name__+" (check input, provider access and schema).")
