# Cart Integration Working Memory

Purpose: Design and validate the fastest, low-risk Woolworths basket fill integration that aligns with Phase 1 PRD and the Agent Brief, using the correct tech stack. This doc tracks the plan, test checklist, and TODOs while we implement.

---

## Context Snapshot

- Phase: Phase 1 app; focus on “Retailer Handoff” that fills a Woolworths cart from a generated item list; user reviews and pays on retailer site.
- Tech stack (per PRD + Agent Brief):
  - Mobile: React Native + Expo Router (TS); RevenueCat for paywall.
  - Cart fill (primary): Chrome Extension (MV3), same-origin XHR preferred; DOM-click fallback; open `/cart`.
  - Optional runner: Playwright script for dev/QA demos (not for production scale).
- Performance targets: 50 items ≈ 30s; 100 items ≈ 60s; ≥95% add success.
- Data available now: `data/product_table_folder/final_product_table_df.pkl` with columns: `uid, name, price, category, sub_category, product_name, quantity, unit`. Missing: product `url` or retailer SKU.

---

## Integration Contract (Item List JSON)

Input schema used by the extension/runner:

```
{
  "retailer": "woolworths_co_za",
  "store": "(optional human label)",
  "items": [
    { "url": "https://www.woolworths.co.za/prod/...", "sku": "(optional)", "qty": 2, "title": "Woolworths Salted Butter 500g" }
  ]
}
```

- Preferred: `url` and/or `sku`. Quantity is integer ≥1. `title` only for logs/fallbacks.
- Success: ≥95% items added with correct quantities, within perf targets, same-origin requests from user’s logged-in browser session.

---

## Architecture Overview

- Chrome Extension (MV3)
  - Service worker orchestrates queue, batching (3–5 concurrent), throttling and retries.
  - Content script performs add-to-cart via same-origin `fetch` (XHR path). If unavailable or fails, falls back to DOM automation on the product page.
  - Popup UI: accept/paste item list JSON, show progress, per-item results, and final summary; open `/cart`.

- Playwright Runner (optional for dev/QA)
  - Visible Chromium; human logs in once and selects store; script adds items in parallel tabs; same logs and summary; opens `/cart`.

---

## Product ID Catalog (Canonical Source)

Goal: Keep a stable mapping from our internal product references to Woolworths product IDs (`productId` / `catalogRefId`) so cart fills do not depend on URLs or live scraping.

1) Canonical Catalog (preferred path)
   - Maintain `{uid | normalized_title -> {productId, catalogRefId, url?}}` under version control.
   - Treat `productId`/`catalogRefId` as required; `url` becomes supporting metadata for UX/debug.
   - Populate initially by parsing existing URLs or via a controlled scrape, then keep in sync as new products ship.

2) Safe Resolver (fallback)
   - If an item ever lacks a product ID, use a rate-limited lookup (manual confirmation or single-page search) to find the ID once, then append it to the catalog.
   - Keep this outside the main UX flow to avoid stressing Woolworths.

3) Manual Override
   - Allow operators to inject `{productId, url}` for edge cases; persist immediately to the catalog so future runs are ID-first.

Outputs: item lists already populated with `productId` (and optional `url`) for the extension/runner.

---

## Performance & Safety Tactics

- Concurrency: 3–5 items at a time; 300–800ms jitter between item ops; 500–1000ms between batches.
- Retries: exponential backoff on transient failures; skip fast on hard errors (OOS, no variant match).
- Observability: per-item `{title,url,sku,qty,status,reason?,duration_ms}`; aggregate `{ok,failed,elapsed_ms}`.
- User control: runs only on `woolworths.co.za`, user-initiated; open `/cart` at end; clear permission text.

---

## Cart API Snapshot (Captured Oct 2025)

- Endpoint: `POST https://www.woolworths.co.za/server/cartAddItems`
- Required headers (same-origin): `content-type: application/json`, `x-requested-by: Woolworths Online`
- Payload shape:
  ```json
  {
    "deliveryType": "Standard",
    "fromDeliverySelectionPopup": "true",
    "address": { "placeId": "<google_place_id>" },
    "items": [
      { "productId": "6009217580415", "catalogRefId": "6009217580415", "quantity": 1, "itemListName": "Extension" }
    ]
  }
  ```
- Session-derived context (delivery type + placeId + storeId) pulled from `userDelivery` / `location` / `storeId` cookies (requires extension `cookies` permission). Product/catalog IDs sourced from the canonical catalog (URL parsing only a fallback).
- Response returns JSON with optional `errorMessages[]`; success indicated by HTTP 200 and empty errors.

---

## Product Catalog Scraper (In Progress)

- New module under `woolworths_scraper/` assembles the canonical product catalog.
- Install dependencies: `pip install -r woolworths_scraper/requirements.txt`.
- Discover Food categories: `python -m woolworths_scraper discover --output woolworths_scraper/config/categories.food.json`
- Run a scrape (auto-discover inline):
  ```bash
  python -m woolworths_scraper scrape --auto-food \
    --output-json data/product_table_folder/woolworths_products_raw.jsonl \
    --output-csv data/product_table_folder/woolworths_products_summary.csv
  ```
- The scraper iterates category pages via the `?No=` pagination parameter, parses `window.__INITIAL_STATE__`, and normalizes product metadata (IDs, prices, category path, promotions).
- Each record includes `path` (e.g., `["Food", "Bakery", "Bread & Rolls", …]`) for downstream classification and recipe pairing.
- Next steps: tune discovery filters (skip promo-only nodes), add PDP enrichment for pack size/specifications, and push outputs into `resolver/catalog.json`.


## Implementation Plan

1) Extension Foundation (MV3)
   - Scaffold `manifest.json`, `popup.{html,ts}`, `service-worker.ts`, `content.ts` (TypeScript, esbuild/Vite).
   - Popup UI: paste/upload item JSON, start/stop, progress, results, open cart.
   - Storage: queue, index, running flag in `chrome.storage.local` for resilience.

2) Cart XHR Path
   - Recon via DevTools to capture add-to-cart endpoint, headers (CSRF/auth), body schema (sku/variant/qty), response shape.
   - Implement `addViaXHR(skuOrUrl, qty)` using same-origin `fetch` with required headers.
   - Handle multi-variant products and stock limits.

3) DOM-click Fallback
   - Given a `url`, open product page, wait for settle, set quantity if supported, click “Add to cart”.
   - Detect success via toast/cart counter or network response; continue.

4) Batch + Throttle Engine
   - Execute in small parallel batches with jitter; exponential backoff; retry-failed-only control.

5) URL/SKU Resolver
   - Implement a small Node/TS utility to enrich items from `final_product_table_df.pkl` outputs:
     - Use curated catalog first; optionally a safe search resolver with strict rate limits.
     - Persist `catalog.json` for reuse.

6) Playwright Runner (Optional)
   - Script that mirrors extension logic for dev/QA demos; manual login; same logs; opens cart.

7) Packaging & Docs
   - `README` with setup, permissions, and demo; sample `items-10.json`, `items-50.json`, `items-100.json`.

---

## Test Checklist

- Functional
  - Logged-in prerequisite gating before any action.
  - Single item (qty=1) via XHR; cart count increments.
  - Quantity >1 respected via XHR and via DOM fallback.
  - Mixed batch: items with `sku` and with only `url`.
  - Out-of-stock item flagged with reason; others continue.
  - Variant-required item: select or fail gracefully with clear reason.

- Performance
  - 10 items in ≈10s.
  - 50 items in ≈30s.
  - 100 items in ≈60s.
  - Measure with `performance.now()`; log elapsed and per-item durations.

- Resilience
  - Network hiccup: retry with backoff; eventual success.
  - One item fails: remaining proceed; “retry failed only” works.
  - XHR path breaks: automatic fallback to DOM-click.

- UX
  - Clear progress (ok/failed counts), cancel button, final “Open Cart”.
  - Permissions text explains domain scoping; no credentials handled.

---

## TODO (Prioritized)

1) Create `extension/` scaffold (MV3 + TS build) and wire popup ↔ service worker ↔ content messaging.
2) Implement queue storage, progress reporting, and cancel/stop.
3) DevTools recon: capture Woolworths add-to-cart XHR details; draft `addViaXHR()`.
4) Implement DOM-click fallback flow on a single product page; success detection heuristics.
5) Batch engine with jitter and exponential backoff; end by opening `/cart`.
6) Define item JSON schema in code; validate input; sample files (10/50/100).
7) Build `catalog.json` bootstrap (manual + curated); add resolver module with strict rate limiting and persistent cache.
8) Add per-item structured logs and aggregate summary; “retry failed only”.
9) Playwright runner script for dev/QA parity (optional).
10) README and demo assets.
11) New task (next): design and implement a Woolworths web scraper that captures product metadata, including canonical `productId`, `catalogRefId`, URLs, pricing, pack size, and category tags, then publish a refreshed product table feeding the integration catalog.

---

## Data Hooks and Utilities

- Source table: `data/product_table_folder/final_product_table_df.pkl` (3832 rows; no URLs/SKUs).
- Utility (Node/TS): ingest a CSV/JSON export from this table, map to `{title, qty}`, and enrich with `url`/`sku` using `catalog.json` and optional safe search.
- Output: `items-N.json` files used by the extension/runner.

---

## Open Questions / Assumptions

- Cart XHR availability: assumed present; exact endpoint/CSRF details to be captured via DevTools.
- SKU exposure: may be derivable from product page or XHR; otherwise rely on `url` + DOM fallback.
- Store context: confirm whether a store selection/state impacts add-to-cart endpoint.
- ToS/Permissions: extension remains strictly user-side; seek permission for any formal integration beyond POC.

---

## Next Steps

- Approve this plan and TODOs.
- If approved, I’ll scaffold `extension/` and a minimal `resolver/` utility, plus sample `items-10.json` to start functional tests.
