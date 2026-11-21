# Provenance: what was built, when, and from what

**Event:** Gemini 4 NYC Hackathon, Saturday November 21, 2026. Hacking window 10:30–17:00 ET.
**Team:** Sadman Habib (GitHub [hxbib](https://github.com/hxbib)), solo.
**Track:** 5 Sigma, plus the Build with Commerce (Shopify) bonus.

Everything in this repository is event-day work. It has two layers, and judges should be able to tell them apart.

## Layer 1: event-day planning notes and reference modules

Written on November 21 before the application was assembled, then committed as the first commit of this repository.

- `spec/`: the product decisions (the buyer experience, interaction and speed budgets, watch semantics), interface
  notes and retailer notes.
- `reference/`: small, separately testable Python modules: prompts and schemas for the model jobs
  (`deal_models.py`, `find.py`, `gemini_io.py`), the automatic second look (`visual_recovery.py`), image cleaning
  (`live_smoke.py`), the Shop MCP client (`shop.py`), barcode normalization and offer comparison
  (`compare_offers.py`), the deterministic eligibility rules (`buyer.py`), watch semantics, proof and alert ledger
  (`autowatch.py`, `core.py`, `proof.py`, `ledger.py`), and their tests.
- `eval/PROTOCOL.md`, `eval/bench.py`, `eval/score.py`, `eval/seal_baskets.py`: the evaluation protocol, sampler
  and scorer, fixed before any result existed.

## Layer 2: the application integrated on top

- `app/`: the HTTP surface, pipeline and run storage (`main.py`, `pipeline.py`, `store.py`, `contracts.py`,
  `config.py`); the model lane (`app/models/`, the corrected live boundary to the Interactions and Decisions APIs,
  binding with deterministic corroboration, the listing check (two versions, both kept), local barcode decode); the commerce lanes
  (`app/merchants/shop.py` for the Shop catalog, `chains.py` for the merchant's own storefront, `compare.py` for
  fan-out and the shopper-facing result, `ranking.py` for tiered identity on top of the reference rules); the
  watch lane (`app/watch/`); the evaluation evidence route (`app/evidence/`).
- `app/web/`: the browser app (capture, progress, result, evidence drawer, watch, proof page).
- `tests/`: offline tests for each lane.
- `eval/draw.py`, `run_pairs.py`, `summarize.py`, `report.py`, `interim.py`, `failures.py`, `diagnose.py`, `eval/e2e/` and
  `eval/results/`: the frozen draws, the real evaluation runs, the failure analysis and their results.
- `docs/`, `README.md`, `scripts/`.

The identity design changed during integration because of what the live Shop catalog returned at the event (no GTIN
field; UPC lookup `not_found`): the reference GTIN-lookup path could not be the spine, so the tiered identity in
`docs/CONTRACT.md` section 0 and `app/merchants/ranking.py` was built in its place.

## Commit history

`git log --reverse --format='%h %ad %s' --date=iso`, times shown in ET. Range at the time of writing:
`0e6fbd2..caf5b8f` (this list is refreshed before submission; the repository log is authoritative).

| Commit | Time (ET), Nov 21 2026 | Subject |
|---|---|---|
| `0e6fbd2` | 15:36:50 | Seed: event-day product spec, reference modules and evaluation protocol |
| `6cf1194` | 15:43:38 | Application skeleton: API, pipeline, model/catalog/ranking/watch/eval lanes, browser app |
| `caf5b8f` | 15:53:53 | Serve and tunnel scripts; model, catalog and ranking lanes verified against live services |

The first commit was made at 15:36 ET. Work earlier in the day (the planning notes and reference modules of layer 1)
was done outside this repository's history, so the log shows when each layer was committed, not when each line was
first written. The evaluation draw was frozen against the first commit (`eval/results/freeze.json`,
`code_sha` `0e6fbd2`) before either arm ran.

## Prior work: influence only, no code

The author has earlier projects in the same problem area. They are acknowledged here as background and influence.

- **Dealza**: the author's earlier private price-comparison project.
- **Curzoh Task Engine / Dealforge**: the author's private Go engine for polling retailers and scoring deals.
- **monitord** ([saucesteals/monitord](https://github.com/saucesteals/monitord)): a third-party open-source project.

Check performed on November 21, 2026 at about 15:56 ET, before this statement was written:

| Project | What was checked | Result |
|---|---|---|
| Curzoh Task Engine (`hxbib/curzoh-task-engine`, private, HEAD `ae04407` of 2026-11-08) | Shallow clone, then a whole-word search for the distinctive identifiers of this repository (`inspect_variant`, `compare_now`, `second_look`, `bind_target`, `explicit_family`, `anchors_for`, `run_comparison`, `show_witness`, `autowatch`, `visual_recovery`, `parse_goal`, `compile_watch`, `proof_hash`, `near_miss` and others) and for all 211 function and class names of eight or more characters defined in `reference/`, `app/`, `eval/` and `scripts/` | No distinctive identifier found. The only matches were ordinary words used as names (`products`, `selected`, `question`, `structured`, `describe`, `evaluate`, `Retailers`, `Selection`). The project is written in Go and contains no Python file; this repository is Python and browser JavaScript. |
| monitord (`saucesteals/monitord`, public, HEAD `b5d4c11` of 2026-11-03) | Same search | No distinctive identifier found; only `describe`, `selected`, `structured`, `question`, `discover`. Written in Go. |
| Dealza (`hxbib/dealza`, private) | Clone attempted | The repository is empty (no commits, no branches), so there was no Dealza source to compare against. For Dealza the statement rests on the author's account. |

The checks above found no overlap with these projects. SignalForge also works differently from the earlier engine:
it does not poll retailers in the background; every merchant read here is triggered by a shopper's photo.

## Libraries, services and data

| What | Used for | Notes |
|---|---|---|
| google-genai (Python SDK) | Gemini 4 Neon and Gemini 4 Carbon through the Interactions API; Gemini Decision 1 through the Decisions API (beta) | event-issued API key, server-side only |
| FastAPI, Uvicorn, python-multipart | HTTP server and upload handling | open source |
| Pydantic | typed model outputs and schemas | open source |
| Pillow | image cleaning and cropping | open source |
| zxing-cpp | local barcode decode | open source |
| pytest | offline tests | open source |
| SQLite (Python standard library) | run storage, watch ledger | |
| shop.dev Shop MCP (`https://mcp.shop.com/`) | product discovery, seller variants, price and availability | read-only, shopper-triggered; nothing kept beyond the run; merchant links kept unmodified |
| Merchant Shopify storefront Ajax product endpoint (`/products/<handle>.js`) | merchant confirmation of the exact variant | one read per listing per run; memory only, at most 120 s |
| WDC Product Data Corpus and Gold Standard for Large-scale Product Matching v2 | Experiment A | Peeters, Primpeli, Bizer, Web Data Commons, University of Mannheim: https://webdatacommons.org/largescaleproductcorpus/v2/ . The raw corpus and gold labels are not redistributed in this repository; `eval/results/freeze.json` records source URLs, archive digests and pair ids. |

Not in this repository: API keys, uploaded photos, raw merchant responses, the raw WDC archives and gold labels, and
the event's participant PDF.

Stored-interaction retention at the model service is disclosed in the README (Privacy).
