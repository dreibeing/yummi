# Woolworths Product Scraper

Utility for discovering Woolworths Food categories and scraping product metadata that feeds the cart resolver catalog.

## Architecture

- **HTTP client (`client.py`)** – wraps `httpx.Client`, keeps cookies, introduces jitter, and retries transient shields/anti-bot pages.
- **Initial state parser (`parser.py`)** – extracts `window.__INITIAL_STATE__` JSON and exposes helpers for record iteration, pagination, and PDP enrichment hooks.
- **Category discovery (`discover.py`)** – walks the Food navigation starting at the department root and produces canonical category URLs plus breadcrumb paths.
- **Category crawler (`scraper.py`)** – paginates each category via the `?No=<offset>` parameter, yielding normalized product summaries keyed by product ID.
- **Writers (`writer.py`)** – persist outputs as JSON Lines + CSV under `data/product_table_folder/` and refresh `resolver/catalog.json`, including alternate entries when titles collide.
- **CLI (`__main__.py`)** – provides two subcommands:
  - `discover` – `python -m woolworths_scraper discover --output woolworths_scraper/config/categories.food.json`
  - `scrape` – `python -m woolworths_scraper scrape --categories woolworths_scraper/config/categories.food.json --catalog-output resolver/catalog.json`

## Running The Scraper

1. **Install dependencies (once per environment)**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r woolworths_scraper\requirements.txt
   ```

2. **Refresh category list (optional but recommended after major site changes)**
   ```powershell
   python -m woolworths_scraper discover --output woolworths_scraper/config/categories.food.json --log-level INFO
   ```

3. **Scrape Food products**
   ```powershell
   python -m woolworths_scraper scrape --categories woolworths_scraper/config/categories.food.json --catalog-output resolver/catalog.json --log-level INFO
   ```
   - Swap `--categories …` for `--auto-food` to discover categories inline.
   - Use `--limit <N>` to clamp record counts during testing.

4. **Outputs**
   - `data/product_table_folder/woolworths_products_raw.jsonl` – complete record dump.
   - `data/product_table_folder/woolworths_products_summary.csv` – spreadsheet-friendly subset.
   - `resolver/catalog.json` – normalized title → `{ productId, catalogRefId, url, ... }`, with `alternates` on collisions (reported in logs).

## Safety & Performance Notes

- Request jitter: ~0.75–1.5 seconds between fetches with up to four concurrent category requests.
- Retries/backoff when empty payloads or transient 5xx are encountered; 500 loops result in a logged skip so the scrape continues.
- Category toggles: set `"enabled": false` inside `woolworths_scraper/config/categories.food.json` to omit a category (logged as `Skipping disabled category …`).
- Cached responses land in `.cache/` for debugging and re-runs.

## Data Model (per product)

```json
{
  "product_id": "20018702",
  "catalog_ref_id": "20018702",
  "sku": "20018702",
  "name": "White Thick Slice Bread 700 g",
  "brand": "Woolies Brands",
  "department": "Food",
  "path": ["Food", "Bakery", "Bread & Rolls", "Bread", "White Bread"],
  "sale_price": 20.99,
  "detail_url": "https://www.woolworths.co.za/prod/Food/Bakery/Bread-Rolls/Bread/White-Bread/White-Thick-Slice-Bread-700-g/_/A-20018702",
  "image_url": "https://assets.woolworthsstatic.co.za/White-Thick-Slice-Bread-700-g-20018702.jpg?…",
  "attributes": {...}
}
```

Future enrichment hooks can add nutrition, pack size, allergens, etc., by parsing PDP payloads.

## Next Steps

1. Improve category discovery dedupe/promo filtering.
2. Extend PDP enrichment for nutritional metadata and pack sizing.
3. Integrate the resolver catalog refresh into CI or scheduled jobs and alert on scraping failures.

