"""On-demand US retailer discovery and Best Buy verification. Reads credentials from environment."""
import argparse
from decimal import Decimal
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from shop import NoRedirect
from compare_offers import gtin, same_gtin


def get_json(url,params):
    # This function is used only with the provider origin constants below. No merchant URL is fetched.
    req=Request(url+"?"+urlencode(params),headers={"Accept":"application/json"})
    with build_opener(NoRedirect).open(req,timeout=12) as resp:
        raw=resp.read(2_000_001)
    if len(raw)>2_000_000:raise ValueError("response too large")
    doc=json.loads(raw,parse_float=Decimal)
    if not isinstance(doc,dict) or doc.get("error"): raise ValueError("provider could not return a result")
    return doc


def serp(parameters):
    return get_json("https://serpapi.com/search.json",{"api_key":os.environ["SERPAPI_KEY"],"no_cache":"true",**parameters})


def discover(query):
    doc=serp({"engine":"google_shopping","q":query,"gl":"us","hl":"en"})
    return [{"title":r.get("title"),"token":r["immersive_product_page_token"]}
            for r in doc.get("shopping_results",[]) if r.get("immersive_product_page_token")][:8]


def sellers(chosen_token):
    doc=serp({"engine":"google_immersive_product","page_token":chosen_token,"more_stores":"true"})
    return [{"seller":r.get("name"),"listing":r.get("title"),"url":r.get("link"),"display_price":r.get("price"),
             "shipping":r.get("shipping"),"source":"third-party shopping index","status":"candidate"}
            for r in (doc.get("product_results") or {}).get("stores",[])]


def bestbuy_rows(doc,upc):
    out=[]
    for r in doc.get("products",[]):
        if not same_gtin(str(r.get("upc","")),upc):continue
        price=Decimal(str(r["salePrice"])) if r.get("salePrice") is not None else None
        cents=price*100 if price is not None and price.is_finite() else None
        status="available_to_order_online" if r.get("onlineAvailability") is True else "not_confirmed_online"
        if r.get("priceRestriction") or r.get("preowned") is not False or r.get("specialOrder") is True or r.get("active") is not True:
            status="requires_review"
        if cents is None or cents<0 or cents!=cents.to_integral_value():status="requires_review"
        out.append({"source":"Best Buy US Products API","sku":r.get("sku"),"title":r.get("name"),"url":r.get("url"),
                    "item_price_cents":int(cents) if cents is not None and status!="requires_review" else None,
                    "currency":"USD","status":status,"stock_update":r.get("onlineAvailabilityUpdateDate"),
                    "orderable":r.get("orderable"),"model":r.get("modelNumber")})
    return out


def bestbuy(upc):
    gtin(upc)
    fields="sku,upc,name,modelNumber,url,salePrice,active,preowned,priceRestriction,specialOrder,onlineAvailability,onlineAvailabilityUpdateDate,orderable"
    doc=get_json("https://api.bestbuy.com/v1/products(upc="+upc+")",{"apiKey":os.environ["BESTBUY_API_KEY"],"format":"json","show":fields})
    return bestbuy_rows(doc,upc)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("command",choices=["discover","sellers","bestbuy"])
    p.add_argument("value",help="query, explicitly selected product token, or exact UPC")
    args=p.parse_args()
    try:
        out={"discover":discover,"sellers":sellers,"bestbuy":bestbuy}[args.command](args.value)
        print(json.dumps(out,indent=2,default=str))
    except (URLError,OSError,KeyError,ValueError,ArithmeticError) as err:
        # Never log the provider's request URL: these public protocols use query-string credentials.
        sys.exit("Provider unavailable: "+type(err).__name__+". Check key, permissions and input.")
