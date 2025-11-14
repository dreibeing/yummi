# Thin-Slice Auth + Payments Template

This repository is a production-ready thin slice you can copy whenever you need a mobile app with secure authentication, a FastAPI backend, and PayFast-backed wallet top-ups. It keeps the Yummi/Woolworths implementation in place for reference while giving you a clean blueprint for new apps.

## Quick start for a new project
1. **Clone & install**
   ```bash
   git clone https://github.com/dreibeing/yummi.git my-app
   cd my-app
   python -m venv .venv && source .venv/bin/activate
   pip install -r yummi-server/requirements.txt
   npm install --prefix thin-slice-app
   ```
2. **Configure environment**
   - Copy `env.staging` or `env.prod` to `.env` for local Docker runs.
   - Create `thin-slice-app/.env` with `EXPO_PUBLIC_CLERK_PUBLISHABLE_KEY`, `EXPO_PUBLIC_API_BASE_URL`, and `EXPO_PUBLIC_PAYFAST_MODE`.
3. **Run the stack**
   ```bash
   docker compose up -d --build       # Postgres, Redis, FastAPI (localhost:8000)
   npm run start --prefix thin-slice-app
   ```
   Use Expo Go or an emulator pointed at the Metro URL and confirm `/v1/health` responds.
4. **Deploy**
   - Follow `server.md#flyio-deployment` to push the FastAPI app to Fly.io (or reuse your own platform).
   - Wire PayFast production credentials only after the sandbox flow and ITN logs in `payfastmigration.md` are green.

## Repository layout at a glance
| Path | Category | Notes |
|------|----------|-------|
| `thin-slice-app/` | Template | Expo Router client with Clerk auth + wallet UI (see `yummi_scaffold_spec.md`). |
| `yummi-server/` | Template | FastAPI backend with PayFast endpoints, wallet ledger, migrations, and Fly/Docker configs (`server.md`). |
| `thin-slice-server/` | Template | Lightweight queue prototype for the WebView runner; use as a pattern for future automation services. |
| `extension/` | Shared | MV3 Chrome extension that talks to the server and fills retailer carts. |
| `resolver/` | Shared | Catalog + resolver assets consumed by the server/extension. |
| `woolworths_scraper/` | Yummi-specific | Python tooling that discovers categories and builds Woolworths catalogs. |
| `data/`, `samples/` | Shared | Example catalogs, payloads, and reference datasets for thin-slice testing. |
| `docker-compose.yml`, `fly.toml`, `env.*` | Template | Infrastructure defaults for local and Fly deployments. |

Keep Yummi-specific directories (scraper, Woolworths agent brief, cart integration docs) for reference, but feel free to fork the template directories unmodified for other products.

## Template building blocks
### Mobile scaffold (`thin-slice-app`)
- Clerk-provided OAuth (Google, Apple, Facebook) with session persistence and protected routes.
- Wallet screen that calls `/v1/wallet/balance`, launches PayFast checkout, and refreshes on return.
- Deep-link bridges (`yummi://payfast/{return,cancel}`) that Expo handles automatically.
- See `yummi_scaffold_spec.md` for provider setup, routing, and environment variables.

### Backend (`yummi-server`)
- FastAPI + Postgres + Redis with Alembic migrations.
- `/v1/payments/payfast/{initiate,status,itn}` endpoints that sign requests, parse ITN webhooks, and update the wallet ledger.
- `/v1/wallet/*` + `/v1/me` for account balances, plus thin-slice routes under `/v1/thin/*`.
- Docker Compose + Fly runbooks, logging, and PayFast-specific operational guidance (see `server.md` + `payfastmigration.md`).

### Automation & data helpers
- `extension/`: MV3 service worker + popup for browser-assisted cart filling. Ideal starting point if a future retailer requires Chrome automation.
- `thin-slice-server/`: queue + runner interfaces used by the thin-slice mobile/WebView flow.
- `resolver/` + `woolworths_scraper/`: scripts that curate retailer catalogs and attach `productId/catalogRefId` metadata before cart fill.
- Ingredient cleanup pipeline: `scripts/ingredient_cleanup.py` (heuristics) → `scripts/ingredient_batch_builder.py` (LLM payloads) → `scripts/ingredient_llm_classifier.py` (model pass). Use `gpt-5-nano-2025-08-07` by default for cost efficiency; bump to `gpt-5-mini-2025-08-07` if nano truncates. Best seen working command (batch size 1) is `python scripts/ingredient_llm_classifier.py --max-batches 50 --max-output-tokens 5000 --overwrite`; reruns without `--overwrite` resume from the next unfinished batch.
- `scripts/wallet_admin_cli.py`: stopgap CLI for recording chargebacks or moderating refunds before a UI exists.

## Using the template for another app
1. **Rename packages** – update Expo slug, bundle IDs, and Android package (see sample config in `yummi_scaffold_spec.md` §7).
2. **Swap auth provider keys** – generate your own Clerk instance (or swap in Auth0/Supabase) and update the publishable/secret keys in `.env` + Expo config.
3. **Configure payments** – PayFast sandbox credentials live in `env.*`; replace with your merchant info or adapt the API layer to another PSP using the same wallet contract.
4. **Adjust backend routes** – extend `yummi-server/app/routes/` with new business logic while keeping `/v1/payments/*` and `/v1/wallet/*` untouched for future reuse.
5. **Decide on automation** – keep the Woolworths extension as a reference, or duplicate its structure for another retailer with your own resolver/catalog.

## Yummi implementation references
- Woolworths cart automation: `CartIntegration.md`, `interimcartintegration.md`, and `Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt`.
- Resolver + scraper history: `POC-Memory.md`, `woolworths_scraper/README.md`, and datasets under `data/`.
- PayFast production readiness: `payfastmigration.md`, `server.md`, and `Chargebacks.txt`.
- Thin-slice UX experiments: `thinslice.md` and `thin-slice-server/`.

## Documentation map
| File | Scope | Template vs. Yummi |
|------|-------|--------------------|
| `README.md` | Quickstart + repo layout + how to reuse the template. | Template |
| `thisproject.md` | Deep project guide, status board, and TODOs. | Template + Yummi |
| `plan.md` | Roadmap milestones and immediate priorities. | Template |
| `server.md` | FastAPI runbook, Docker/Fly deployment, observability. | Template |
| `yummi_scaffold_spec.md` | Mobile auth + payments scaffold. | Template |
| `payfastmigration.md` | PayFast rollout logs, ITN notes, operations checklist. | Template |
| `CartIntegration.md` | Woolworths cart automation plan/tests. | Yummi |
| `interimcartintegration.md` | Additional Woolworths-specific notes. | Yummi |
| `Woolworths Basket Integration — Implementation & Test Plan (Agent Brief).txt` | Original agent brief for the retailer integration. | Yummi |
| `Chargebacks.txt` | Wallet refund/chargeback policy. | Template |
| `POC-Memory.md`, `Phase 1 PRD.txt`, `Script Process.txt` | Historical context + decision logs. | Yummi |

Use this table to decide what to keep when cloning—for a fresh app copy over everything in the Template column, then selectively bring Yummi references if they still help.
