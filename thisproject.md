# Yummi Woolworths Cart Integration — Project Guide

> Use `README.md` for thin-slice template onboarding. This guide dives deeper into what exists today, what is Yummi-specific, and what remains outstanding.

## Reference documents
| File | Purpose | Template vs. Yummi | Notes |
|------|---------|--------------------|-------|
| [README.md](README.md) | High-level template overview + repo map. | Template | New entry point for anyone cloning the stack. |
| [plan.md](plan.md) | Phase roadmap + immediate priorities. | Template | Mirrors this guide’s “Next steps”. |
| [server.md](server.md) | FastAPI runbook, Docker/Fly deployment, ops notes. | Template | Includes PayFast and observability commands. |
| [yummi_scaffold_spec.md](yummi_scaffold_spec.md) | Mobile auth + payments scaffold spec (Clerk + PayFast). | Template | Source of Expo config + env vars. |
| [payfastmigration.md](payfastmigration.md) | Sandbox/production PayFast migration log + ITN checklist. | Template | References regression logs and monitoring. |
| [CartIntegration.md](CartIntegration.md) | Woolworths cart automation plan and test checklist. | Yummi | Keep for retailer automation history. |
| [interimcartintegration.md](interimcartintegration.md) | Additional Cart integration scratch notes. | Yummi | Historical decisions + experiments. |
| [POC-Memory.md](POC-Memory.md) | Resolver, data, and automation working memory. | Yummi | Useful when extending catalog logic. |
| [Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt](Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt) | Original agent brief. | Yummi | Archive-only but documents scope. |
| [Chargebacks.txt](Chargebacks.txt) | Wallet refund/chargeback policy. | Template | Applies to any tenant of the template. |

---

## 1. Template baseline snapshot

### Project overview
We operate a production-ready pipeline that prepares product data, enriches basket payloads, and fills a retailer cart quickly and safely. The Expo thin slice calls the FastAPI stack to give users a ready-for-checkout experience while respecting performance, rate limiting, and retailer ToS constraints.

### Status board (2025-11-12)
| Area | State | Notes |
|------|-------|-------|
| FastAPI backend (`yummi-server/`) | ✅ Stable | `/v1/payments/payfast/*`, `/v1/wallet/*`, thin-slice routes, Alembic migrations, Fly/Docker runbooks. |
| PayFast integration | ✅ Verified sandbox + staging | Remote ITN validation enforced; staging R100 top-ups logged in `payfastmigration.md`. |
| Expo thin slice (`thin-slice-app/`) | ✅ Wallet + PayFast loop | Clerk auth, wallet balance polling, PayFast WebView/deep-link return. |
| Automation queue (`thin-slice-server/`) | 🟡 Prototype | Supports order queueing and runner handoff; meant as inspiration for future automation services. |
| Documentation | 🟡 Partially consolidated | README + this guide now split between template overview and implementation detail; remaining docs mapped below. |

### Current status (2025-11-12)
- FastAPI backend exposes `/v1/payments/payfast/*` and `/v1/wallet/balance`, persisting payments + wallet ledger via Alembic migrations ([models](yummi-server/app/models.py), [routes](yummi-server/app/routes/)).
- PayFast initiate flow logs only non-sensitive metadata (reference, amount, currency, item) while still bridging return/cancel HTTPS callbacks into Expo deep links.
- Sandbox PayFast top-up succeeded (R100 on 2025-11-10) after aligning signature ordering with the official SDK and adding `python-multipart`; ITN hit `/v1/payments/payfast/itn` and the wallet credited immediately (runbook + logs captured in [payfastmigration.md](payfastmigration.md)).
- Fly staging now mirrors the sandbox flow (R100 on 2025-11-11) using Clerk-verified requests and remote ITN validation; secrets live in Fly and the hosted return/cancel bridges run from `yummi-server-greenbean.fly.dev`.
- Thin-slice Expo client fetches wallet balances, launches PayFast hosted checkout, and refreshes the ledger automatically; repeated top-ups increment the wallet without extra taps ([thin-slice-app/App.js](thin-slice-app/App.js)).
- Preference onboarding now persists to Fly: `/v1/preferences` accepts Clerk-authenticated PUT/GET calls, stores normalized preference tags in `user_preference_profiles`, and the Expo flow surfaces the sync timestamp after completion.
- Meal exploration + follow-up recommendations now ride the same runtime pipeline: `/v1/recommendations/exploration` seeds GPT-5 with filtered meals, `/v1/recommendations/feed` consumes the stored reactions + tags to return a ranked starter lineup, and the thin-slice UI walks users through liking/disliking meals before showing the curated follow-up list. Each feed run now persists the ordered meal IDs + manifest metadata inside `user_preference_profiles`, so the latest home-feed snapshot is queryable via `/v1/preferences`.
- The thin-slice client now lands on a meal home surface instead of immediately launching the exploration flow. A Reset Preferences CTA restarts tag selection, rebuilds Exploration Meals, and saves a fresh recommendation set, while a secondary action still exposes the Woolworths cart runner tools.
- New Ingredients surface: tapping “Next” on the meal home screen pushes a dedicated Ingredients view that aggregates the products for all currently active meals, sorts them alphabetically, and provides inline quantity controls plus CTAs for generating a shopping list or sending the items to the Woolworths cart filler.
- `/v1/preferences` now returns `latestRecommendationMeals` with name, description, prep/cook steps, and structured ingredient details. The Expo meal home uses this payload to show a meal-count selector, randomized recommendations, and recipe modals with full instructions/ingredients before users tap “Create shopping list.”
- `/v1/payments/payfast/status` enforces Clerk auth + owner checks, and remote validation can only be skipped with `PAYFAST_SKIP_REMOTE_VALIDATION=true` (dev default). CORS defaults tightened via [`env.staging`](env.staging)/[`env.prod`](env.prod).
- Observability/logging and Docker/Fly infrastructure captured in [server.md](server.md); deployment-ready Compose + Fly configs exist.
- Data ingestion and cart-fill flows operate via the resolver, thin-slice endpoints, and Chrome extension runtime.

### Tech stack snapshot
- **Client trigger**: React Native + Expo Router (TypeScript). Auth via Clerk (publishable key in Expo, secret key on server). RevenueCat ready for subscriptions.
- **Primary cart fill**: MV3 Chrome extension (TypeScript). Prefers same-origin XHR (`POST /server/cartAddItems`), falls back to DOM automation only when essential. Uses `chrome.storage` for queue state and throttled batching.
- **Optional tools**: Playwright runner for QA/demo; serverless hooks for analytics or automation triggers.
- **Backend data**: Product catalog stored locally (`resolver/catalog.json`, pickles/JSON). Resolver maps internal IDs to Woolworths product IDs and URLs.
- **Scraper utility**: [`woolworths_scraper/`](woolworths_scraper) (Python 3.11+, httpx) for category discovery + product enrichment.
- **Payments**: PayFast hosted checkout (card + Instant EFT) with signed requests from FastAPI; ITN confirms wallet top-ups and updates ledger rows.
- **Wallet policy**: chargebacks/refunds handled per [Chargebacks.txt](Chargebacks.txt) (negative balances allowed, spending blocked until recovered, audit trails required).

### Working practices (from Dream/Openworld guidelines)
1. **Service-first architecture**  
   Keep Chrome extension modules thin; push logic into focused utilities (queue manager, XHR client, resolver). Split Python responsibilities into client/parser/discovery/scraper/writer modules.
2. **Coordinators delegate**  
   Extension popup issues commands only; worker orchestrates but doesn’t embed DOM logic. Scraper CLI orchestrates but calls helper modules for heavy lifting.
3. **File size & clarity guardrails**  
   Target ≤300 LOC per module; extract helpers before complexity piles up (e.g., separate PDP enrichment module).
4. **Type safety & validation**  
   TypeScript strict mode, JSON payload validation prior to enqueueing. Python uses dataclasses/TypedDicts and defensive parsing for `window.__INITIAL_STATE__`.
5. **Structured logging & flags**  
   Popup surfaces succinct status lines; noisy console output only when debugging. Scraper uses proper logging levels plus retry/backoff on 4xx/5xx.
6. **Performance & risk management**  
   Cart fill batches 3–5 items with jitter; same-origin fetch respects user session. Scraper throttles requests, respects pagination, caches outputs.

### Wallet & payments workflow
1. Mobile client calls `/v1/payments/payfast/initiate` to retrieve PayFast hosted checkout details (JSON signature payload).
2. After PayFast redirects to `yummi://payfast/return`, backend processes the ITN, validates via PayFast, and updates the `payments` + `wallet_transactions` tables.
3. Clients fetch `/v1/wallet/balance` (and `/v1/me`) to display updated wallet totals and recent transactions.
4. Chargebacks/refunds mirror ledger entries and enforce negative-balance lockouts (see [Chargebacks.txt](Chargebacks.txt)).

### Archetype data pipeline status (2025-11-12)
- **Vocabulary + briefs**: `data/tags/defined_tags.json` (tags_version `2025.02.0`) and `data/tags/archetype_constraint_brief.md` now encode the mainstream-first coverage rules. `data/prompts/archetype_generation_prompt.md` and `scripts/archetype_prompt_runner.py` wire those rules into GPT-5 calls, including compact “prior archetype” context to avoid duplicates.
- **Latest run**: `data/archetypes/run_20251112T091259Z` (25 archetypes, one per batch, reasoning effort = low). Each batch folder contains raw prompts/responses plus `archetypes_so_far.json` snapshots and `run_metadata.json`.
- **Run artifacts**: `scripts/archetype_prompt_runner.py` writes aggregated payloads per scope (`run_*/archetypes_aggregated.json`). Run `python scripts/predefined_archetype_aggregator.py` to collapse every scope’s runs into `<predefined>/archetypes_combined.json` before meal generation.
- **Curator status**: `scripts/archetype_curator.py` is retained for historical reference but is no longer part of the default pipeline.
- **Next steps**: select the archetype UIDs you plan to ship from the combined file, generate meals, and package them into Parquet + manifest (Plan Step 6).

---

## 2. Yummi implementation layer

### Cart integration highlights
- Input contract: `{ retailer: "woolworths_co_za", items: [{ productId?, catalogRefId?, url?, qty, title }] }`.
- XHR payload: `deliveryType`, `fromDeliverySelectionPopup`, optional `address.placeId`, `storeId`, and array of `{ productId, catalogRefId, quantity }`.
- Session context captured from cookies (`userDelivery`, `location`, `storeId`); DOM automation only runs when XHR fails.
- Extension opens `https://www.woolworths.co.za/check-out/cart` upon completion while surfacing per-item status.
- Playwright/WebView runner mirrors the extension logic for QA demos and mobile automation experiments.

### Web scraper notes
- Discover Food categories after major site changes:  
  `python -m woolworths_scraper discover --output woolworths_scraper/config/categories.food.json --log-level INFO`
- Full scrape (refresh resolver catalog + data exports):  
  `python -m woolworths_scraper scrape --categories woolworths_scraper/config/categories.food.json --catalog-output resolver/catalog.json --log-level INFO`
  - Swap `--categories …` for `--auto-food` to re-discover inline.
  - Use `--limit <N>` during testing to clamp records.
  - Disable categories with `"enabled": false` in the JSON file.
- Discovery walks the Food navigation, filters promo-only nodes, and stores breadcrumb `path` arrays.
- Scraper paginates via `?No=<offset>`, extracts `productId`, `catalogRefId`, pricing, imagery, and breadcrumb path, and skips categories that repeatedly return HTTP 500.
- PDP enrichment (next iteration): parse `productInfo.multiAttributes` for pack sizes, nutrition, allergens, ingredients; merge into canonical records.
- Outputs feed `resolver/catalog.json`, ensuring cart fill always uses Woolworths IDs with URLs as supporting metadata.

### Ingredient cleanup + classification
- Heuristic filter CLI (`scripts/ingredient_cleanup.py`) + config (`data/catalog_filters.json`) strip obvious non-meal categories before LLM review. Current heuristics keep ready-meal branches while dropping beverages/household.
- Batch builder (`scripts/ingredient_batch_builder.py`) now slices the candidate set into JSON payloads (currently 1 SKU per batch to keep GPT-5 nano responses under limits) and records run metadata in `data/ingredients/llm_batches/manifest.json`.
- GPT classification runner (`scripts/ingredient_llm_classifier.py`) prompts `gpt-5-nano-2025-08-07` (fallback `gpt-5-mini-2025-08-07`) with `--max-output-tokens 5000`, logging progress per SKU and resuming automatically when interrupted. Responses live under `data/ingredients/llm_batches/responses/`.
- Consolidation script (`scripts/ingredient_classifications_builder.py`) merges `all_results.jsonl` into product-level tables (`data/ingredients/ingredient_classifications.{jsonl,csv}`) and a deduped ingredient catalog (`data/ingredients/unique_core_items.csv`, currently 1 844 unique ingredient/ready-meal rows) for downstream meal generation prompts.

### Meal generation + manifest publishing
- `scripts/meal_builder.py` now requires a predefined scope (`--predefined-dir data/archetypes/predefined/<slug>`) so each meal lives under `data/meals/<slug>/<archetype_uid>/` with run logs in `data/meals/runs/<slug>/`. Validation fills any missing required tags from the archetype defaults, enforces curated ingredient usage per UID, and writes the meal file immediately after the recipe call (with metadata noting whether SKU selection is pending/completed). Use `--archetype-uid all` to batch one meal per archetype once `archetypes_combined.json` + curated ingredients are present.
- The new aggregation CLI (`scripts/meal_aggregate_builder.py`) walks all per-archetype directories, normalizes tags, and emits a single manifest at `resolver/meals/meals_manifest.json` (and optional Parquet rows when `pyarrow` is installed). Each manifest carries `schema_version`, `manifest_id`, stats, warnings, and every archetype -> meal relationship required by the thin slice.
- FastAPI exposes `/v1/meals` and `/v1/meals/{archetype_uid}` off that manifest. Fly builds now bundle `resolver/meals/meals_manifest.json`, so the hosted server already returns live meal data (confirmed via `Invoke-RestMethod https://yummi-server-greenbean.fly.dev/v1/meals/arch_1k7p9f`).
- Remaining gap: generate meals for the archetype UIDs chosen from the aggregated runs and update the thin-slice UI/extension to call these endpoints instead of the local mocks.

### TODO focus areas
1. Refine category discovery filters (skip promo-only nodes), freeze canonical category list, and version it under source control.
2. Add PDP enrichment for pack size/specifications + nutritional metadata.
3. Sync enriched catalog into `resolver/catalog.json`; ensure extension prioritizes IDs from the catalog.
4. Promote PayFast staging settings into production, monitor ITN/ledger parity, and keep remote validation enforced (`PAYFAST_SKIP_REMOTE_VALIDATION=false`).
5. Operationalize the new chargeback/refund endpoints (alerting, UI surface, and automation hooks) per [Chargebacks.txt](Chargebacks.txt).
6. Extend wallet UX + automated test coverage so mobile surfaces transactions, errors, and negative balances clearly.
7. Wire the thin-slice app + Chrome extension to `/v1/meals*`, cache manifests client-side, and run an end-to-end thin-slice smoke test consuming the hosted meal data.

---

## 3. Root artifact audit

### Template-critical
| Path | Keep? | Notes |
|------|-------|-------|
| `thin-slice-app/` | ✅ | Expo client with Clerk + PayFast wiring; starting point for any new app. |
| `thin-slice-server/` | ✅ | Automation queue prototype; reuse patterns for future runners. |
| `yummi-server/` | ✅ | FastAPI backend with wallet + payments. |
| `extension/` | ✅ | MV3 Chrome extension foundations for any retailer cart fill. |
| `resolver/` | ✅ | Canonical catalog + resolver assets. |
| `woolworths_scraper/` | ✅ (if retailer scraping needed) | Provides scaffolding for catalog enrichment. |
| `data/`, `samples/` | ✅ | Example catalogs/payloads referenced by docs/tests. |
| `docker-compose.yml`, `fly.toml`, `env.*` | ✅ | Baseline infra configs; update values per environment. |
| `.md/.txt` docs listed above | ✅ | Serve as living runbooks/specs. |
| `scripts/wallet_admin_cli.py` | ✅ | Stopgap CLI for chargebacks/refunds before a dashboard exists. |

### Yummi implementation focus
| Path | Purpose |
|------|---------|
| `CartIntegration.md`, `interimcartintegration.md` | Working memories + test plans for Woolworths automation. |
| `Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt` | Reference spec from the original engagement. |
| `payfastmigration.md` | Production-readiness notes for PayFast (keep even when cloning). |
| `POC-Memory.md`, `thinslice.md`, `Phase 1 PRD.txt` | Capture context for resolver builds, thin-slice UX, and business requirements. |
| `state_detail.json`, `resolver/catalog.json` | Current dataset snapshots feeding the runner + extension. |

### Historical / scratch files
| Path | Recommendation |
|------|----------------|
| `LogoGPTOutput.docx`, `ServerGPTDiscussion.docx`, `ServerStartup.docx` | Archive references; keep outside the main template if you want a lean fork. |
| `Script Process.txt`, `ngrok codes.txt` | Operational scratch pads; consolidate into README/server docs if still relevant. |
| `scratch5.py` | Local experiment; delete or move under `samples/` when no longer needed. |
| `~$goGPTOutput.docx`, `~$rverStartup.docx` | Word temp lock files; can be deleted once Word is closed. |

---

## 4. Change management
- Update this guide alongside major workflow or tooling changes.
- Document new commands, schemas, and test expectations for quick onboarding.
- Version control: repository lives at `https://github.com/dreibeing/yummi.git`. Standard flow:
  ```powershell
  git add .
  git commit -m "Describe change"
  git push
  ```
  (First push already ran `git push -u origin main`; future pushes can just use `git push`.)

## 5. Immediate next steps
See [plan.md](plan.md) for the authoritative roadmap. Top priorities for the next coding session:
1. **Meal manifest hardening** — run `python scripts/predefined_archetype_aggregator.py` to refresh combined archetype files, finish generating meals for the archetype UIDs you plan to ship, rerun `scripts/meal_aggregate_builder.py` (with Parquet output + checksums), and document the release ID served from Fly.
2. **Thin-slice integration** — update the Expo thin slice (and extension if needed) to fetch `/v1/meals` + `/v1/meals/{uid}`, add client caching/invalidation, and run an end-to-end smoke test against staging.
3. **PayFast production rollout** — clone the hardened staging config into production Fly apps, keep `PAYFAST_SKIP_REMOTE_VALIDATION=false`, and add monitoring/alerts using the regression log in [payfastmigration.md](payfastmigration.md).
4. **Chargeback/refund groundwork** — design debit/negative-balance handling in backend services (refer to [Chargebacks.txt](Chargebacks.txt)).
5. **Wallet UI polish** — expand thin-slice UI to show full transaction history and flag negative balances before chargeback logic hardens.
6. **Preference-driven runtime** — plug the newly stored `user_preference_profiles` into meal filtering and runner logic so `/v1/meals*` can honor saved diet/allergen tags per user.

### Archetype generation workflow (2025-02 update)
- `data/archetypes/predefined_archetypes.xlsx` (or the CSV export) now enumerates every hard-scope combination (DietaryRestrictions × Audience × secondary DietaryRestrictions).
- Run `python scripts/predefined_archetypes_sync.py` to materialize `data/archetypes/predefined/<diet>_<audience>_<secondary>/config.json` for each row; re-run whenever the sheet changes.
- Invoke scoped generation per folder using `scripts/archetype_prompt_runner.py --predefined-config ...`. The curator CLI is deprecated; review runs manually if additional QA is needed.
- After each wave of runs, execute `python scripts/predefined_archetype_aggregator.py` so `<predefined>/archetypes_combined.json` is ready for meal generation.
- The prompt runner now makes one GPT-5 call per archetype. Use `--archetype-count N` to create N sequential archetypes, optionally limiting existing-context size via `--context-summary-max`. `--max-output-tokens` and `--reasoning-effort` still pass straight through to the OpenAI Responses API (requires `openai>=1.3.0`).
- Prompts embed an "Approved Tags" appendix derived from `defined_tags.json`, and responses are validated so every category/value matches the manifest; scope audiences/diets must be included exactly.
- `Allergens` is optional—default to the new `None` value unless the archetype is explicitly allergen-focused.
