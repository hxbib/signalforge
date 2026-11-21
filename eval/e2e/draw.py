"""Blind draw for the end-to-end benchmark. Run once, before any arm; refuses to overwrite a freeze.

The query list below was written before any catalog call. The pick inside each query is made by a RNG
seeded with the code SHA, so nobody on the team chooses a product or a photo.
Photos go to private/e2e/ (gitignored). The freeze file carries ids, hashes and counts only.

  .venv/bin/python eval/e2e/draw.py      # draw A -> eval/results/e2e-freeze.json
  .venv/bin/python eval/e2e/draw.py --b  # held-out draw B -> eval/results/e2e-freeze-b.json: same queries, seed =
                                         # sha256 of eval/results/e2e-v1.json, every draw-A product id excluded
"""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.merchants import shop  # noqa: E402
from live_smoke import clean_image  # noqa: E402  (reference/, on sys.path after `import app`)

FREEZE = ROOT / "eval" / "results" / "e2e-freeze.json"
FREEZE_B = ROOT / "eval" / "results" / "e2e-freeze-b.json"
PHOTOS = ROOT / "private" / "e2e"
TARGET_CASES = 24
IMAGE_HOSTS = {"cdn.shopify.com"}
IMAGE_CAP = 5_000_000

QUERIES = [
    "building set", "board game", "action figure", "phone charger", "power bank", "wireless headphones",
    "usb-c cable", "mechanical keyboard", "insulated water bottle", "tumbler", "nonstick frying pan",
    "chef knife", "pour over coffee maker", "face moisturizer", "hardcover novel", "jigsaw puzzle",
    "dog toy", "yoga mat", "notebook", "backpack", "bluetooth speaker", "desk lamp", "sunscreen",
    "running socks", "cast iron skillet", "card game", "cat treats", "fountain pen", "camping lantern",
    "electric toothbrush",
]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def first_image(p: dict) -> str | None:
    """First catalog image of a universal product: product media, then its variants in catalog order."""
    for media in [p.get("media")] + [v.get("media") for v in p.get("variants") or [] if isinstance(v, dict)]:
        for m in media or []:
            if isinstance(m, dict) and m.get("type", "image") == "image" and isinstance(m.get("url"), str):
                return m["url"]
    return None


def permitted(url: str | None) -> bool:
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return False
    return parts.scheme == "https" and parts.hostname in IMAGE_HOSTS


def fetch_image(url: str) -> bytes:
    if not permitted(url):
        raise ValueError("image host not permitted")
    req = Request(url, headers={"Accept": "image/jpeg,image/png,image/webp"})
    with build_opener(_NoRedirect).open(req, timeout=8) as resp:
        raw = resp.read(IMAGE_CAP + 1)
    if len(raw) > IMAGE_CAP:
        raise ValueError("image too large")
    clean_image(raw)  # must be an image the pipeline accepts
    return raw


def pick_order(candidates: list[dict], seed: str, index: int, query: str) -> list[dict]:
    """Seeded order in which a query's candidates are tried (the first whose photo downloads is the case)."""
    order = list(candidates)
    random.Random(f"{seed}:{index}:{query}").shuffle(order)
    return order


def sellers_of(doc: dict) -> int:
    variants = ((doc.get("product") or {}).get("variants")) or []
    return len({(v.get("seller") or {}).get("id") for v in variants if isinstance(v, dict) and (v.get("seller") or {}).get("id")})


def main(argv: list[str] | None = None) -> int:
    held_out = "--b" in (argv or [])
    freeze_path = FREEZE_B if held_out else FREEZE
    photos = PHOTOS / "b" if held_out else PHOTOS
    prefix = "e2e-b" if held_out else "e2e"
    if freeze_path.exists():
        print(f"{freeze_path} exists; the draw is frozen and is not repeated", file=sys.stderr)
        return 1
    code_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    cases, skipped, seen = [], [], set()
    if held_out:
        sha = hashlib.sha256((ROOT / "eval" / "results" / "e2e-v1.json").read_bytes()).hexdigest()
        seen = {c["gold_upid"] for c in json.loads(FREEZE.read_text())["cases"]}
    else:
        sha = code_sha
    excluded = len(seen)
    photos.mkdir(parents=True, exist_ok=True)
    for i, query in enumerate(QUERIES):
        if len(cases) >= TARGET_CASES:
            break
        try:
            doc, _ = shop.search(query, limit=10)
        except Exception as e:
            skipped.append({"query": query, "reason": "search_failed:" + type(e).__name__})
            continue
        cands = [p for p in shop._products(doc) if p.get("id") and p["id"] not in seen and permitted(first_image(p))]
        chosen = None
        for tries, p in enumerate(pick_order(cands, sha, i, query), 1):
            try:
                raw = fetch_image(first_image(p))
            except Exception:
                continue
            chosen = (p, raw, tries)
            break
        if not chosen:
            skipped.append({"query": query, "reason": "no_product_with_usable_image", "candidates": len(cands)})
            continue
        p, raw, tries = chosen
        try:
            pdoc, _ = shop.product(p["id"])
            sellers = sellers_of(pdoc)
        except Exception:
            sellers = None
        case_id = f"{prefix}-{len(cases) + 1:02d}"
        (photos / f"{case_id}.img").write_bytes(raw)
        seen.add(p["id"])
        cases.append({"case_id": case_id, "query": query, "gold_upid": p["id"], "sellers": sellers,
                      "image_sha256": hashlib.sha256(raw).hexdigest(), "candidates": len(cands), "pick_tries": tries})
        print(case_id, query, "candidates", len(cands), "sellers", sellers, flush=True)
    out = {"seed": sha, "code_sha": code_sha, "created_at": int(time.time()), "target_cases": TARGET_CASES,
           "queries": QUERIES, "cases": cases, "skipped": skipped}
    if held_out:
        out.update(draw="B", seed_source="sha256 of eval/results/e2e-v1.json", excluded_draw_a_products=excluded)
    freeze_path.write_text(json.dumps(out, indent=1) + "\n")
    print(f"frozen {len(cases)} cases, {len(skipped)} queries skipped -> {freeze_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
