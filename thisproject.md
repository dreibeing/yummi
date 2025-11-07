# Yummi Woolworths Cart Integration — Project Guide

## Reference Documents
- [CartIntegration.md](CartIntegration.md)
- [Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt](Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt)
- [POC-Memory.md](POC-Memory.md)
- [PayFast Migration Plan](payfastmigration.md)
- [Server Plan & TODO](server.md)
- [Mobile Scaffold Spec (Clerk + PayFast)](yummi_scaffold_spec.md)
- [Chargebacks & Refund Policy](Chargebacks.txt)
- [Build Plan & Roadmap](plan.md)
- Thin-slice mobile client entry point: [`thin-slice-app/App.js`](thin-slice-app/App.js)

## Project Overview
Build a production-ready pipeline that prepares product data, enriches basket payloads, and fills the user’s Woolworths cart safely and quickly. The mobile app will call into this stack to generate a ready-for-checkout experience on the retailer site while respecting performance, rate limiting, and ToS constraints.

## Current Status (2025-11-04)
- FastAPI backend exposes `/v1/payments/payfast/*` and `/v1/wallet/balance`, persisting payments + wallet ledger via Alembic migrations ([models](yummi-server/app/models.py), [routes](yummi-server/app/routes/)).
- PayFast ITNs write ledger credits; `/v1/me` now returns wallet information alongside Clerk claims.
- Thin-slice Expo client fetches wallet balances, launches PayFast hosted checkout, and refreshes ledger after payment ([mobile code](thin-slice-app/App.js)).
- Observability/logging and Docker/Fly infrastructure captured in [server.md](server.md); deployment-ready Compose + Fly configs exist.
- Data ingestion and cart-fill flows operate via the existing resolver, thin-slice endpoints, and Chrome extension runtime.

## Tech Stack Snapshot
- **Client trigger**: React Native + Expo Router (TypeScript). Auth via Supabase/Auth0; subscriptions via RevenueCat. The app hands off shopping lists to the integration.
- **Primary cart fill**: MV3 Chrome extension (TypeScript). Prefers same-origin XHR (`POST /server/cartAddItems`), falls back to DOM automation only when needed. Uses chrome.storage for queue state and throttled batching.
- **Optional tools**: Playwright runner for QA/demo; serverless functions for analytics and optional handoff triggers.
- **Backend data**: Product catalog maintained locally (pickle/JSON); resolver catalog maps internal IDs to Woolworths product IDs + URLs.
- **Scraper utility**: [`woolworths_scraper/`](woolworths_scraper) (Python 3.11+, httpx) for Food department category discovery and product enrichment.
- **Payments**: PayFast hosted checkout (card + Instant EFT) with signed requests from the FastAPI backend; ITN confirms wallet top-ups and credits the wallet ledger stored in Postgres.
- **Wallet policy**: chargebacks/refunds handled per [Chargebacks.txt](Chargebacks.txt) (negative balances allowed, spending blocked until recovered, audit trails required).

## Working Practices (adapted from Dream/Openworld guidelines)
1. **Service-first architecture**
   - Keep Chrome extension modules (service worker, content scripts) thin; push logic into focused utilities (queue manager, XHR client, resolver).
   - In Python, split responsibilities into client, parser, discovery, scraper, writer modules.
2. **Coordinators delegate**
   - Extension popup only issues commands; service worker orchestrates but does not embed DOM logic.
   - Scraper CLI orchestrates discovery/scraping but leaves fetching/parsing to dedicated helpers.
3. **File size & clarity guardrails**
   - Target ≤300 lines per module; extract helpers when approaching limits (e.g., separate PDP enrichment module if files grow large).
4. **Type safety & validation**
   - TypeScript: strict mode, no implicit `any`. Validate JSON payloads (`items` schema) before enqueueing.
   - Python: maintain dataclass/TypedDicts for product records where practical; defensive parsing when reading `window.__INITIAL_STATE__`.
5. **Structured logging & flags**
   - Popup shows succinct status/log lines; suppress noisy console output unless debugging.
   - Scraper uses logging levels; retry/backoff for 4xx/5xx.
6. **Performance/risk management**
   - Cart fill batches 3–5 items with jitter to avoid anti-bot triggers; uses same-origin fetch to respect user session.
   - Scraper throttles requests, respects pagination, and caches outputs.

## Web Scraper Notes
- Discover Food categories (run after major site changes):  
  `python -m woolworths_scraper discover --output woolworths_scraper/config/categories.food.json --log-level INFO`
- Full scrape (uses frozen categories and refreshes resolver catalog + data exports):  
  `python -m woolworths_scraper scrape --categories woolworths_scraper/config/categories.food.json --catalog-output resolver/catalog.json --log-level INFO`
  - Swap `--categories …` for `--auto-food` to re-discover categories inline.
  - Use `--limit <N>` during testing to clamp record counts.
  - Disable individual categories by setting `"enabled": false` inside the JSON file (logged as `Skipping disabled category …`).
- Discovery walks the Food navigation, filters promo-only nodes, and stores breadcrumb `path` arrays.
- Scraper paginates via `?No=<offset>`, extracts `productId`, `catalogRefId`, pricing, imagery, and breadcrumb path, and skips categories that repeatedly return HTTP 500.
- PDP enrichment (next iteration): parse `productInfo.multiAttributes` for pack sizes, nutrition, allergens, ingredients; merge into canonical records.
- Outputs feed `resolver/catalog.json`, ensuring cart fill always uses Woolworths IDs with URLs as supporting metadata.

## Cart Integration Highlights
- Input contract: `{ retailer: "woolworths_co_za", items: [{ productId?, catalogRefId?, url?, qty, title }] }`.
- XHR payload: `deliveryType`, `fromDeliverySelectionPopup`, optional `address.placeId`, `storeId`, and array of `{ productId, catalogRefId, quantity }`.
- Session context captured from cookies (`userDelivery`, `location`, `storeId`); fallback to DOM automation only when XHR fails.
- Extension opens `https://www.woolworths.co.za/check-out/cart` upon completion while surfacing per-item status.

## Wallet & Payments Workflow
1. Mobile client calls `/v1/payments/payfast/initiate` to retrieve PayFast hosted checkout details (JSON signature payload).
2. After PayFast redirects to `yummi://payfast/return`, backend processes the ITN, validates via PayFast, and updates the `payments` + `wallet_transactions` tables.
3. Clients fetch `/v1/wallet/balance` (and `/v1/me`) to display updated wallet totals and recent transactions.
4. Chargebacks/refunds will mirror ledger entries and enforce negative-balance lockouts (see [Chargebacks.txt](Chargebacks.txt)).

## TODO Focus Areas
1. Refine category discovery filters (skip promo-only nodes), freeze canonical category list, and version it under source control.
2. Add PDP enrichment for pack size/specifications + nutritional metadata.
3. Sync enriched catalog into `resolver/catalog.json`; ensure extension prioritizes IDs from catalog.
4. Exercise the Fly-hosted PayFast sandbox stack end-to-end (top-up, ITN, wallet refresh) and capture QA notes.
5. Finalize chargeback/refund workflows (negative balance handling, debit reversals, abuse monitoring) per [Chargebacks.txt](Chargebacks.txt).
6. Extend wallet UX + automated test coverage so mobile surfaces transactions, errors, and negative balances clearly.

## Change Management
- Update this guide alongside major workflow or tooling changes.
- Document new commands, schemas, and test expectations for quick onboarding.
- Version control: repository lives at `https://github.com/dreibeing/yummi.git`. Standard flow:
  ```powershell
  git add .
  git commit -m "Describe change"
  git push
  ```
  (First push already ran `git push -u origin main`; future pushes can just use `git push`.)

## Immediate Next Steps
See [plan.md](plan.md) for the authoritative roadmap. The top priorities for the next coding session are:
1. **Sandbox PayFast QA**: run a full wallet top-up from the thin-slice app against Fly, confirm ITN processing, and document any gaps in [payfastmigration.md](payfastmigration.md).
2. **Chargeback/refund groundwork**: design debit/negative-balance handling in backend services (refer to [Chargebacks.txt](Chargebacks.txt)).
3. **Wallet UI polish**: expand thin-slice UI to show full transaction history and flag negative balances before we harden chargeback logic.
