# Yummi Server Plan & TODO

## Goals & Scope
- Production-ready backend for Yummi and thin-slice prototype.
- Record user info (auth via Clerk), receive data from app, serve catalog and basket/cart endpoints.
- Accept uploads of precomputed data (resolver/catalog.json and future enriched datasets) with versioning.
- Run application/business logic (catalog selection, basket queueing, OpenAI-assisted features).
- Securely manage secrets and safely call the OpenAI API (server-owned and optional user-provided keys).
- Operate reliably with logging, metrics, rate limiting, and CI/CD.

## Operational Runbook

### Local development
- Start everything with `docker compose up -d --build` from repo root; this brings up Postgres, Redis, and the FastAPI app on `http://localhost:8000`. Auth verification is disabled by default in the Compose stack so Clerk isn’t required during local development (set `AUTH_DISABLE_VERIFICATION=false` + Clerk issuer/audience to exercise full verification).
- Quick health check: `curl http://localhost:8000/v1/health` or `curl http://localhost:8000/v1/thin/health`. Catalog lives under `/v1/catalog`; thin slice clients hit `/v1/thin/*`.
- Follow live logs with `docker compose logs -f yummi-server`. Thin slice runner logs are persisted inside the container at `/app/data/thin-runner-log.txt`; view via `docker compose exec yummi-server tail -n 50 data/thin-runner-log.txt`.
- Whenever the schema changes, apply migrations with `docker compose run --rm yummi-server alembic upgrade head`. Generate a new migration via `docker compose run --rm yummi-server alembic revision --autogenerate -m "describe change"`.
- Structured logs default to JSON; flip to console mode with `LOG_JSON=false` (and tweak verbosity via `LOG_LEVEL=DEBUG`) in `.env` when developing locally.
- Non-dev environments will refuse to start unless `REDIS_URL`, `OPENAI_API_KEY`, PayFast merchant credentials (`PAYFAST_MERCHANT_ID`/`PAYFAST_MERCHANT_KEY`) and notify/return URLs are set. Keep `ENVIRONMENT=dev` in local `.env` if you want to bypass the strict checks.
- PayFast ITN form parsing requires `python-multipart`; `pip install -r yummi-server/requirements.txt` (or rebuild the Docker image) whenever that dependency changes so `/payments/payfast/itn` keeps accepting multipart/form-data.
- Expo thin-slice app defaults to `http://10.0.2.2:8000/v1/thin` on Android. Launch emulator through Android Studio’s Device Manager, then run `npx expo start --android` from `thin-slice-app` in Windows PowerShell (not WSL). Override the API by creating `.env` with `EXPO_PUBLIC_THIN_SLICE_SERVER_URL=...` (see `.env.example`); release builds fall back to the Fly URL automatically.
- Common fixes: delete stale secrets in `.env`, ensure Redis is reachable (`docker compose exec redis redis-cli ping`), and rebuild images if requirements change. Only set `AUTH_DISABLE_VERIFICATION=true` temporarily when debugging local auth issues.

### Fly.io deployment
- Install CLI (`iwr https://fly.io/install.ps1 -useb | iex`), then `fly auth login`.
- Provision managed Postgres: `fly postgres create --name yummi-server-db --org personal --region jnb --initial-cluster-size 1`. Attach it to the app with `fly postgres attach --app yummi-server-YOURNAME yummi-server-db` (this sets `DATABASE_URL`/`DATABASE_URL_INTERNAL` secrets automatically).
- Provision Redis once: `fly redis create --name yummi-redis --org personal --region jnb` (no eviction). Capture the URL.
- Required secrets (CSV lists are plain comma-separated strings; JSON arrays must be valid JSON):
  ```
  fly secrets set ^
    REDIS_URL="redis://default:...@fly-yummi-redis.upstash.io:6379" ^
    OPENAI_API_KEY="sk-live-or-test" ^
    AUTH_DISABLE_VERIFICATION="false" ^
    CLERK_ISSUER="https://clerk.your-instance.com" ^
    CLERK_AUDIENCE="yummi-mobile" ^
    ENVIRONMENT="staging" ^
    THIN_SLICE_ENABLED="true" ^
    THIN_RUNNER_LOG_PATH="/app/data/thin-runner-log.txt" ^
    PAYFAST_MERCHANT_ID="1234567" ^
    PAYFAST_MERCHANT_KEY="abc123" ^
    PAYFAST_PASSPHRASE="optional-passphrase" ^
    PAYFAST_NOTIFY_URL="https://yummi-server-YOURNAME.fly.dev/payments/payfast/itn" ^
    PAYFAST_RETURN_URL="https://yummi.app/payfast/return" ^
    PAYFAST_CANCEL_URL="https://yummi.app/payfast/cancel"
  ```
  Optional later: `CORS_ALLOWED_ORIGINS=https://yourdomain.com,http://localhost:19006` and `ADMIN_EMAILS=alice@example.com,bob@example.com`. If pydantic throws `error parsing value`, remove the offending secret with `fly secrets unset <NAME>`.
- Postgres connections: attaching a Fly Postgres instance sets `DATABASE_URL` automatically. The server now normalizes anything that starts with `postgres://`/`postgresql://` into `postgresql+asyncpg://...?...ssl=disable`, so you no longer need to rewrite secrets manually for asyncpg compatibility.
- Switch `PAYFAST_MODE=live` only after production credentials are enabled; sandbox is assumed when omitted.
- Run migrations after each deploy: `fly ssh console -a yummi-server-YOURNAME -C "cd /app && alembic upgrade head"`. The container image bundles Alembic so the same command works locally and remotely.
- Wire up Sentry (optional): set `SENTRY_DSN=...` and `SENTRY_TRACES_SAMPLE_RATE=0.1` (or similar) via `fly secrets set` to capture errors + breadcrumbs. Leave unset to disable.
- Deploy from repo root: `fly deploy`. Inspect rollout at `https://fly.io/apps/yummi-server-greenbean/monitoring`.
- Post-deploy checks:
  - `fly status -a yummi-server-greenbean` (machines should be `started`).
  - `fly logs -a yummi-server-greenbean --no-tail` (look for “Application startup complete”).
  - `curl https://yummi-server-greenbean.fly.dev/v1/thin/health`.
- Troubleshooting:
  - If machines show `stopped` with exit code 1, run `fly logs` to spot misconfigured secrets or missing dependencies.
  - Restart after fixes via `fly apps restart yummi-server-greenbean` or `fly machine start <id>`.
  - For persistent storage of thin-slice logs, create a Fly volume and mount it at `/app/data` (update `fly.toml` accordingly).

## Context (from repo)
- Mobile app: Expo/React Native with Clerk auth and PayFast hosted checkout (per yummi_scaffold_spec.md).
- Thin-slice: minimal flow for Fetch Products, Build Basket, Place Order using resolver/catalog.json (thinslice.md).
- Data pipeline: woolworths_scraper produces resolver/catalog.json and future enrichments (thisproject.md).

## High-Level Architecture
- API: FastAPI (Python) for alignment with data tooling, async I/O, Pydantic validation, OpenAPI docs.
- Auth: Clerk JWT verification middleware; server trusts user identity from Clerk session tokens.
- DB: PostgreSQL (users, datasets, products index, orders/queues, api_credentials); Redis for rate limits/queues.
- Storage: S3-compatible bucket (e.g., Cloudflare R2) for uploaded dataset files; checksum + version metadata in DB.
- Secrets: KMS/Keyring (cloud-native) with envelope encryption; dev fallback via AES-GCM using server secret.
- Background: Worker (RQ/Celery or APScheduler) for order processing, OpenAI tasks, dataset ingestion.
- Observability: Structured logs (JSON), traces (OTel), metrics (Prometheus/OpenTelemetry), error tracking (Sentry).
- Deployment: Docker + Fly.io/Render/Railway (simple) or AWS (ECS/Fargate + RDS + ElastiCache + S3) with IaC (Terraform).

## Data Model (initial)
- users: id, clerk_user_id, email, created_at, updated_at.
- api_credentials: id, user_id, provider, label, enc_key_blob, created_at, last_used_at.
- datasets: id, name, version, status, source, notes, created_by, created_at.
- dataset_files: id, dataset_id, object_key, checksum_sha256, size_bytes, uploaded_at.
- products: id, retailer, product_id, catalog_ref_id, title, url, price, payload_jsonb, dataset_id, created_at.
- orders: id, user_id, status, items_jsonb, retailer, notes, created_at, updated_at.
- order_events: id, order_id, type, payload_jsonb, created_at.
- payments: id (uuid), provider, provider_reference, user_id, user_email, amount_minor, currency, status, checkout_payload_jsonb, last_itn_payload_jsonb, created_at, updated_at.
- wallet_transactions: id (uuid), user_id, payment_id (fk), amount_minor, currency, entry_type, note, created_at.

## API Surface (v1, initial)
- GET /v1/health -> liveness, version, pending_counts.
- GET /v1/me -> current user (Clerk-verified) profile from DB.
- GET /v1/catalog -> list products; query: retailer, limit, random, dataset_version.
- GET /v1/products/{id} -> product detail.
- POST /v1/orders -> create basket/order queue; body: items[], retailer; returns order_id.
- GET /v1/orders/{id} -> status + progress events.
- POST /v1/orders/{id}/ack -> finalize (used by runner/worker).
- POST /v1/payments/payfast/initiate -> returns PayFast hosted checkout fields + reference and logs the canonical signature payload (copy/paste directly into PayFast’s tester when debugging).
- POST /v1/payments/payfast/itn -> webhook endpoint for PayFast Instant Transaction Notifications.
- GET /v1/payments/payfast/status?reference= -> returns payment + wallet status (PayFast status string, wallet credit flag, timestamps) so clients can poll after checkout.
- GET /v1/payments/payfast/return-bridge and `/cancel-bridge` -> HTTPS bridge pages that immediately redirect back into the Expo deep links so PayFast’s sandbox (which requires HTTPS) can return to `yummi://payfast/*`.
- GET /v1/wallet/balance -> current wallet balance + transactions for the authenticated user.
- Admin (guarded by role/claims):
  - POST /admin/datasets -> begin dataset version (metadata); returns upload URL(s) or direct JSON ingestion.
  - POST /admin/datasets/{id}/complete -> finalize + index products.
  - POST /admin/catalog/import -> upload resolver/catalog.json (small) directly; server stores and indexes.
  - POST /v1/admin/catalog/import -> thin-slice Redis-backed import (implemented).
  - GET /v1/admin/catalog/source -> indicates active source and item count (implemented).
- OpenAI features:
  - POST /ai/complete -> run server-owned key; optional user_key_id to use user’s stored credential.
  - POST /ai/tools/* -> future tool-calls; rate limited; logs cost per user.

## Security & Compliance
- AuthZ: Clerk JWT verification; require valid bearer token for non-public endpoints; role claims for admin routes.
- Rate limiting: per-user and per-IP (Redis-backed leaky bucket); separate limits for /ai/* endpoints.
- Input validation: Pydantic schemas, size limits on uploads, JSON schema for items[].
- CORS: restrict to mobile app & admin domain; preflight caching; secure cookies off (Bearer only).
- Secrets: use cloud KMS for encryption-at-rest; rotate envelope key quarterly; audit access.
- Data: encrypt sensitive fields (api_credentials.enc_key_blob) with AES-256-GCM; store KMS-wrapped DEKs;
  redact in logs; implement right-to-erasure for PII.
- Transport: HTTPS everywhere (HSTS), TLS 1.2+; disable weak ciphers.
- Headers: security headers (Content-Security-Policy for admin UI, X-Content-Type-Options, Referrer-Policy),
  JSON-only API.
- Idempotency: Idempotency-Key on POST /orders and /ai/* to protect against retries.

## Precomputed Data Ingestion
- Small path: POST /admin/catalog/import with resolver/catalog.json (<= 25MB). Validate schema, compute checksum,
  store as dataset + index minimal fields into products table with JSONB payload.
- Large path: multipart upload to S3/R2 then POST /admin/datasets/{id}/complete to trigger background indexing.
- Versioning: datasets.version string (e.g., 2025-10-22T12:00Z); products link to dataset_id for reproducibility.
- Rollback: keep previous dataset version; feature flag to select active dataset.

Initial thin-slice behavior implemented now:
- POST /v1/admin/catalog/import stores the catalog JSON into Redis (key `catalog:data`).
- GET /v1/catalog prefers Redis dataset; falls back to file `resolver/catalog.json`.
- Admin protection uses `ADMIN_EMAILS` env; in non-dev envs, only emails listed may call admin routes.

## OpenAI Integration
- Server-owned key: store only in secret manager; never expose to clients.
- Optional user-provided keys: encrypted at rest; per-call decryption with KMS-unwrapped DEK; scope usage
  to the owner user_id; show usage and spend limits; enforce model allowlist.
- SDK: official OpenAI SDK; retry with backoff; log prompt/response hashes (not raw content) + token usage.

## Client Integration
- Thin-slice endpoints: GET /v1/catalog?limit=100&random=true, POST /v1/orders for queueing.
- Mobile app: Clerk bearer token on each request; PayFast checkout posts directly to their hosted form (handled outside this doc). Wallet balance fetched via `/v1/wallet/balance` and also included in `/v1/me` responses.
- Extension/web runner: uses /orders/next, /orders/{id}/ack for processing (optional if kept for desktop parity).

## Environments & Deployment
- Envs: dev (local Docker), staging, prod.
- DB migrations: Alembic; gated deploy (migrate before rollout); backups daily; PITR if managed service.
- Deploy: Docker image; CI -> build, test, scan, push; CD -> staging (manual approve) -> prod.
- Secrets: managed per env (Fly secrets / Render env vars / AWS SSM + KMS). No secrets in repo.

## Observability
- Logs: JSON to stdout; shipping via platform; correlation IDs.
- Metrics: request counts, latency, errors, queue depth, OpenAI tokens/cost; /metrics endpoint.
- Traces: OTel auto-instrumentation (FastAPI, DB, Redis, HTTP);
- Errors: Sentry with PII scrubbing; alerting thresholds.

## Testing Strategy
- Unit tests for schemas, services, crypto envelope, OpenAI wrapper.
- Integration tests for /catalog and /orders using a test DB.
- Contract tests for mobile client (OpenAPI schema + example fixtures).
- Load test: /catalog and /orders with realistic sizes; rate-limit behaviors.

## Rollout Plan
1) Scaffold FastAPI service with auth middleware, health endpoint.
2) Add DB models, migrations; wire users + api_credentials.
3) Implement /catalog and /orders (thin-slice support), then admin dataset import.
4) Integrate secrets manager and OpenAI wrapper with rate limits + idempotency.
5) Add observability + CI/CD; deploy to staging; run smoke tests.
6) Harden security (headers, CORS, backups), then promote to prod.

## TODO (Phased)
- Foundation
  - [ ] Create FastAPI project, Dockerfile, compose (db, redis).
  - [ ] Add Clerk JWT verification middleware and /me.
  - [ ] Set up Postgres + Alembic; define initial schema.
  - [ ] Health + metrics endpoints.
- Catalog & Orders
  - [ ] Implement GET /catalog (random, limit) from products table.
  - [ ] Implement POST /orders with idempotency + validation.
  - [ ] Implement GET /orders/{id} and POST /orders/{id}/ack.
  - [ ] Admin: POST /admin/catalog/import (direct JSON) + indexing job.
- Secrets & OpenAI
  - [ ] Integrate cloud KMS (or local AES-GCM) envelope encryption.
  - [ ] api_credentials CRUD (create/list/delete) for user-owned keys.
  - [ ] OpenAI client wrapper with model allowlist and per-user limits.
- Ops & Security
  - [ ] Rate limiting middleware (per-user/IP; stricter for /ai/*).
  - [ ] Structured logging, tracing, error reporting (Sentry).
  - [ ] CI pipeline: tests, lint, build, container scan.
  - [ ] CD to staging + prod with migrations.
  - [ ] Backups and disaster recovery runbook.
- Validation
  - [ ] Thin-slice e2e: app -> /catalog -> /orders.
  - [ ] Load test and rate-limit verification.
  - [ ] Security review and secrets rotation drill.

## Open Questions / Assumptions
- Are user-provided OpenAI keys required, or server-owned only? (Plan supports both.)
- Preferred cloud: Fly/Render/Railway for speed, or AWS for control?
- Do we keep MV3 extension runner long-term, or use server-side XHR via same-origin cookies (likely not feasible)?
- PayFast payments are handled via the scaffolded service; share auth/DB or stay isolated?
- Chargebacks/refunds: follow `Chargebacks.txt` policy (allow negative balances, block new debits, log audit data).

## Additional Considerations (not to forget)
- API versioning (v1) and deprecation policy.
- Request/response size limits and gzip/brotli compression.
- Pagination for catalog endpoints; search/indexing (pg_trgm or Meilisearch later).
- Feature flags for dataset version selection and AI features.
- GDPR: data export and deletion for users.
- Webhooks signing (if any inbound webhooks introduced later).

## Cloud Hosting & Deployment
- Target: Fly.io (containerized, regional Postgres/Redis options). Alternative: AWS ECS/Fargate + RDS + ElastiCache + S3.
- Artifacts added:
  - Docker image build context: `yummi-server/` with `Dockerfile` (serves on `:8000`).
  - Compose for local dev: `docker-compose.yml` (FastAPI + Postgres + Redis).
  - Fly config: `fly.toml` (HTTP service on `:8000`).
  - GitHub Action: `.github/workflows/deploy-fly.yml` (requires `FLY_API_TOKEN`).
- Deploy steps (Fly.io):
  1) Install Fly CLI and create app: `flyctl launch` (or use provided `fly.toml`).
  2) Provision Postgres/Redis (optional): `flyctl postgres create`, Redis via Upstash add-on or external.
  3) Set secrets: `flyctl secrets set CLERK_ISSUER=... CLERK_AUDIENCE=... OPENAI_API_KEY=... CORS_ALLOWED_ORIGINS=... ADMIN_EMAILS=you@example.com`
  4) Deploy: `flyctl deploy` or via GitHub Action with `FLY_API_TOKEN` secret.
  5) Verify: `GET /health`, `GET /metrics`, and thin-slice flow `GET /catalog` + `POST /orders`.

Note: The server is designed to run entirely in the cloud; no dependency on a local machine beyond development. Catalog can be baked into the image (we copy `resolver/catalog.json`) or uploaded via admin APIs.

## Bringup Checklist (Now)
- Local dev restart (post reboot)
  - Launch Docker Desktop and wait for “Engine running”.
  - In repo root `projects\yummi`, run `docker compose up yummi-server db redis` (add `--build` only if dependencies changed).
  - Keep that terminal open; once logs show `Uvicorn running on http://0.0.0.0:8000`, open a second prompt for API checks.
  - Verify health: `curl http://localhost:8000/v1/health` should return status `ok`.
  - If the catalog was cleared, regenerate the unsigned dev JWT (PowerShell snippet below) and POST `resolver/catalog.json` to `/v1/admin/catalog/import`, then confirm with `curl http://localhost:8000/v1/catalog?limit=5`.

- Fly app name
  - Choose a unique Fly app name and set it in `fly.toml` (`app = "yummi-server-YOURNAME"`).
- Deploy (staging, auth enabled)
  - `flyctl launch --copy-config --name yummi-server-YOURNAME` (accept defaults)
  - `flyctl secrets set AUTH_DISABLE_VERIFICATION=false CLERK_ISSUER=https://clerk.your-instance.com CLERK_AUDIENCE=yummi-mobile CORS_ALLOWED_ORIGINS=* ADMIN_EMAILS=you@example.com`
  - (Optional) Provision Redis for admin import: Upstash or external; set `REDIS_URL=...` via `flyctl secrets set`
  - Deploy: `flyctl deploy`
  - Smoke test: `curl https://yummi-server-YOURNAME.fly.dev/v1/health`
- Push dataset (thin-slice)
  - (Optional) Generate a dev JWT if you temporarily disabled verification (`AUTH_DISABLE_VERIFICATION=true`):
    - Windows PowerShell: `py -c "import json,base64; hdr=b'{\"alg\":\"none\",\"typ\":\"JWT\"}'; p=b'{\"sub\":\"devuser\",\"email\":\"you@example.com\"}'; b64=lambda b: base64.urlsafe_b64encode(b).rstrip(b'='); print((b64(hdr)+b'.'+b64(p)+b'.').decode())"`
  - Import: `curl -X POST -H "Authorization: Bearer <DEV_JWT>" -H "Content-Type: application/json" --data @resolver/catalog.json https://yummi-server-YOURNAME.fly.dev/v1/admin/catalog/import`
  - Verify: `curl https://yummi-server-YOURNAME.fly.dev/v1/catalog?limit=5`
- Connect the app
  - Set `EXPO_PUBLIC_API_BASE_URL=https://yummi-server-YOURNAME.fly.dev` in your Expo config.
  - Wire thin-slice "Fetch Products" to `GET /v1/catalog` and "Place Order" to `POST /v1/orders` with Clerk bearer when you re-enable auth.
- Sandbox PayFast QA
  - Populate `PAYFAST_*` secrets with sandbox credentials and keep `ENVIRONMENT=dev` only when running behind ngrok (remote ITN validation is skipped in dev, enforced elsewhere).
  - Start ngrok (`ngrok http 8000`), set the resulting HTTPS URL in `PAYFAST_NOTIFY_URL`, `PAYFAST_RETURN_URL`, and `PAYFAST_CANCEL_URL`, then rebuild/restart so the tunnel is baked into settings.
  - From the thin-slice app, trigger a wallet top-up; confirm `/v1/payments/payfast/initiate` returns the sandbox host, submit the hosted checkout, and watch `docker compose logs -f yummi-server` (or `flyctl logs`) for ITN delivery (`POST /payments/payfast/itn`).
  - After a successful ITN, copy the reference, ngrok URL, and log excerpt into [payfastmigration.md](payfastmigration.md#43-operations--security) so future regression runs have a checklist.
- Harden for production
  - Keep `AUTH_DISABLE_VERIFICATION=false` with proper Clerk settings: `CLERK_ISSUER`, `CLERK_AUDIENCE`.
  - Set `CORS_ALLOWED_ORIGINS` to your app’s web/admin origin(s) only.
  - Add Postgres + Alembic migrations and switch orders to DB-backed persistence.

### Current status (2025-10-23)
- Local Docker image (`yummi-server/Dockerfile`) builds and runs successfully. `/v1/health` returns `{"status":"ok"}` via `docker run -p 8000:8000 yummi-server-local`.
- `/v1/catalog` currently returns `[]` until the admin import endpoint receives data (tested locally).
- Remote deploy via Fly.io:
  - App scaffolding created (`yummi-server-greenbean`), secrets staged.
  - Remote builds using Depot fail because BuildKit uses repo root context; attempts to force context/disable BuildKit hit parsing issues with `flyctl deploy --local-only`.
  - Best known next step: set `DOCKER_BUILDKIT=0` without trailing space using `cmd /c "set \"DOCKER_BUILDKIT=0\" && \"%USERPROFILE%\\.fly\\bin\\flyctl.exe\" deploy --local-only"`. If that still fails, fall back to `flyctl deploy --local-only --no-cache` after ensuring Fly CLI points at `.`, or push the locally tagged image via `docker tag yummi-server-local registry.fly.io/yummi-server-greenbean:manual && docker push ... && flyctl deploy --image registry.fly.io/yummi-server-greenbean:manual`.
- Catalog upload and client wiring are pending until the cloud deploy stays healthy.
