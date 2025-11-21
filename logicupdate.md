# Logic Update Plan — Tags, Archetypes, Meals, Recommendations

This doc captures the current tag taxonomy and the end‑to‑end touchpoints we’ll update together: pre‑calculation logic, backend services, and the app.

---

## Snapshot: Current Defined Tags (source of truth)

Path: `data/tags/defined_tags.json`

```json
{
  "tags_version": "2025.02.0",
  "required_categories": {
    "archetype": [
      "DietaryRestrictions",
      "Cuisine",
      "Complexity",
      "PrepTime",
      "HeatSpice",
      "Allergens",
      "Audience"
    ],
    "meal": [
      "DietaryRestrictions",
      "Cuisine",
      "PrepTime",
      "Complexity",
      "HeatSpice",
      "Allergens"
    ]
  },
  "defined_tags": [
    {"tag_id":"dietres_none","category":"DietaryRestrictions","value":"None","description":"No dietary restrictions; eats all foods"},
    {"tag_id":"dietres_vegan","category":"DietaryRestrictions","value":"Vegan","description":"Excludes all animal-derived products"},
    {"tag_id":"dietres_vegetarian","category":"DietaryRestrictions","value":"Vegetarian","description":"No meat or seafood; allows dairy/eggs"},
    {"tag_id":"dietres_pescatarian","category":"DietaryRestrictions","value":"Pescatarian","description":"Seafood allowed; no poultry or red meat"},
    {"tag_id":"dietres_halal","category":"DietaryRestrictions","value":"Halal","description":"Prepared with halal-certified ingredients and processes"},
    {"tag_id":"dietres_kosher","category":"DietaryRestrictions","value":"Kosher","description":"Meets kosher certification requirements; supervised sourcing and preparation"},

    {"tag_id":"cuisine_southaf","category":"Cuisine","value":"SouthAfrican","description":"Braai, bunny chow, bobotie, chakalaka, pap and samp with peri-peri or chutney heat"},
    {"tag_id":"cuisine_american","category":"Cuisine","value":"American","description":"Burgers, BBQ ribs or pulled pork, fried chicken, mac & cheese, chili, casseroles, subs, wings, hearty dinner salads"},
    {"tag_id":"cuisine_mexican","category":"Cuisine","value":"Mexican","description":"Tacos, burritos, enchiladas, quesadillas, pozole, chilaquiles, rice and bean bowls with corn masa, salsas, chiles, lime"},
    {"tag_id":"cuisine_caribbean","category":"Cuisine","value":"Caribbean","description":"Jerk chicken or pork, curry goat, oxtail stew, rice and peas, plantains, roti; allspice, thyme, scotch bonnet heat"},
    {"tag_id":"cuisine_latin","category":"Cuisine","value":"LatinAmerican","description":"Ceviche, lomo saltado, feijoada, moqueca, asado plates, arepas, empanadas with ají peppers, chimichurri, rice and beans"},
    {"tag_id":"cuisine_italian","category":"Cuisine","value":"Italian","description":"Pastas, risotto, pizza, parmigiana, minestrone; tomato, basil, olive oil, Parmigiano-centered dishes"},
    {"tag_id":"cuisine_french","category":"Cuisine","value":"French","description":"Bistro braises like coq au vin or boeuf bourguignon, quiche, ratatouille, steak frites; butter and wine sauces, Provençal herbs"},
    {"tag_id":"cuisine_greek","category":"Cuisine","value":"Greek","description":"Souvlaki, gyros, moussaka, spanakopita, Greek salads; lemon, oregano, feta, olive oil, yogurt, pita accompaniments"},
    {"tag_id":"cuisine_turkish","category":"Cuisine","value":"Turkish","description":"Kebabs, köfte, pide, lahmacun, dolma, meze spreads; yogurt sauces with sumac, Aleppo pepper, pomegranate molasses"},
    {"tag_id":"cuisine_mideast","category":"Cuisine","value":"MiddleEastern","description":"Shawarma or kofta, falafel, hummus and mezze, pilafs, fattoush; tahini, za’atar, sumac, saffron, warm flatbreads"},
    {"tag_id":"cuisine_northaf","category":"Cuisine","value":"NorthAfrican","description":"Tagines, couscous platters, chermoula fish, harira soup; cumin, coriander, cinnamon, preserved lemon, harissa spice"},
    {"tag_id":"cuisine_indian","category":"Cuisine","value":"Indian","description":"Curries, biryani, dal, tandoori grills, chaat, naan or roti, dosa; garam masala, cumin, turmeric"},
    {"tag_id":"cuisine_chinese","category":"Cuisine","value":"Chinese","description":"Stir-fries, fried rice, chow mein, dumplings, hot pot, mapo tofu; soy, vinegar, ginger, garlic, five-spice or Sichuan pepper"},
    {"tag_id":"cuisine_japanese","category":"Cuisine","value":"Japanese","description":"Sushi and sashimi, ramen, udon, tempura, katsu, donburi, yakitori; dashi, miso, soy, yuzu, meticulous presentation"},
    {"tag_id":"cuisine_korean","category":"Cuisine","value":"Korean","description":"Bulgogi or galbi, bibimbap, kimchi stew, soft tofu jjigae, japchae; gochujang, gochugaru, sesame oil, fermented banchan"},
    {"tag_id":"cuisine_thai","category":"Cuisine","value":"Thai","description":"Green, red, or yellow curries, pad thai, pad kra pao, tom yum or tom kha soups; Thai basil, lime, sweet-salty-sour-spicy balance"},
    {"tag_id":"cuisine_vietnamese","category":"Cuisine","value":"Vietnamese","description":"Pho, bun cha, bun bo Hue, banh mi, goi cuon; fresh herbs, nuoc cham, crisp vegetables, light aromatic broths"},
    {"tag_id":"cuisine_portuguese","category":"Cuisine","value":"Portuguese","description":"Peri-peri grilled meats, bacalhau, caldo verde, bifana sandwiches; garlic, bay leaf, paprika, vinegar, seafood emphasis"},
    {"tag_id":"cuisine_spanish","category":"Cuisine","value":"Spanish","description":"Paella, tapas selections, tortilla española, gazpacho, hearty stews; olive oil, saffron, smoked paprika, seafood-forward plates"},





    {"tag_id":"preptime_less15","category":"PrepTime","value":"Under15","description":"Less than 15 minutes hands-on"},
    {"tag_id":"preptime_15_30","category":"PrepTime","value":"15to30","description":"15 to 30 minutes hands-on"},
    {"tag_id":"preptime_30_60","category":"PrepTime","value":"30to60","description":"30 minutes to 1 hour build"},
    {"tag_id":"preptime_60_plus","category":"PrepTime","value":"60Plus","description":"1 hour plus or multi-step"},

    {"tag_id":"complex_easy","category":"Complexity","value":"Simple","description":"Beginner-friendly steps"},
    {"tag_id":"complex_mid","category":"Complexity","value":"Intermediate","description":"Multiple components but approachable"},
    {"tag_id":"complex_adv","category":"Complexity","value":"Advanced","description":"Advanced timing or techniques"},
    {"tag_id":"complex_show","category":"Complexity","value":"Showstopper","description":"High effort, presentation-focused"},

    {"tag_id":"heat_none","category":"HeatSpice","value":"NoHeat","description":"0 heat; suitable for sensitive palettes"},
    {"tag_id":"heat_mild","category":"HeatSpice","value":"Mild","description":"Noticeable warmth but kid-friendly"},
    {"tag_id":"heat_medium","category":"HeatSpice","value":"Medium","description":"Balanced heat for most adults"},
    {"tag_id":"heat_hot","category":"HeatSpice","value":"Hot","description":"Bold chili presence"},
    {"tag_id":"heat_extra","category":"HeatSpice","value":"ExtraHot","description":"Significant burn; for spice lovers"},

    {"tag_id":"audience_solo","category":"Audience","value":"Solo","description":"1 serving meals for individuals"},
    {"tag_id":"audience_couple","category":"Audience","value":"Couple","description":"2 servings sized for two adults"},
    {"tag_id":"audience_family","category":"Audience","value":"Family","description":"4 servings covering a typical household"},
    {"tag_id":"audience_largefamily","category":"Audience","value":"LargeFamily","description":"6 servings designed for big families"},
    {"tag_id":"audience_extendedfamily","category":"Audience","value":"ExtendedFamily","description":"8 servings for extended family"},




    {"tag_id":"allergen_none","category":"Allergens","value":"None","description":"No declared allergen avoidance; default tolerance"},



    {"tag_id":"allergen_dairy","category":"Allergens","value":"Dairy","description":"Contains milk or lactose ingredients"},
    {"tag_id":"allergen_egg","category":"Allergens","value":"Egg","description":"Contains eggs or egg products"},
    {"tag_id":"allergen_gluten","category":"Allergens","value":"Gluten","description":"Contains wheat/barley/rye gluten"},
    {"tag_id":"allergen_soy","category":"Allergens","value":"Soy","description":"Contains soybeans or soy derivatives"},
    {"tag_id":"allergen_nuts","category":"Allergens","value":"Nuts","description":"Contains peanuts or tree nuts"},
    {"tag_id":"allergen_seafood","category":"Allergens","value":"Seafood","description":"Contains finned fish or shellfish"},
    {"tag_id":"allergen_sesame","category":"Allergens","value":"Sesame","description":"Contains sesame seeds or oil"},

    {"tag_id":"nutrition_highprotein","category":"NutritionFocus","value":"HighProtein","description":"Meals optimized for high protein density per serving"},
    {"tag_id":"nutrition_lowcalorie","category":"NutritionFocus","value":"LowCalorie","description":"Meals designed to stay within a ≤500–600 kcal per serving threshold"},
    {"tag_id":"nutrition_lowcarb","category":"NutritionFocus","value":"LowCarb","description":"Meals moderate in carbohydrates without strict keto limits"},
    {"tag_id":"nutrition_keto","category":"NutritionFocus","value":"Keto","description":"Strict low-carbohydrate, high-fat macro profile for ketogenic diets"},
    {"tag_id":"nutrition_lowfat","category":"NutritionFocus","value":"LowFat","description":"Meals intentionally low in total and saturated fat"},
    {"tag_id":"nutrition_lowsodium","category":"NutritionFocus","value":"LowSodium","description":"Meals formulated to keep sodium levels lower than standard recipes"},
    {"tag_id":"nutrition_highfiber","category":"NutritionFocus","value":"HighFiber","description":"Meals containing elevated dietary fiber per serving"},

    {"tag_id":"equip_oven","category":"Equipment","value":"Oven","description":"Requires oven baking or roasting"},
    {"tag_id":"equip_countertop_cooker","category":"Equipment","value":"SlowOrPressureCooker","description":"Has slow cooker or Instant Pot/pressure cooker"},
    {"tag_id":"equip_airfryer","category":"Equipment","value":"AirFryer","description":"Uses air fryer basket"},
    {"tag_id":"equip_microwave","category":"Equipment","value":"Microwave","description":"Needs microwave-safe cooking or reheating"},
    {"tag_id":"equip_stove","category":"Equipment","value":"StoveTop","description":"Requires stove for boiling or pan cooking"},
    {"tag_id":"equip_grill","category":"Equipment","value":"OutdoorGrill","description":"Needs braai/grill access"},

    {"tag_id":"mealcomp_fromscratch","category":"MealComponentPreference","value":"FromScratch","description":"Prefers meals made from baseline raw ingredients; sauces and components are cooked from scratch."},
    {"tag_id":"mealcomp_semiprepared","category":"MealComponentPreference","value":"SemiPrepared","description":"Prefers combining partially prepared or ready-to-cook items with simple scratch components."},
    {"tag_id":"mealcomp_readymeal","category":"MealComponentPreference","value":"ReadyMealPreferred","description":"Prefers fully prepared ready meals or heat-and-eat dishes, with minimal additional cooking."}
  ]
}
```

### Manifest notes (2025-11 refresh)
- Tag entries now expose only `tag_id`, `category`, `value`, and `description`; the old `is_required_for_*` flags were retired. Required coverage is driven from the `required_categories` arrays instead.
- Added `Allergens` value `None` so archetypes can declare a default “no allergen avoidance” stance when no specific exclusions apply.
- The archetype prompt runner injects the full tag list (with descriptions) for reference while continuing to enforce scope strictly from each predefined config.

---

## E2E Touchpoints We’ll Update

1) Tag Vocabulary and Constraints
- Source of truth: `data/tags/defined_tags.json` (this file).
- Normalization helpers: `data/tags/tag_synonyms.json` (used to map or drop non‑canonical values).
- Constraint brief for coverage: `data/tags/archetype_constraint_brief.md` (referenced by generation/curation prompts).

-2) Offline Generation and Aggregation
- Archetype generation: `scripts/archetype_prompt_runner.py` reads `defined_tags.json` to embed `tags_version` and required category lists in prompts and snapshots.
- Archetype curator (deprecated): `scripts/archetype_curator.py` is retained for manual QA only; the main flow consumes the aggregated run output directly.
- Combined archetype rollup: `scripts/predefined_archetype_aggregator.py` scans each predefined scope and writes `<scope>/archetypes_combined.json` by merging all `run_*` artifacts.
- Meal generation (LLM + SKU selection): `scripts/meal_builder.py`
  - Loads `defined_tags.json` to build the category→values catalog and enforce `ALWAYS_REQUIRED_MEAL_CATEGORIES` for every meal.
  - Applies `data/tags/tag_synonyms.json` when model returns non‑canonical values.
  - Infers missing `Allergens` from ingredient text where needed.
- Meal aggregation: `scripts/meal_aggregate_builder.py`
  - Reads the combined archetypes (`archetypes_combined.json`) + per‑meal JSON to build `resolver/meals/meals_manifest.json` (and optional Parquet).
  - Enforces `required_categories.meal` from `defined_tags.json` and fills gaps from archetype defaults.

3) Resolver Artifacts (served to API)
- Manifest consumed by API: `resolver/meals/meals_manifest.json` (+ `.parquet`).
- Warnings in manifest (e.g., unknown `Equipment` or `NutritionFocus` values) indicate taxonomy drift that we should fix via tag synonyms or vocabulary expansion.

4) Backend Services (`yummi-server/`)
- Config paths: `yummi-server/app/config.py` → `meals_manifest_path` and `tags_manifest_path` default to the files above.
- Tag manifest loader: `yummi-server/app/services/preferences.py::load_tag_manifest()` builds `TagManifest` maps (tag_id→category/value) and exposes `tags_version` for validation.
- Preference profile: `user_preference_profiles` model (`yummi-server/app/models.py`) stores `responses`, `selected_tags`, `disliked_tags`, and latest recommendations.
- Candidate filtering: `yummi-server/app/services/filtering.py`
  - Converts selected/disliked tag_ids into human values using `TagManifest`.
  - Applies diet/ethics/allergen/heat/prep filters before ranking.
  - Has hard‑coded sets and maps that must stay aligned with tag IDs/values (see “Known Inconsistencies”).
- Exploration workflow: `yummi-server/app/services/exploration.py` + route `app/routes/recommendations.py` (`/v1/recommendations/exploration`).
- Recommendation workflow: `yummi-server/app/services/recommendation.py` + same route module (`/v1/recommendations/feed`). Persists latest ranked meal IDs back onto the preference profile.
- Meals API: `app/routes/meals.py` serves `GET /v1/meals` and `GET /v1/meals/{uid}` from the manifest.
- Schemas: `yummi-server/app/schemas.py` defines all request/response contracts (preferences, candidate pool, exploration, feed).

5) App (Expo thin slice, `thin-slice-app/App.js`)
- Tag selection UI: `BASE_PREFERENCE_CATEGORIES` enumerates tag_ids and labels users interact with.
- Tag version: `PREFERENCES_TAGS_VERSION` must match `tags_version` in `defined_tags.json` and the backend’s manifest.
- Preference sync: PUT `/v1/preferences` with `tagsVersion` and `responses` once onboarding completes; GET `/v1/preferences` hydrates state and latest home‑feed snapshot.
- Exploration + feed: POST `/v1/recommendations/exploration` then POST `/v1/recommendations/feed`; home surface renders `latestRecommendationMeals` when available.

---

## Known Inconsistencies To Address

- Prep time buckets differ across docs/code vs. tag values:
  - Tags use `Under15`, `15to30`, `30to60`, `60Plus` (see manifest snapshot above).
  - `yummi-server/app/services/filtering.py` maps `PREP_TIME_BUCKET_TO_MINUTES` with `30to45`, `45Plus` — update to align with the tag manifest or adjust the manifest accordingly.
- Equipment/Nutrition tags in some meals fall outside the vocabulary:
  - `resolver/meals/meals_manifest.json` contains warnings like `value 'FryingPan' not in defined_tags` and `value 'ProteinRich' not in defined_tags`.
  - Resolve by expanding `defined_tags` or mapping via `data/tags/tag_synonyms.json` (preferred) and regenerating meals/manifest.
- Filtering restrictions depend on stable tag_ids:
  - Hard‑coded sets (e.g., `DIET_RESTRICTION_TAG_IDS` in filtering) must be updated if tag_ids change.

---

## Refactor Plan (High‑Level)

1) Taxonomy update
- Finalize category list and values in `data/tags/defined_tags.json`; bump `tags_version`.
- Extend `data/tags/tag_synonyms.json` to normalize legacy/new terms (drop or canonicalize).

2) Prompt + generation alignment
- Update `data/tags/archetype_constraint_brief.md` and prompt templates to reflect the new vocabulary.
- Regenerate archetypes (`scripts/archetype_prompt_runner.py`) and review the aggregated outputs for coverage (curator script optional/archived).

3) Meal builder adjustments
- Update `ALWAYS_REQUIRED_MEAL_CATEGORIES` or rely solely on `required_categories.meal` from the manifest.
- Add/adjust normalization rules in `meal_builder.py` to handle new categories/values.
- Regenerate meals per archetype.

4) Aggregate + validate manifest
- Rebuild `resolver/meals/meals_manifest.json` via `scripts/meal_aggregate_builder.py`.
- Resolve warnings by iterating tag synonyms or extending `defined_tags`.

5) Backend runtime
- Ensure `tags_manifest_path` and `meals_manifest_path` point to updated artifacts.
- Update `filtering.py` constants (diet/ethics/heat/prep maps) to match the new tag_ids/values.
- Verify `/v1/recommendations/*` flows with new tags (schema remains stable).

6) App integration
- Sync `PREFERENCES_TAGS_VERSION` and `BASE_PREFERENCE_CATEGORIES` to the new taxonomy.
- Adjust any UI copy impacted by category changes; keep tag_ids intact for API.

7) Data migration considerations
- Existing `user_preference_profiles` store tag_ids. If ids change, provide a migration (one‑off script) or expand `tag_synonyms` to map old→new during request handling.

8) Testing
- Smoke test: preference save/fetch, exploration, feed, meal detail fetch.
- Validate that a variety of user profiles produce non‑empty candidate pools and end‑to‑end feeds.

---

## Quick Inventory (Where To Touch)

- Tags and briefs
  - `data/tags/defined_tags.json`
  - `data/tags/tag_synonyms.json`
  - `data/tags/archetype_constraint_brief.md`
- Offline scripts
  - `scripts/archetype_prompt_runner.py`
  - `scripts/archetype_ingredient_curator.py`
  - `scripts/archetype_curator.py` (deprecated; retained for manual QA)
  - `scripts/predefined_archetype_aggregator.py`
  - `scripts/meal_builder.py`
  - `scripts/meal_aggregate_builder.py`
- Resolver artifacts
  - `resolver/meals/meals_manifest.json`
  - `resolver/meals/meals_manifest.parquet`
- Backend (FastAPI)
  - `yummi-server/app/config.py`
  - `yummi-server/app/services/meals.py`
  - `yummi-server/app/services/preferences.py`
  - `yummi-server/app/services/filtering.py`
  - `yummi-server/app/services/exploration.py`
  - `yummi-server/app/services/recommendation.py`
  - `yummi-server/app/routes/*.py`
  - `yummi-server/app/schemas.py`
- App (Expo)
  - `thin-slice-app/App.js` → `PREFERENCES_TAGS_VERSION`, `BASE_PREFERENCE_CATEGORIES`, flows for `/v1/preferences`, `/v1/recommendations/*`, `/v1/meals*`.

---

## Predefined Archetype Workflow (Current Implementation)

- **Spreadsheet source** — `data/archetypes/predefined_archetypes.xlsx` (or `.csv`) defines one scope per row using three columns: `DietaryRestrictions` (primary diet), `Audience`, and `DietaryRestrictions2` (optional additional diet; set to `None` when unused).
- **Sync script** — Run `python scripts/predefined_archetypes_sync.py` to materialize folders under `data/archetypes/predefined/`. The script:
  - Parses Excel or `;`/`,`/`\t`-delimited CSV and normalizes headers (handles BOM).
  - Creates one folder per row with slug `<col1>_<col2>_<col3>` (e.g., `none_family_none`, `halal_family_pescatarian`).
  - Writes `config.json` containing `hard_constraints` (DietaryRestrictions array includes both `col1` and non-`None` `col3`; Audience array includes `col2`), `required_subarchetype_tags`, and `source_scope` metadata.

### Ingredient curation (new)

- **Purpose** — Trim the canonical ingredient list (`data/ingredients/unique_core_items.csv`) to only the items each archetype is likely to use when generating meals.
- **Runner** — `python scripts/archetype_ingredient_curator.py --predefined-dir data/archetypes/predefined/<slug> [--chunk-size 200] [--max-output-tokens 5000]`.
  - The script slices the catalog into fixed-size chunks (default 200 items), feeds each chunk to the LLM with the target archetype description/tags, and records any ingredient the model marks as a fit.
  - Responses are aggregated per archetype and resolved back to canonical names + `item_type` before being written.
- **State tracking** — Progress is persisted in a single file per predefined folder: `ingredient_curation/curated_ingredients.json`.
  - Reruns automatically skip archetype UIDs already present in that file; remove an entry (or delete the file) to force regeneration.
  - Use `--recurate-all` to regenerate everything regardless of prior state.
- **Artifacts** — Each run still captures prompts/responses under `ingredient_curation/run_<timestamp>/` for auditing, but only successful UIDs are added to `curated_ingredients.json`.
- **Scoped generation** — Execute the archetype runner against a folder-specific config:
  ```powershell
  python scripts/archetype_prompt_runner.py --predefined-config data/archetypes/predefined/<slug>/config.json --archetype-count 12
  ```
  The runner appends a “Scope (HARD CONSTRAINTS)” block so every generated archetype respects the required Diet×Audience combination.
- **Scoped QA (optional)** — Review `run_*/archetypes_aggregated.json` directly; the curator CLI is deprecated but remains available if you need structured keep/modify guidance.
- **Rollup** — Run `python scripts/predefined_archetype_aggregator.py` so each scope has `archetypes_combined.json` containing every unique archetype across its runs (deduped by `uid`).
- **Meal generation + aggregation** — Run `scripts/meal_builder.py --predefined-dir data/archetypes/predefined/<slug> --archetype-uid <uid|all>` so the prompt receives the scope metadata, curated ingredients, and archetype tags. Meals save to `data/meals/<slug>/<archetype_uid>/` with run logs under `data/meals/runs/<slug>/`. Use `--archetype-uid all` to create one meal per UID inside the scope, then aggregate across the folders you plan to ship.
- **Maintenance tips** — Update the sheet when adding/removing scopes; rerun the sync script (safe to execute repeatedly). Ensure diet tag values stay aligned with `defined_tags.json`; mismatches will surface later during generation.

---

## Notes

- Keep `tags_version` consistent across:
  - `data/tags/defined_tags.json`
  - `resolver/meals/meals_manifest.json` (embedded by the aggregator)
  - `thin-slice-app/App.js` constant `PREFERENCES_TAGS_VERSION`
- When in doubt, prefer expanding `tag_synonyms` to preserve backward compatibility, and migrate the database only if tag_ids themselves must change.
