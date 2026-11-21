"""On-demand join with one team-configured, rights-cleared merchant export. Never accepts a path from a shopper."""
import json
from pathlib import Path
from urllib.error import URLError
from buyer import evaluate
from compare_offers import compare_now, same_gtin
from deal_models import bind_target
import time

def load_export(path):
    with Path(path).open("rb") as f:
        data=f.read(2_000_001)
    if len(data)>2_000_000:
        raise ValueError("merchant export too large")
    doc=json.loads(data)
    if not doc.get("feed_id") or not isinstance(doc.get("anchors"),list) or not isinstance(doc.get("offers"),list):
        raise ValueError("merchant export shape invalid")
    return doc

def select_anchors(visual, anchors):
    if visual.barcode:
        return [a for a in anchors if same_gtin(a.get("gtin"),visual.barcode)]
    def norm(value):
        return str(value or "").casefold().strip()
    if not norm(visual.brand) or not norm(visual.part_number):
        return []
    return [a for a in anchors if norm(a.get("brand"))==norm(visual.brand)
            and norm(a.get("mpn"))==norm(visual.part_number)]

def compare_export(image,note,visual,decision,intent,path):
    feed=load_export(path)
    anchors=select_anchors(visual,feed["anchors"])
    if not anchors:
        return None
    target, question, trace=bind_target(image,note,anchors,visual,decision)
    if not target:
        return {"status":"clarify","question":question,"binding_trace":trace}
    fulfillment="online_us" if intent.fulfillment=="online_us" else intent.fulfillment+":"+intent.location
    if intent.market!="US" or intent.unsupported or (intent.fulfillment!="online_us" and not intent.location):
        return {"status":"additional_merchant_evidence_needed","item_gtin":target,"binding_trace":trace}
    # Export producer assigns product scope; related rows can include deliberately nonmatching variants.
    listings=[r for r in feed["offers"] if same_gtin(r.get("target_gtin",r.get("gtin")),target)]
    notes=[{"source":feed["feed_id"],"state":"returned","rows":len(listings)}]
    try:
        shop=compare_now(target)
    except (ValueError,OSError,KeyError,TypeError,URLError) as err:
        notes.append({"source":"shop_native","state":"unavailable","error":type(err).__name__})
    else:
        notes.extend(shop["scope_results"])
        times=[r.get("effective_at",r["read_at"]) for r in shop["read_receipts"]]
        aliases=feed.get("shop_seller_aliases",{})
        listings.extend({"gtin":target,"seller":aliases.get(o["seller"],o["seller"]),"listing":o["variant"],
                         "url":o["url"],"source":"shop_native","currency":"USD","item_cents":o["cents"],
                         "condition":"new","stock":"in_stock","unconditional":True,"fulfillment":"online_us",
                         "read_at":min(times)}
                        for o in shop["offers"] if times)
        notes.append({"source":"shop_native","excluded":shop["excluded"],"warnings":shop["warnings"]})
    answer=evaluate(target,{"basis":intent.basis,"fulfillment":fulfillment},listings,int(time.time()))
    return {**answer,"item_gtin":target,"binding_trace":trace,"scope_results":notes}

