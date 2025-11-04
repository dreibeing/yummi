# Yummi Build Plan

## Phase 0 – Platform hardening
- ✅ Provision managed Postgres guidance (Fly) and add Alembic migrations so schema changes are reproducible (2025-11-04).
- ✅ Enable structured logging + metrics exporters (Prometheus, Sentry breadcrumbs) for both local and Fly environments (2025-11-04).
- Add startup validation that fails fast when mandatory secrets (Clerk, Stripe, Redis, OpenAI) are missing.

## Phase 1 – Authentication & access control
- Enable Clerk verification in FastAPI (`AUTH_DISABLE_VERIFICATION=false`) with real issuer, JWKS, and audience.
- Store Clerk user records in Postgres; build sync logic and role assignments.
- Require auth on thin-slice endpoints (shared token or role) to prevent anonymous use.

## Phase 2 – Persistent data + background jobs
- Move thin-slice orders/catalog state into Postgres/Redis with models and indexing.
- Implement ingestion and worker processes for dataset uploads, order claims, and cart fill retries.
- Introduce queue monitoring endpoints and admin tooling.

## Phase 3 – Billing & monetization
- Integrate Stripe customer linkage, subscription management, and payment-intent flows.
- Handle Stripe webhooks (invoice paid/failed) and enforcement of subscription status on API usage.
- Surface billing status in the app and admin dashboards.

## Phase 4 – Business logic & AI features
- Implement OpenAI-based assistants with server-owned keys, per-user quotas, and usage logging.
- Extend catalog enrichment and selection logic; add recommendation/personalization features.
- Harden cart-fill automation (retry logic, analytics, alerts).

## Phase 5 – Productization & QA
- Build integration + E2E tests (CI pipeline) covering auth, payments, orders, and AI flows.
- Establish staging environment mirroring Fly prod; add blue/green or rolling deploy strategy.
- Conduct security review (rate limiting, secret rotation, audit logs) and prepare launch checklist.
