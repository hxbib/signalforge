"""Final cross-provider ranking after evidence normalization, evaluated on demand."""
from collections import defaultdict
from dataclasses import dataclass
from compare_offers import same_gtin


@dataclass(frozen=True)
class Quote:
    seller: str        # canonical retailer/marketplace seller id from a checked identity mapping
    listing: str       # seller's stable listing/variant id
    gtin: str
    cents: int
    currency: str
    condition: str
    stock: str         # in_stock, orderable, sold_out or unknown
    fulfillment: str   # e.g. ship:10001 or pickup:merchant/location
    source: str        # merchant_api, merchant_page, shop_native or shopping_index
    read_at: int
    url: str


def rank(barcode,quotes,fulfillment,now):
    groups=defaultdict(list); exclusions=[]
    for q in quotes:
        if q.source not in ("merchant_api","merchant_page","shop_native"):
            exclusions.append((q.seller,"unconfirmed source"));continue
        if not same_gtin(q.gtin,barcode) or q.fulfillment!=fulfillment or q.condition!="new":
            exclusions.append((q.seller,"different or unknown item, condition or fulfillment"));continue
        if type(q.read_at) is not int or not 0<=now-q.read_at<=120:
            exclusions.append((q.seller,"stale or invalid timestamp"));continue
        if not q.seller or not q.listing or not q.url.startswith("https://"):
            exclusions.append((q.seller,"missing seller, listing or link"));continue
        groups[q.seller,q.listing].append(q)
    accepted=[]
    for (seller,_),rows in groups.items():
        values={(q.stock,q.cents,q.currency) for q in rows}
        if len(values)!=1:
            exclusions.append((seller,"conflicting live sources"));continue
        q=max(rows,key=lambda o:o.read_at)
        if q.stock!="in_stock" or q.currency!="USD" or type(q.cents) is not int or q.cents<0:
            exclusions.append((seller,"no verified in-stock USD item price"));continue
        accepted.append(q)
    accepted.sort(key=lambda q:(q.cents,q.seller,q.listing))
    floor=accepted[0].cents if accepted else None
    return floor,[q for q in accepted if q.cents==floor],accepted,exclusions
