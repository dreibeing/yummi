# Yummi Woolworths Cart Integration — Project Guide

## Reference Documents
- [CartIntegration.md](CartIntegration.md)
- [Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt](Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt)
- [POC-Memory.md](POC-Memory.md)

## Project Overview
Build a production-ready pipeline that prepares product data, enriches basket payloads, and fills the user’s Woolworths cart safely and quickly. The mobile app will call into this stack to generate a ready-for-checkout experience on the retailer site while respecting performance, rate limiting, and ToS constraints.

## Tech Stack Snapshot
- **Client trigger**: React Native + Expo Router (TypeScript). Auth via Supabase/Auth0; subscriptions via RevenueCat. The app hands off shopping lists to the integration.
- **Primary cart fill**: MV3 Chrome extension (TypeScript). Prefers same-origin XHR (`POST /server/cartAddItems`), falls back to DOM automation only when needed. Uses chrome.storage for queue state and throttled batching.
- **Optional tools**: Playwright runner for QA/demo; serverless functions for analytics and optional handoff triggers.
- **Backend data**: Product catalog maintained locally (pickle/JSON); resolver catalog maps internal IDs to Woolworths product IDs + URLs.
- **Scraper utility**: `woolworths_scraper/` (Python 3.11+, httpx) for Food department category discovery and product enrichment.

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

## TODO Focus Areas
1. Refine category discovery filters (skip promo-only nodes), freeze canonical category list, and version it under source control.
2. Add PDP enrichment for pack size/specifications + nutritional metadata.
3. Sync enriched catalog into `resolver/catalog.json`; ensure extension prioritizes IDs from catalog.
4. Extend automated and manual test coverage per Agent Brief (functional, performance, resilience, UX).

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
