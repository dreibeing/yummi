# Archetype Data Build Plan

## Objective
Produce a validated archetype repository that satisfies the contracts in `yummi_business_logic_requirements.txt` and can be published as Parquet + JSON artifacts for the Fly runtime stack.

## Prerequisites
- Latest controlled vocabulary (`defined_tags` manifest + `tags_version`) and canonical ingredient catalog.
- Market coverage brief + retailer metadata inputs referenced in `thisproject.md`.
- Access to the archetype generation prompt stack (Appendix A.1) and validation scripts.

## Status (2025-11-12)
- **Steps 1–4:** Completed. `data/tags/defined_tags.json` locked at `tags_version 2025.02.0`; constraint brief lives in `data/tags/archetype_constraint_brief.md`; prompt/template + runner (`scripts/archetype_prompt_runner.py`) and curator (`scripts/archetype_curator.py`) now bake in mainstream-first guidance and compact prior-archetype context.
- **Step 5:** Initial QA + GPT-5 curation finished for run `data/archetypes/run_20251112T091259Z`. Raw outputs + recommendations live under `…/curation/`.
- **Step 6:** Aggregated meals are now published via `scripts/meal_aggregate_builder.py`, which emits `resolver/meals/meals_manifest.json` (Fly serves this through `/v1/meals*`). Parquet packaging/checksums remain optional until `pyarrow` is installed, and we still need a process note for release tagging.
- **Ingredient normalization (new pipeline):** `scripts/ingredient_cleanup.py` + `data/catalog_filters.json` trim the Woolworths catalog to 6 k candidates, `scripts/ingredient_batch_builder.py` emits single-item GPT batches, `scripts/ingredient_llm_classifier.py` (model `gpt-5-nano-2025-08-07`, `--max-output-tokens 5000`) classified every SKU into `ingredient`/`ready_meal`, and `scripts/ingredient_classifications_builder.py` produced `data/ingredients/ingredient_classifications.{jsonl,csv}` plus a deduped list at `data/ingredients/unique_core_items.csv`.
- **Preference sync:** Expo onboarding now persists tag selections via `/v1/preferences`, which stores normalized responses in `user_preference_profiles` on Fly (deployed 2025-11-17). Runtime work must read from this table going forward.

## Build Steps
1. **Tag Vocabulary & Versioning**  
   - Lock the controlled categories/values called out in `yummi_business_logic_requirements.txt` and assign a `tags_version`.  
   - Fail the build if any planned archetype fields reference missing tags.

2. **Canonical Ingredient & Constraint Alignment**  
   - Ensure diet/allergen guardrails and household/audience contexts are documented per the Tagging System Architecture.  
   - Map required allergen restrictions to canonical ingredient flags before prompting.

3. **Prompt Package Preparation**  
   - Draft the `market_coverage_brief` (audience, cuisines, household sizes) plus retailer notes, then feed them into the Archetype Generation prompt template (Appendix A.1).  
   - Pre-assign deterministic base36 `uid`s from archetype names to keep referential integrity with planned meals.

4. **Archetype Generation Run**  
   - Execute prompt batches, collect JSON output with the required fields (`uid`, `name`, `description`, `core_tags`, `diet_profile`, `allergen_flags`, `heat_band`, `prep_time_minutes_range`, `complexity`, `refresh_version`).  
   - Capture raw run metadata (model, temperature, inputs) for reproducibility.

5. **Validation & Coverage QA**  
   - Enforce schema checks, required tag coverage (Diet, Cuisine openness, Complexity, PrepTime, Heat, Allergens, Audience), and uniqueness of `uid`s.  
   - Verify collective coverage spans the “full theoretical customer base” mandate before publishing.

6. **Artifact Packaging & Publication**  
   - Serialize the final set into Parquet + JSON manifests, embed `tags_version`/`refresh_version`, and store under the resolver dataset tree referenced in `thisproject.md`.  
   - Record release notes + checksums, then push artifacts to the Fly server/object store for downstream meal generation and runtime usage.

## Deliverables
- `archetypes.parquet` and `archetypes.json` aligned with the canonical schema.  
- Validation report + prompt metadata for traceability.  
- Updated roadmap entry noting the deployed `tags_version` and `refresh_version`.

## Immediate Next Actions
1. Finish meal coverage for every curated archetype (run `scripts/meal_builder.py --archetype-uid <uid>`), then regenerate `resolver/meals/meals_manifest.json` so `/v1/meals*` exposes the full portfolio before product QA begins.
2. Install `pyarrow` and rerun `scripts/meal_aggregate_builder.py` with Parquet output + checksum logging so Step 6 artifacts satisfy the publication contract (JSON + analytics Parquet + release note).
3. Wire curator recommendations (keep/modify/replace) into the next generation run or manual edits, then re-run the curator to confirm overlap clusters are resolved.
4. Hook the thin-slice app + extension to `/v1/meals` and `/v1/meals/{uid}`, add caching/invalidation guidance, and confirm an end-to-end thin-slice journey uses the hosted manifest.
5. Review `data/ingredients/unique_core_items.csv` with product/culinary leads, lock a `canonical_ingredients` schema/version, and feed that list into the upcoming meal-generation prompt so recipes reference normalized ingredient IDs instead of retailer SKUs.
6. Read `user_preference_profiles` when building candidate pools so meal scoring honors saved diet/allergen tags; document the contract that `/v1/preferences` now satisfies for downstream services.
