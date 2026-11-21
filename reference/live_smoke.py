"""Run with a consenting participant's image. Prints only stage names, ids, statuses and times."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import warnings
from PIL import Image, ImageOps
from gemini_io import CARBON, NEON, COMPILE, VISION, Grounding, Proposal, clients, parts, route, structured
from shop import search


def clean_image(raw):
    if not 0 < len(raw) <= 5_000_000:
        raise ValueError("choose an image up to 5 MB")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in {"JPEG","PNG","WEBP"} or im.width*im.height > 16_000_000 or getattr(im,"n_frames",1) != 1:
                raise ValueError("unsupported or oversized image")
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1400,1400))
            out = io.BytesIO()
            im.save(out,"JPEG",quality=85)
    return out.getvalue()


def main():
    import pathlib
    p=argparse.ArgumentParser()
    p.add_argument("image",type=pathlib.Path)
    p.add_argument("--note",required=True)
    args=p.parse_args()
    with args.image.open("rb") as f:
        pic=clean_image(f.read(5_000_001))
    stable,beta=clients()
    raw=parts(pic,"image/jpeg",args.note)
    # Smoke exercise calls both services even when the gate blocks continuation in the product workflow.
    with ThreadPoolExecutor(max_workers=2) as pool:
        gate_job=pool.submit(route,beta,raw)
        visual,trace=structured(stable,NEON,VISION,Grounding,raw)
        gate=gate_job.result()
    print(json.dumps({"neon":trace,"neon_status":visual.status,"decision":gate}))
    # Explicit empty candidate list tests Carbon's ability to abstain; this is not a successful compile.
    candidate,trace=structured(stable,CARBON,COMPILE,Proposal,parts(pic,"image/jpeg",args.note,{"candidates":[]}))
    print(json.dumps({"carbon":trace,"returned":candidate.status}))
    if candidate.status=="ready":
        raise ValueError("Carbon invented an absent candidate")
    doc, source=search(visual.query) if visual.query else ({}, {})
    print(json.dumps({"shop_read":source,"products":len(doc.get("products",[]))}))
    if not doc.get("products"):
        raise ValueError("no catalog search results")


if __name__=="__main__":
    main()
