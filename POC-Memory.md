# Shopper PoC Memory (for new build)

This document captures the prior “Shopper” Proof‑of‑Concept (PoC). It was a manual, script‑driven spike to validate data flows and LLM‑assisted meal generation ahead of the new Phase 1 app. It is not production‑ready. Use this as institutional memory while building the new project.

- Context docs in this repo: `Phase 1 PRD.txt`, `Woolworths Basket Integration - Implementation & Test Plan (Agent Brief).txt`
- Original PoC root: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI`
- Some earlier scripts/outputs referenced a prior root: `C:\Users\user\Documents\Greenbean\Shopper` (not CursorProjects)

**Status**
- PoC only, manually orchestrated scripts, mixed paths, secrets in local `config.py`.
- Retailer cart fill integration is specified (Agent Brief) but not implemented in this PoC.

**High‑Level Flow**
- Retailer catalog scrape → product table
- Ingredients corpus build/clean
- Archetypes (LLM) → add stable `uid`
- Archetype‑specific dinner ingredients (LLM)
- Meal generation (LLM) → names, ingredients+qty, recipes
- Meal tagging (LLM) with controlled vocabulary → persisted + DB sync
- User preference capture (Flask + SQLite) → user tags and restrictions

**Runbook (from Script Process)**
- 1) Run `webscraper` per category (update URLs). Outputs per‑category product tables.
- 2) Run `combined_webscrape` → `combined_webscrape_df.pkl` in product table folder.
- 3) Run `clean_combined_webscrape` → `clean_combined_webscrape_df.pkl`.
- 4) Run `clean_quantities_combined_webscrape` → `clean_quantities_combined_webscrape_df.pkl`.
- 5) Run `final_product_table` → adds product `uid` → `final_product_table_df.pkl`.
- 6) Run `ingredients_processing` → `ingredients_table_df.pkl`.
- 7) Run `ingredients_processing_2` → `ingredients_table_clean_df.pkl` (capitalized).
- 8) Run `create_archetypes` (LLM) → `archetypes_df.pkl`.
- 9) Run `final_archetypes` → add `uid` → `final_archetypes_df.pkl`.
- 10) Run `archetype_ingredients` (LLM) → `archetype_ingredients.pkl`.
- 11) Meals processing (LLM): `meals_processing.py` / `meals_processing_batch.py` → meals, ingredients, recipes.
- 12) Tags processing (LLM): `tags_processing.py` → controlled tags + meal tag assignments; `distill_meal_tags.py` for mapping.
- 13) Meal restrictions batch: derive user‑specific allowed meals.
- 14) Web app for user tag selection: `Scripts/web_user_tags/feedback_meal_batch.py` etc.

File references in the PoC below show the concrete locations of these steps and outputs.

**Key Components & Files**
- Config and secrets
  - `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\config.py` (contains `GOOGLE_API_KEY`; do not commit secrets)

- Product catalog (scrape outputs and consolidated tables)
  - Per‑category scrape outputs: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\product_webscape`
    - e.g., `Pantry.pkl`, `Milk-Dairy-Eggs.pkl`, etc.
  - Consolidated/clean tables: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\product_table_folder`
    - `combined_webscrape_df.pkl`, `clean_combined_webscrape_df.pkl`, `clean_quantities_combined_webscrape_df.pkl`, `final_product_table_df.pkl`

- Ingredients corpus
  - `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\ingredients\ingredients_table_df.pkl`
  - `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\ingredients\ingredients_table_clean_df.pkl`

- Archetypes (LLM) and IDs
  - Create: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\create_archetypes.py`
  - Finalize with `uid`: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\final_archetype.py`
    - UID logic: Base36 encoded MD5(name) prefix
  - Data: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\archetypes\archetypes_df.pkl`, `final_archetypes_df.pkl`

- Archetype dinner ingredients (LLM selection over global ingredients)
  - Script: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\archetype_ingredients.py`
  - Output: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\archetype_ingredients\archetype_ingredients.pkl`

- Meal generation (LLM)
  - Single/batch: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\meals_processing.py`, `meals_processing_batch.py`
  - Outputs: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\meals\meals.pkl`, `meal_ingredients_df.pkl`, `meal_recipe_df.pkl`
  - Logs: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\meal_generation.log` and `Scripts\meal_generation.log`

- Tagging (LLM) with controlled vocabulary
  - Controlled vocab + tagging: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\tags_processing.py`
    - Maintains `CONTROLLED_VOCAB` categories and tags
    - Syncs `defined_tags` into SQLite
  - Distilled outputs: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data\tags\distilled_tags_df.pkl` (+ `.csv`, `.xlsx`)

- User preference capture (Flask + SQLite)
  - App: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\web_user_tags\app.py`
  - DB: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\web_user_tags\web_user_tags.db`
  - DB schema/init and population from dataframes: `Scripts\web_user_tags\database.py`
    - Tables: `users`, `defined_tags`, `meals`, `user_tags`, `meal_tags_assoc`, `user_meal_restrictions`
  - Batch scripts: `Scripts\web_user_tags\meal_restriction_batch.py`, `feedback_meal_batch.py`

- Legacy user/tag picker (Tkinter; older pathing)
  - `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\user_tags.py`
  - References legacy paths under `C:\Users\user\Documents\Greenbean\Shopper\...` for user/tag files

**Logic Notes**
- LLM usage (Gemini 2.0 Flash)
  - Prompts require strict JSON; responses are cleaned (strip code fences) and parsed with retries/backoff.
  - Archetypes: prompt constrained with a controlled tag vocabulary (from `tags_processing.py`).
  - Archetype ingredients: select dinner ingredients per archetype from the global cleaned list.
  - Meals: generate distinct meal names; each meal uses only provided ingredients with explicit quantities; full recipe text.

- Controlled vocabulary and DB sync
  - `CONTROLLED_VOCAB` defines categories like Cuisine, Protein/Base, Technique, Dish Format, Timing, Audience, Dietary, Complexity, Spice.
  - `tags_processing.py` can sync this vocabulary into the SQLite `defined_tags` table (idempotent populate).

- Identifiers and schemas
  - Archetype UID: Base36 of MD5(name) prefix (stable across runs if names unchanged).
  - Meals dataframe columns: `uid`, `archetype_name`, `meal_name`.
  - Meal ingredients: `meal_name`, `ingredient`, `quantity`.
  - Meal recipes: `meal_name`, `recipe`.

- Paths and outputs
  - Many scripts persist `.pkl` and `.xlsx` side‑by‑side under `Data/*` for inspection.
  - Some earlier scripts and the Tkinter tool use the legacy root (`C:\Users\user\Documents\Greenbean\Shopper\...`). The consolidated PoC lives under the `CursorProjects\Shopper\ShopperAI` tree above.

**Retailer Integration (planned, not in PoC)**
- See: `Woolworths Basket Integration - Implementation & Test Plan (Agent Brief).txt`
  - Primary: Chrome extension (MV3) on `woolworths.co.za`; prefer XHR add‑to‑cart; fallback to DOM click; batched with jitter; then open `/cart`.
  - Optional: Playwright runner for demos (manual login, replay network calls).
  - Inputs: item list JSON (`url` and/or `sku`, `qty`, `title`).

**Gaps / Risks (to address in new build)**
- Manual orchestration; no pipeline automation or tests.
- Mixed/legacy paths; centralize to one project root and environment config.
- Secrets in plaintext (`config.py`); replace with env vars/secret manager.
- LLM determinism and retry logic; add observability and idempotence.
- Web scraper scripts not present in this folder; only their outputs are checked in.

**How To Reuse**
- Treat Data outputs as fixtures for early development:
  - Product mapping: `final_product_table_df.pkl`
  - Archetypes: `final_archetypes_df.pkl`
  - Archetype ingredients: `archetype_ingredients.pkl`
  - Meals: `meals.pkl`, `meal_ingredients_df.pkl`, `meal_recipe_df.pkl`
  - Tags: `distilled_tags_df.pkl` and DB sync via `tags_processing.py`
- Port the controlled vocabulary and DB schema from `web_user_tags`.
- Re‑home configuration (API keys) to environment variables; add `.env` and config loader.
- Unify file I/O paths behind a small path utility and app data root.
- Build a proper pipeline (CLI or tasks) to replace the manual runbook.

**Useful Paths (quick open)**
- Scripts root: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts`
- Data root: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Data`
- PoC runbook reference: `C:\Users\user\Documents\Greenbean\CursorProjects\Shopper\ShopperAI\Scripts\Script Process.txt`

