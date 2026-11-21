"""Extract candidates from already-obtained merchant HTML. The caller owns the source's origin, access and timestamp."""
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
from urllib.parse import urljoin,urlsplit
from compare_offers import same_gtin


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.collect=False;self.documents=[];self.chunk=[]
    def handle_starttag(self,tag,attrs):
        if tag=="script":
            self.collect=dict(attrs).get("type","").lower()=="application/ld+json";self.chunk=[]
    def handle_data(self,data):
        if self.collect:self.chunk.append(data)
    def handle_endtag(self,tag):
        if tag=="script" and self.collect:
            self.documents.append("".join(self.chunk));self.collect=False


def many(v):return v if isinstance(v,list) else [v]


def products(value):
    if isinstance(value,list):
        for row in value:yield from products(row)
    elif isinstance(value,dict):
        if "Product" in many(value.get("@type")):yield value
        for field in ("@graph","hasVariant","mainEntity"):
            if field in value:yield from products(value[field])


def extract(html,page_url,barcode):
    parser=Scripts();parser.feed(html)
    origin=urlsplit(page_url);rows=[]
    if origin.scheme!="https" or not origin.hostname:raise ValueError("merchant origin required")
    for script in parser.documents:
        try:doc=json.loads(script,parse_float=Decimal)
        except (TypeError,ValueError):continue
        for p in products(doc):
            identifiers=[p[k] for k in ("gtin8","gtin12","gtin13","gtin14","gtin") if k in p]
            if not identifiers or not all(same_gtin(str(i),barcode) for i in identifiers):continue
            for offer in many(p.get("offers")):
                if not isinstance(offer,dict) or offer.get("@type")!="Offer":continue
                seller=offer.get("seller") or {};vendor=seller.get("name") if isinstance(seller,dict) else None
                url=urljoin(page_url,offer.get("url") or page_url)
                if urlsplit(url).scheme!="https" or urlsplit(url).hostname!=origin.hostname:continue
                try:amount=Decimal(str(offer.get("price")))*100
                except InvalidOperation:continue
                if not amount.is_finite() or amount<0 or amount!=amount.to_integral_value():continue
                if offer.get("priceCurrency")!="USD":continue
                def term(name):return str(offer.get(name,"")).removeprefix("https://schema.org/").removeprefix("http://schema.org/")
                rows.append({"source":"merchant structured markup","url":url,"seller":vendor,"item_price_cents":int(amount),
                             "in_stock":term("availability")=="InStock","new":term("itemCondition")=="NewCondition",
                             "gtin":barcode,"merchant_context_required":True})
    return rows
