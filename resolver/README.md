# Resolver Utility (URL/SKU Enrichment)

Purpose
- Enrich items lacking `url`/`sku` using a curated `catalog.json` first.
- Optionally add a safe, rate-limited search resolver (future) that looks up product URLs one-by-one and caches results.

Status
- Bootstrap only. Populate `catalog.json` manually for core items.

Files
- `catalog.json` — mapping `{ normalized_title | uid : { url, sku? } }`.

Notes
- Keep lookups slow and sparse to avoid stressing the retailer.
- Persist enriched items to reuse results across sessions.

