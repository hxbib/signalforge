"""Venue smoke for a real photo → Decision 1 + Neon → Carbon → exact, shopper-triggered Shop lookup."""
import argparse
import json
import os
from pathlib import Path
from typing import Literal
from gemini_io import Shape, CARBON, clients, structured, parts
from deal_models import inspect_image, bind_target
from compare_offers import compare_now, gtin
from shop import search
from live_smoke import clean_image

class Intent(Shape):
    operation: Literal["compare","compare_and_watch"]
    market: Literal["US","unsupported"]
    basis: Literal["item","delivered"]
    fulfillment: Literal["online_us","ship","pickup"]
    location: str
    watch_words: str
    unsupported: list[str]

INTENT = """Read the user's request. With an empty note, compare the new pictured item, USD sticker
price, ordinary online US availability. Item price excludes shipping/tax. Delivered or all-in
means item plus shipping, tax and fees. Never invent a ZIP or a store. Capture explicit watch
language verbatim; otherwise choose compare only. Report any requested condition you cannot
represent, including used/refurbished, deadlines, memberships, bundles of alternate items,
market, currency, coupon, pickup radius or cap applying to the immediate lookup. The UI can
then offer an explicitly scoped partial answer. No checkout or contact actions."""

def parse_goal(image, note):
    stable,_ = clients()
    parsed,trace = structured(stable,CARBON,INTENT,Intent,parts(image,"image/jpeg",note))
    if parsed.operation=="compare_and_watch" and (not parsed.watch_words.strip() or parsed.watch_words not in note):
        raise ValueError("watch authorization lacks a verbatim user request")
    return parsed,trace

def run(path,note):
    from concurrent.futures import ThreadPoolExecutor
    with path.open("rb") as f:
        image=clean_image(f.read(5_000_001))
    with ThreadPoolExecutor(max_workers=2) as pool:
        intent_job=pool.submit(parse_goal,image,note)
        visual,decision,vision_trace=inspect_image(image,note)
        intent,intent_trace=intent_job.result()
    from visual_recovery import second_look
    image,visual,recovered,retry = second_look(image,note,visual,decision,inspect_image)
    out={"intent":intent.model_dump(),"models":[vision_trace,decision,intent_trace],"automatic_recovery":retry}
    decision=recovered
    feed_path=os.environ.get("SIGNALFORGE_MERCHANT_EXPORT")
    if feed_path:
        from merchant_feed import compare_export
        try:
            result=compare_export(image,note,visual,decision,intent,feed_path)
        except (OSError,ValueError,KeyError,TypeError) as err:
            out["merchant_feed_error"]=type(err).__name__
        else:
            if result is not None:
                return {**out,**result}
    try:
        gtin(visual.barcode)
    except ValueError:
        doc,receipt=search(visual.query,image) if visual.query else ({},{})
        out.update(status="needs_identity_evidence",catalog_candidates=len(doc.get("products",[])),receipt=receipt,
                   next_step="Resolve the printed manufacturer model and variant in a permitted identity feed; a crop is the last resort.")
        return out
    observed=compare_now(visual.barcode)
    if not observed["offers"]:
        return {**out,"status":"no_verified_offer","scope_results":observed["scope_results"],
                "excluded":observed["excluded"],"warnings":observed["warnings"]}
    # An eligible catalog row already proves an exact match against the input GTIN.
    anchors=[{"gtin":visual.barcode,"identity_source":"exact Shop variant input"}]
    target,question,trace=bind_target(image,note,anchors,visual,decision)
    out["models"].append(trace)
    if target is None:
        return {**out,"status":"clarify","question":question}
    if intent.market!="US" or intent.unsupported or intent.fulfillment!="online_us" or intent.basis!="item":
        return {**out,"status":"additional_merchant_evidence_needed","item_gtin":target,
                "known_basis":"US catalog item price only","verified_stores":observed["seller_count_eligible"]}
    return {**out,"status":"winner","item_gtin":target,"shop":observed}

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image",type=Path)
    parser.add_argument("--note",default="")
    args=parser.parse_args()
    try:
        print(json.dumps(run(args.image,args.note),indent=2))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        parser.exit(1,"Comparison unavailable: "+type(exc).__name__+"\n")

