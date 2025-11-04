# Interim Cart Integration Plan (XHR-Only, iOS + Android)

## Objective
Fill the user's Woolworths cart using same-origin XHR inside our in-app WebView (no DOM automation). UX stays: user taps "Place Order" -> sees a progress panel inside the app -> once items are added, we deep-link to the Woolworths cart page.

## Non-Negotiables
- Works on iOS (WKWebView) and Android WebView with a single runner script.
- No DOM clicking or PDP navigation. Treat any non-XHR cart add as a failure.
- User must be signed in and must choose a delivery location before we post items.
- Respect rate limits with batching and jitter to avoid ToS issues.

## Current Symptoms
- Responses often lack an explicit success marker, so the runner reports `no_success_flag` and the cart stays empty.
- We previously saw `formExceptions` like "Please select a valid item..." for certain SKUs.

## Root-Cause Hypotheses
1. Missing or incorrect request headers in the mobile WebView (e.g., `X-Requested-With`, `Accept`).
2. Payload mismatch: boolean vs string (`fromDeliverySelectionPopup`), or preferring the wrong IDs (`productId` vs `catalogRefId`).
3. Session context not matching desktop: missing `placeId`, `deliveryType`, or `storeId`.
4. Mobile UA/variant returns a different JSON shape that lacks the desktop success flags.

## Implementation Direction (XHR-Only)
- POST to `https://www.woolworths.co.za/server/cartAddItems` with body:
  - `deliveryType`
  - `address.placeId`
  - optional `storeId`
  - `fromDeliverySelectionPopup: true`
  - `items: [{ productId, catalogRefId, quantity, itemListName }]`
- Headers:
  - `content-type: application/json`
  - `x-requested-by: Woolworths Online`
  - `x-requested-with: XMLHttpRequest`
  - `accept: application/json, text/javascript, */*; q=0.01`
- Always send with `credentials: include` so cookies accompany the request.

## WebView Notes
- Keep the WebView on a Woolworths origin page (checkout/cart) so XHR stays same-origin.
- Force a desktop user agent for consistent server behaviour.
- Run items sequentially with 400-800 ms jitter to avoid rapid-fire adds.

## Success Criteria
- HTTP 200 plus one of:
  - `success === true`
  - `status` in `{"SUCCESS","OK"}`
  - numeric totals in `cartData.cartTotal` or `cartSummary.total`
- No `errorMessages`.
- (Optional) Subsequent cart fetch shows the added quantity.

## Diagnostics
- Log HTTP status, `errorMessages`, and whether `deliveryType`, `placeId`, and `storeId` were present.
- Log the top-level keys in any 200 response that lacks a success flag for easy diffing vs desktop.

## ID Resolution Rules
- Prefer `catalogRefId` from the resolver; fallback to `productId`.
- If only a URL exists, extract `A-<digits>` or trailing digits.
- Missing IDs are hard failures that never send an XHR.

## Runner Flow (Mobile)
1. Open WebView to Woolworths; prompt user to sign in if needed.
2. Wait until cookies show login, then wait until `placeId` is present.
3. Iterate items: build payload, issue XHR with credentials and headers.
4. Capture per-item success/failure; do not attempt DOM fallback.
5. When done, navigate to `https://www.woolworths.co.za/check-out/cart` inside the same WebView.

## Immediate Adjustments Done
- Removed all DOM fallbacks (extension + runner).
- Detect and surface Woolworths formExceptions as failures.
- Ensure `fromDeliverySelectionPopup` is sent as the string `"true"` to match the Woolworths contract.
- Normalise cart payloads so mismatched `catalogRefId` values fall back to the current `productId`.
- Treat basket responses with `basketId`/`groupSubTotal`/`items` as success to avoid false negatives.
- Added `X-Requested-With: XMLHttpRequest` and JSON-friendly `Accept` headers everywhere.
- Added a per-run runner log file (Expo FileSystem) that records payload and response summaries for easy sharing.

## Next Investigation Steps
1. Capture a desktop-success request and a mobile-failure request for the same SKU; diff headers, payload, and response JSON.
2. Extend success detection if the mobile response uses alternate flags.
3. Re-check ID preference: prioritise `catalogRefId`, fallback to `productId`, measure error counts.
4. Tune jitter or add backoff if 429/5xx responses appear.

## Edge Handling
- If user is not logged in or no `placeId` is detected, pause and prompt instead of firing XHR.
- Mark out-of-stock/invalid SKUs as failed but continue the queue; summarise at the end.

## Rollout
- Ship as v0.5.3 (thin-slice) / 0.1.3 (extension).
- Validate on Android emulator and iOS simulator with a handful of known SKUs.
- After parity, consider a lightweight retry/backoff for transient 5xx while staying XHR-only.

