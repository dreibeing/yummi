# PayFast Migration Plan

## 1. Context
- **Current stack:** Expo app uses Stripe PaymentSheet via a Node/Express backend (`payments/create-intent`, `/stripe/webhook`). Server stores wallet/payment records and verifies Stripe webhooks.
- **Problem:** Stripe does not support merchants in South Africa, preventing live onboarding.
- **Goal:** Replace Stripe integration with PayFast while preserving secure auth via Clerk and future subscription features.

## 2. PayFast at a Glance
- **Products:** Once-off payments (`payment request` form), recurring subscriptions, ad-hoc top-ups.
- **Hosted checkout:** Mobile or web app posts signed fields to `https://www.payfast.co.za/eng/process`. Sandbox: `https://sandbox.payfast.co.za`.
- **Security model:** Merchant ID + merchant key (+ optional passphrase). Every request/response is signed by concatenating sorted parameters (`name=value&…`) and hashing with MD5 (legacy) or SHA-256 when passphrase enabled.
- **Notifications:** ITN (Instant Transaction Notification) POSTs to our backend; PDT (Payment Data Transfer) for client-side confirmation.
- **Reference:** https://developer.payfast.co.za/documentation (Instant EFT, CC, wallet).

## 3. Target Architecture
```
Expo Mobile
 ├─ Auth via Clerk (existing)
 ├─ Calls POST /payments/payfast/initiate -> server signs payload
 ├─ Opens WebView/Browser with PayFast hosted form
 ├─ Handles return/cancel URLs (deep link -> app)

Backend (FastAPI)
 ├─ POST /payments/payfast/initiate
 │    - validate amount, bind to user, generate signature, log record
 │    - respond with form fields + redirect URL
 ├─ POST /payments/payfast/itn
 │    - verify signature & source IP, POST back to PayFast for validation
 │    - update payment status, credit wallet
 ├─ GET /payments/payfast/pdt (optional)
 │    - frontend query to confirm (requires PDT token)
 └─ Future: recurring billing endpoint wrappers
```

## 4. Migration Workstream

### 4.1 Backend changes (FastAPI)
1. **Config & secrets**
   - Add `PAYFAST_MERCHANT_ID`, `PAYFAST_MERCHANT_KEY`, `PAYFAST_PASSPHRASE?`, `PAYFAST_MODE` (sandbox/live), `PAYFAST_PDT_TOKEN`.
   - Update startup validation: require PayFast secrets for non-dev.
2. **Signature utilities**
   - Implement helper to serialize fields sorted by key, URL encode values, append passphrase if configured, hash (MD5 default, SHA-256 if `use_passphrase`).
3. **Initiate endpoint**
   - Endpoint: `POST /payments/payfast/initiate`.
   - Body: `{ amountMinor, currency, itemName, itemDescription }`.
   - Server tasks:
     - Map to PayFast fields (amount in Rands with 2 decimals, merchant details, `return_url`, `cancel_url`, `notify_url`, custom field(s) linking to user/order).
     - Add security fields: signature, user-defined reference.
     - Persist payment record (status `pending`, store checksum).
     - Respond with JSON: hosted URL + fields for client to POST (or auto-generated HTML form string).
4. **Notify handler (`/payments/payfast/itn`)**
   - Accept PayFast POST (form-encoded).
   - Verify request origin (validate signature, ensure IP in PayFast range or by remote verification).
   - Post validation: make HTTP POST back to PayFast with same fields (`https://sandbox.payfast.co.za/eng/query/validate`).
   - If valid and payment `COMPLETE`, mark payment success, update wallet.
5. **PDT (optional)**
   - If we provide PDT token, implement `GET /payments/payfast/pdt?pt=...` to fetch final status for client return.
6. **Model updates**
   - Add `payments` table/model storing PayFast reference, amount, currency, checkout payload, ITN history, and status transitions.
7. **Tests**
   - Unit tests for signature builder & ITN parser.
   - Integration tests using recorded fixtures from sandbox.

### 4.2 Mobile app (Expo)
1. **API Contract**
   - Update `payments` service to call `POST /payments/payfast/initiate`.
   - Expect JSON: `{ url, params }`.
2. **Checkout UI**
   - Show a WebView with auto-submitted form to PayFast (preferred) or open system browser if regulations require.
   - Capture return via deep link (PayFast `return_url` -> `yummi://payfast/return?reference=...&pf_payment_id=...`).
   - On return (or while checkout is open), call `/payments/payfast/status?reference=` to poll payment + wallet credit status; fall back to PDT endpoint only if PayFast disables ITN temporarily.
   - On cancel: detect `cancel_url` navigation, show error state.
3. **Sandbox testing**
   - Use PayFast test card numbers or Instant EFT instructions.

### 4.3 Operations & Security
1. **Environment variables**
   - Local dev: supply sandbox merchant credentials (PayFast provides sample).
   - Fly secrets: set PayFast keys; remove Stripe keys from repo and secrets manager.
2. **CI/CD**
   - Update `.env.example`, startup validation, `docker-compose`, `fly.toml` docs.
3. **Webhook handling**
   - Ensure ITN endpoint accessible over HTTPS (public). For local dev use ngrok/Cloudflared.
   - Store HMAC logs for audits, respond `200` quickly (<7s).
4. **Compliance**
   - No card data handled client-side (hosted page). Must provide return/privacy policy links in fields.
   - Implement chargeback + refund policies (see `Chargebacks.txt`): allow negative balances, block spending under zero, keep audit logs, limit user-initiated refunds, flag abusive behavior.
   - ITN sync now mirrors payment status into the wallet ledger:
     - `payment_status=COMPLETE` → credit entry is created (idempotent).
     - `payment_status=CANCELLED|FAILED` → matching debit entry is recorded when a prior credit exists, marking chargebacks.
   - Wallet summaries expose `spendBlocked`, `spendableMinor`, and `lockReason` so clients can block debits until the balance returns to ≥0. Negative balances set `spendBlocked=true` with `lockReason="negative_balance"`.

#### Sandbox QA log — 2025-11-10
- Ngrok tunnel: `https://polarographic-zuri-semielastic.ngrok-free.dev`
- Reference: `user_353rOOrT8uKsY1A9np0mDn8Thgw`
- Amount: R100.00 (mobile top-up)
- Result: ITN delivered, wallet credited (thin-slice shows “Complete · Wallet credited”)
- PayFast dashboard ITN ID: `1644573`
- Key log excerpt:
  ```
  2025-11-10T10:29:49Z PayFast checkout params built host=https://sandbox.payfast.co.za/eng/process reference=user_353rOOrT8uKsY1A9np0mDn8Thgw … notify_url=https://polarographic-zuri-semielastic.ngrok-free.dev/v1/payments/payfast/itn … passphrase=test_passphrase
  2025-11-10T10:30:13Z Skipping ITN remote validation in dev environment
  2025-11-10T10:30:13Z PayFast ITN received status=COMPLETE reference=user_353rOOrT8uKsY1A9np0mDn8Thgw
  2025-11-10T10:30:13Z 172.18.0.1:53422 - "POST /v1/payments/payfast/itn HTTP/1.1" 200
  2025-11-10T10:30:14Z 172.18.0.1:55102 - "GET /v1/payments/payfast/status?reference=user_353rOOrT8uKsY1A9np0mDn8Thgw HTTP/1.1" 200
  ```
- Notes: UI confirmed “Complete · Wallet credited” at 12:30 local and PayFast dashboard entry `1644573` moved to `Complete` after the ITN retry, so no further screenshots were captured.

#### Sandbox QA log — 2025-11-11
- Environment: Fly staging (`https://yummi-server-greenbean.fly.dev`)
- Reference: `user_353rOOrT8uKsY1A9np0mDn8Thgw`
- Amount: R100.00 (thin-slice mobile top-up)
- Result: Hosted checkout succeeded, ITN validated remotely, wallet credited (Clerk auth enforced).
- PayFast dashboard ITN ID: `1645682`
- Key log excerpt:
  ```
  2025-11-11T07:52:04Z PayFast checkout params built host=https://sandbox.payfast.co.za/eng/process reference=user_353rOOrT8uKsY1A9np0mDn8Thgw … notify_url=https://yummi-server-greenbean.fly.dev/v1/payments/payfast/itn … merchant_id=10043474
  2025-11-11T07:52:34Z HTTP Request: POST https://sandbox.payfast.co.za/eng/query/validate "HTTP/1.1 200 OK"
  2025-11-11T07:52:34Z PayFast ITN received status=COMPLETE reference=user_353rOOrT8uKsY1A9np0mDn8Thgw
  2025-11-11T07:52:35Z 172.16.31.162:57202 - "GET /v1/wallet/balance HTTP/1.1" 200
  ```
- Notes: Secrets now live in Fly; remote validation stays enabled (`AUTH_DISABLE_VERIFICATION=false`). Return bridge served successfully via Fly without ngrok.

#### Ngrok regression checklist
1. **Start local stack** – `docker compose up -d --build yummi-server db redis` from repo root with `ENVIRONMENT=dev` so ngrok traffic can hit the ITN without remote validation (staging/prod must keep validation enabled).
2. **Expose the tunnel** – `ngrok http 8000` (or Cloudflared). Copy the HTTPS URL.
3. **Refresh PayFast URLs** – update `.env` or Compose overrides with the latest tunnel for `PAYFAST_NOTIFY_URL`, `PAYFAST_RETURN_URL`, and `PAYFAST_CANCEL_URL`, then restart the stack so the values load.
4. **Trigger a top-up** – from the thin-slice app run a R100 wallet top-up (card: `4100 0000 0000 0000`, CVV `123`, OTP `12345`).
5. **Watch validation logs** – expect `PayFast ITN received status=...` followed by the remote validation call (only dev logs “Skipping ITN remote validation”; staging/prod must never skip).
6. **Confirm wallet credit** – `/v1/wallet/balance` should reflect the credit immediately; verify the PayFast dashboard entry transitions to `Complete`.
7. **Record the run** – capture tunnel URL, reference, ITN ID, and log excerpt back into this section so the next regression run has provenance.

## 5. Rollout Steps
1. **Foundation (backend)**
   - Implement PayFast config/secrets.
   - Replace Stripe modules with PayFast service (keep feature flags for fallback if needed).
   - Store payments + wallet ledger entries in Postgres so ITNs credit user balances.
2. **Mobile integration**
   - Update API service & screens.
   - Implement return/cancel flows, status polling.
   - Fetch `/v1/wallet/balance` post-payment to refresh wallet UI.
3. **Testing**
   - Unit tests for signature/ITN.
   - Manual sandbox flow (test card: `4100000000000000` with CVV `123`, expiry future date, OTP `12345`).
   - ITN validation via sandbox (requires publicly reachable notify URL or PayFast's manual trigger).
4. **Cutover**
   - Remove Stripe-specific secrets and docs.
   - Update README/onboarding guides.
5. **Post-launch**
   - Monitor ITN endpoints, log unknown statuses.
   - Schedule credential rotation and reconcile wallet ledger vs PayFast dashboard regularly.

## 6. Open Questions
- Will we offer recurring subscriptions? (PayFast Subscriptions API differs; requires additional enablement.)
- Do we need on-demand payouts or wallet balances? (Ledger now tracks credits; confirm debit/reversal flows.)
- Should we maintain Stripe capability for other regions (feature flag by locale)?

## 7. References
- PayFast Developer Docs: https://developer.payfast.co.za/documentation
- Signature guide: https://developer.payfast.co.za/documentation/#step_3
- Sandbox credentials: https://developer.payfast.co.za/testing/
