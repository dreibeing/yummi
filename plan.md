# Yummi Build Plan

## Quick Links
- Product guide: [thisproject.md](thisproject.md)
- Backend roadmap & runbook: [server.md](server.md)
- Mobile scaffold details: [yummi_scaffold_spec.md](yummi_scaffold_spec.md)
- PayFast integration notes: [payfastmigration.md](payfastmigration.md)
- Chargebacks & refunds policy: [Chargebacks.txt](Chargebacks.txt)
- Thin-slice client entry point: [`thin-slice-app/App.js`](thin-slice-app/App.js)

## Current Snapshot (2025-11-04)
- PayFast initiate + ITN endpoints live with wallet ledger persistence (see [yummi-server/app/routes/payfast.py](yummi-server/app/routes/payfast.py) and [yummi-server/app/routes/wallet.py](yummi-server/app/routes/wallet.py)).
- `/v1/me` returns wallet data; `/v1/wallet/balance` exposes recent transactions (ledger modeled in [yummi-server/app/models.py](yummi-server/app/models.py)).
- Thin-slice Expo client shows wallet balance, triggers PayFast checkout, and refreshes ledger after return ([thin-slice-app/App.js](thin-slice-app/App.js)).
- Observability, migrations, and Fly deployment instructions consolidated in [server.md](server.md).

## Phase 0 – Platform hardening
- ✅ Provision managed Postgres guidance (Fly) and add Alembic migrations so schema changes are reproducible (2025-11-04).
- ✅ Enable structured logging + metrics exporters (Prometheus, Sentry breadcrumbs) for both local and Fly environments (2025-11-04).
- ✅ Add startup validation that fails fast when mandatory secrets (Clerk, PayFast, Redis, OpenAI) are missing (2025-11-04).

## Phase 1 – Authentication & access control
- Enable Clerk verification in FastAPI (`AUTH_DISABLE_VERIFICATION=false`) with real issuer, JWKS, and audience.
- Store Clerk user records in Postgres; build sync logic and role assignments.
- Require auth on thin-slice endpoints (shared token or role) to prevent anonymous use.

## Phase 2 – Persistent data + background jobs
- Move thin-slice orders/catalog state into Postgres/Redis with models and indexing.
- Implement ingestion and worker processes for dataset uploads, order claims, and cart fill retries.
- Introduce queue monitoring endpoints and admin tooling.

## Phase 3 – Billing & monetization
- Integrate PayFast wallet top-ups and, if needed, subscriptions/adhoc agreements.
- Handle PayFast ITN/PDT flows and enforce payment status on API usage.
- Surface billing status in the app and admin dashboards.
- Implement chargeback/refund workflows per `Chargebacks.txt` (negative balance handling, refund limits, abuse review).

## Phase 4 – Business logic & AI features
- Implement OpenAI-based assistants with server-owned keys, per-user quotas, and usage logging.
- Extend catalog enrichment and selection logic; add recommendation/personalization features.
- Harden cart-fill automation (retry logic, analytics, alerts).

## Phase 5 – Productization & QA
- Build integration + E2E tests (CI pipeline) covering auth, payments, orders, and AI flows.
- Establish staging environment mirroring Fly prod; add blue/green or rolling deploy strategy.
- Conduct security review (rate limiting, secret rotation, audit logs) and prepare launch checklist.

## Immediate Next Steps
1. **Sandbox PayFast QA**  
   - Use the new sandbox merchant credentials to run a full wallet top-up via the thin-slice app, confirm the ITN updates wallet balances, and record findings in [payfastmigration.md](payfastmigration.md#43-operations--security).  
   - Add a repeatable checklist (amounts, reference IDs, ITN resend steps) for future regression passes.
2. **Chargeback/refund groundwork**  
   - Design debit/chargeback flow using guidance in [Chargebacks.txt](Chargebacks.txt); extend payment service to support negative balances and ledger reversals.  
   - Document the workflow updates in [payfastmigration.md](payfastmigration.md#43-operations--security).
3. **Wallet UX polish**  
   - Expand the thin-slice wallet UI to show full transaction history, highlight negative balances, and prompt users for follow-up actions (link to design in [yummi_scaffold_spec.md](yummi_scaffold_spec.md#10-payments-payfast-hosted-checkout)).  
   - Consider refactoring mobile wallet logic into a dedicated hook/service for reuse.
4. **Data & scrapers**  
   - Resume resolver/catalog enrichment tasks (see [thisproject.md](thisproject.md#todo-focus-areas)) once payment auth work is stable.
