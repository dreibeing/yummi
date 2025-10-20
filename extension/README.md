# Woolworths Cart Filler (POC)

MV3 extension that fills a Woolworths cart from a JSON item list. Primary path is same-origin XHR; fallback to DOM-click on product pages. Opens `/cart` on completion.

Install (Dev)
- Go to chrome://extensions, enable Developer Mode.
- Load unpacked → select the `extension/` folder.
- Pin the extension; open popup on a `woolworths.co.za` tab.

Usage
- Paste Item List JSON like:
  `{ "retailer":"woolworths_co_za", "items":[ { "url":"https://www.woolworths.co.za/prod/...", "qty":1, "title":"Example" } ] }`
- Click Start. Progress and results appear in the popup. Cart opens at the end.

Notes
- Extension service worker fetches delivery context (`userDelivery` / `location` / `storeId` cookies) and passes it to the content script for the `cartAddItems` POST.
- Batching/throttling implemented in `service-worker.js` (3 concurrent; jitter between ops and batches).
- Per-item results and summary are kept in `chrome.storage.local`.
- Sample payload: see `../samples/items-3.json`.

Files
- manifest.json
- popup.html, popup.js
- service-worker.js (orchestrates queue)
- content.js (XHR + DOM fallback stubs)
