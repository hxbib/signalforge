"""At most one automatic second look. A crop cannot choose between multiple unreferenced items."""
import io
from PIL import Image
from compare_offers import same_gtin

def second_look(image, note, visual, decision, inspect):
    if decision["pass"] and visual.target_count == "one":
        return image, visual, decision, None
    box = visual.focus_box
    if not box or len(box)!=4 or any(type(p) is not int or not 0<=p<=1000 for p in box):
        return image, visual, decision, None
    if (decision["choice"]=="multiple" or visual.target_count=="many") and not (
        len(visual.focus_words.strip()) >= 3 and visual.focus_words.strip().casefold() not in {"this","that","this one","the item","the product","this product","this item"} and visual.focus_words.casefold() in note.casefold()
    ):
        return image, visual, decision, None
    left,top,right,bottom=box
    if right-left<100 or bottom-top<100 or (right-left)*(bottom-top)>900000:
        return image, visual, decision, None
    with Image.open(io.BytesIO(image)) as im:
        crop=im.crop((left*im.width//1000,top*im.height//1000,
                      right*im.width//1000,bottom*im.height//1000))
        if min(crop.size)<48:
            return image, visual, decision, None
        out=io.BytesIO()
        crop.convert("RGB").save(out,format="JPEG",quality=90)
    retry=out.getvalue()
    seen,gate,trace=inspect(retry,note)
    record={"event":"auto_crop","box":box,"neon":trace,"decision":gate}
    if not gate["pass"] or seen.target_count!="one":
        return image,visual,decision,record
    if visual.barcode and seen.barcode and not same_gtin(visual.barcode,seen.barcode):
        return image,visual,decision,record
    for key in ("part_number","brand"):
        a,b=getattr(visual,key).casefold(),getattr(seen,key).casefold()
        if a and b and a!=b:
            return image,visual,decision,record
    return retry,seen,gate,record

