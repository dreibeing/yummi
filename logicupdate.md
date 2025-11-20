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
    {"tag_id":"dietres_none","category":"DietaryRestrictions","value":"None","description":"No dietary restrictions; eats all foods","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"dietres_vegan","category":"DietaryRestrictions","value":"Vegan","description":"Excludes all animal-derived products","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"dietres_vegetarian","category":"DietaryRestrictions","value":"Vegetarian","description":"No meat or seafood; allows dairy/eggs","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"dietres_pescatarian","category":"DietaryRestrictions","value":"Pescatarian","description":"Seafood allowed; no poultry or red meat","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"dietres_halal","category":"DietaryRestrictions","value":"Halal","description":"Prepared with halal-certified ingredients and processes","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"dietres_kosher","category":"DietaryRestrictions","value":"Kosher","description":"Meets kosher certification requirements; supervised sourcing and preparation","is_required_for_archetype":true,"is_required_for_meal":true},

    {"tag_id":"cuisine_southaf","category":"Cuisine","value":"SouthAfrican","description":"Braai, bunny chow, bobotie, chakalaka, pap and samp with peri-peri or chutney heat","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_american","category":"Cuisine","value":"American","description":"Burgers, BBQ ribs or pulled pork, fried chicken, mac & cheese, chili, casseroles, subs, wings, hearty dinner salads","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_mexican","category":"Cuisine","value":"Mexican","description":"Tacos, burritos, enchiladas, quesadillas, pozole, chilaquiles, rice and bean bowls with corn masa, salsas, chiles, lime","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_caribbean","category":"Cuisine","value":"Caribbean","description":"Jerk chicken or pork, curry goat, oxtail stew, rice and peas, plantains, roti; allspice, thyme, scotch bonnet heat","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_latin","category":"Cuisine","value":"LatinAmerican","description":"Ceviche, lomo saltado, feijoada, moqueca, asado plates, arepas, empanadas with ají peppers, chimichurri, rice and beans","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_italian","category":"Cuisine","value":"Italian","description":"Pastas, risotto, pizza, parmigiana, minestrone; tomato, basil, olive oil, Parmigiano-centered dishes","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_french","category":"Cuisine","value":"French","description":"Bistro braises like coq au vin or boeuf bourguignon, quiche, ratatouille, steak frites; butter and wine sauces, Provençal herbs","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_greek","category":"Cuisine","value":"Greek","description":"Souvlaki, gyros, moussaka, spanakopita, Greek salads; lemon, oregano, feta, olive oil, yogurt, pita accompaniments","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_turkish","category":"Cuisine","value":"Turkish","description":"Kebabs, köfte, pide, lahmacun, dolma, meze spreads; yogurt sauces with sumac, Aleppo pepper, pomegranate molasses","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_mideast","category":"Cuisine","value":"MiddleEastern","description":"Shawarma or kofta, falafel, hummus and mezze, pilafs, fattoush; tahini, za’atar, sumac, saffron, warm flatbreads","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_northaf","category":"Cuisine","value":"NorthAfrican","description":"Tagines, couscous platters, chermoula fish, harira soup; cumin, coriander, cinnamon, preserved lemon, harissa spice","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_indian","category":"Cuisine","value":"Indian","description":"Curries, biryani, dal, tandoori grills, chaat, naan or roti, dosa; garam masala, cumin, turmeric","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_chinese","category":"Cuisine","value":"Chinese","description":"Stir-fries, fried rice, chow mein, dumplings, hot pot, mapo tofu; soy, vinegar, ginger, garlic, five-spice or Sichuan pepper","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_japanese","category":"Cuisine","value":"Japanese","description":"Sushi and sashimi, ramen, udon, tempura, katsu, donburi, yakitori; dashi, miso, soy, yuzu, meticulous presentation","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_korean","category":"Cuisine","value":"Korean","description":"Bulgogi or galbi, bibimbap, kimchi stew, soft tofu jjigae, japchae; gochujang, gochugaru, sesame oil, fermented banchan","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_thai","category":"Cuisine","value":"Thai","description":"Green, red, or yellow curries, pad thai, pad kra pao, tom yum or tom kha soups; Thai basil, lime, sweet-salty-sour-spicy balance","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_vietnamese","category":"Cuisine","value":"Vietnamese","description":"Pho, bun cha, bun bo Hue, banh mi, goi cuon; fresh herbs, nuoc cham, crisp vegetables, light aromatic broths","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_portuguese","category":"Cuisine","value":"Portuguese","description":"Peri-peri grilled meats, bacalhau, caldo verde, bifana sandwiches; garlic, bay leaf, paprika, vinegar, seafood emphasis","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"cuisine_spanish","category":"Cuisine","value":"Spanish","description":"Paella, tapas selections, tortilla española, gazpacho, hearty stews; olive oil, saffron, smoked paprika, seafood-forward plates","is_required_for_archetype":true,"is_required_for_meal":true},





    {"tag_id":"preptime_less15","category":"PrepTime","value":"Under15","description":"Less than 15 minutes hands-on","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"preptime_15_30","category":"PrepTime","value":"15to30","description":"15 to 30 minutes hands-on","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"preptime_30_60","category":"PrepTime","value":"30to60","description":"30 minutes to 1 hour build","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"preptime_60_plus","category":"PrepTime","value":"60Plus","description":"1 hour plus or multi-step","is_required_for_archetype":true,"is_required_for_meal":true},

    {"tag_id":"complex_easy","category":"Complexity","value":"Simple","description":"Beginner-friendly steps","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"complex_mid","category":"Complexity","value":"Intermediate","description":"Multiple components but approachable","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"complex_adv","category":"Complexity","value":"Advanced","description":"Advanced timing or techniques","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"complex_show","category":"Complexity","value":"Showstopper","description":"High effort, presentation-focused","is_required_for_archetype":true,"is_required_for_meal":true},

    {"tag_id":"heat_none","category":"HeatSpice","value":"NoHeat","description":"0 heat; suitable for sensitive palettes","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"heat_mild","category":"HeatSpice","value":"Mild","description":"Noticeable warmth but kid-friendly","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"heat_medium","category":"HeatSpice","value":"Medium","description":"Balanced heat for most adults","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"heat_hot","category":"HeatSpice","value":"Hot","description":"Bold chili presence","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"heat_extra","category":"HeatSpice","value":"ExtraHot","description":"Significant burn; for spice lovers","is_required_for_archetype":true,"is_required_for_meal":true},

    {"tag_id":"audience_solo","category":"Audience","value":"Solo","description":"1 serving meals for individuals","is_required_for_archetype":true,"is_required_for_meal":false},
    {"tag_id":"audience_couple","category":"Audience","value":"Couple","description":"2 servings sized for two adults","is_required_for_archetype":true,"is_required_for_meal":false},
    {"tag_id":"audience_family","category":"Audience","value":"Family","description":"4 servings covering a typical household","is_required_for_archetype":true,"is_required_for_meal":false},
    {"tag_id":"audience_largefamily","category":"Audience","value":"LargeFamily","description":"6 servings designed for big families","is_required_for_archetype":true,"is_required_for_meal":false},
    {"tag_id":"audience_extendedfamily","category":"Audience","value":"ExtendedFamily","description":"8 servings for extended family","is_required_for_archetype":true,"is_required_for_meal":false},




    {"tag_id":"allergen_dairy","category":"Allergens","value":"Dairy","description":"Contains milk or lactose ingredients","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"allergen_egg","category":"Allergens","value":"Egg","description":"Contains eggs or egg products","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"allergen_gluten","category":"Allergens","value":"Gluten","description":"Contains wheat/barley/rye gluten","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"allergen_soy","category":"Allergens","value":"Soy","description":"Contains soybeans or soy derivatives","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"allergen_nuts","category":"Allergens","value":"Nuts","description":"Contains peanuts or tree nuts","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"allergen_seafood","category":"Allergens","value":"Seafood","description":"Contains finned fish or shellfish","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"allergen_sesame","category":"Allergens","value":"Sesame","description":"Contains sesame seeds or oil","is_required_for_archetype":true,"is_required_for_meal":true},

    {"tag_id":"nutrition_highprotein","category":"NutritionFocus","value":"HighProtein","description":"Meals optimized for high protein density per serving","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"nutrition_lowcalorie","category":"NutritionFocus","value":"LowCalorie","description":"Meals designed to stay within a ≤500–600 kcal per serving threshold","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"nutrition_lowcarb","category":"NutritionFocus","value":"LowCarb","description":"Meals moderate in carbohydrates without strict keto limits","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"nutrition_keto","category":"NutritionFocus","value":"Keto","description":"Strict low-carbohydrate, high-fat macro profile for ketogenic diets","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"nutrition_lowfat","category":"NutritionFocus","value":"LowFat","description":"Meals intentionally low in total and saturated fat","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"nutrition_lowsodium","category":"NutritionFocus","value":"LowSodium","description":"Meals formulated to keep sodium levels lower than standard recipes","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"nutrition_highfiber","category":"NutritionFocus","value":"HighFiber","description":"Meals containing elevated dietary fiber per serving","is_required_for_archetype":false,"is_required_for_meal":false},

    {"tag_id":"equip_oven","category":"Equipment","value":"Oven","description":"Requires oven baking or roasting","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"equip_countertop_cooker","category":"Equipment","value":"SlowOrPressureCooker","description":"Has slow cooker or Instant Pot/pressure cooker","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"equip_airfryer","category":"Equipment","value":"AirFryer","description":"Uses air fryer basket","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"equip_microwave","category":"Equipment","value":"Microwave","description":"Needs microwave-safe cooking or reheating","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"equip_stove","category":"Equipment","value":"StoveTop","description":"Requires stove for boiling or pan cooking","is_required_for_archetype":false,"is_required_for_meal":false},
    {"tag_id":"equip_grill","category":"Equipment","value":"OutdoorGrill","description":"Needs braai/grill access","is_required_for_archetype":false,"is_required_for_meal":false},

    {"tag_id":"mealcomp_fromscratch","category":"MealComponentPreference","value":"FromScratch","description":"Prefers meals made from baseline raw ingredients; sauces and components are cooked from scratch.","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"mealcomp_semiprepared","category":"MealComponentPreference","value":"SemiPrepared","description":"Prefers combining partially prepared or ready-to-cook items with simple scratch components.","is_required_for_archetype":true,"is_required_for_meal":true},
    {"tag_id":"mealcomp_readymeal","category":"MealComponentPreference","value":"ReadyMealPreferred","description":"Prefers fully prepared ready meals or heat-and-eat dishes, with minimal additional cooking.","is_required_for_archetype":true,"is_required_for_meal":true}
  ]
}
```

---

## E2E Touchpoints We’ll Update

1) Tag Vocabulary and Constraints
- Source of truth: `data/tags/defined_tags.json` (this file).
- Normalization helpers: `data/tags/tag_synonyms.json` (used to map or drop non‑canonical values).
- Constraint brief for coverage: `data/tags/archetype_constraint_brief.md` (referenced by generation/curation prompts).

2) Offline Generation and Aggregation
- Archetype generation: `scripts/archetype_prompt_runner.py` reads `defined_tags.json` to embed `tags_version` and required category lists in prompts and snapshots.
- Archetype curation: `scripts/archetype_curator.py` produces `curation/archetypes_curated.json` with coverage notes.
- Meal generation (LLM + SKU selection): `scripts/meal_builder.py`
  - Loads `defined_tags.json` to build the category→values catalog and enforce `ALWAYS_REQUIRED_MEAL_CATEGORIES` for every meal.
  - Applies `data/tags/tag_synonyms.json` when model returns non‑canonical values.
  - Infers missing `Allergens` from ingredient text where needed.
- Meal aggregation: `scripts/meal_aggregate_builder.py`
  - Reads curated archetypes + per‑meal JSON to build `resolver/meals/meals_manifest.json` (and optional Parquet).
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
- Regenerate archetypes (`scripts/archetype_prompt_runner.py`) and curate (`scripts/archetype_curator.py`).

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
  - `scripts/archetype_curator.py`
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

## Notes

- Keep `tags_version` consistent across:
  - `data/tags/defined_tags.json`
  - `resolver/meals/meals_manifest.json` (embedded by the aggregator)
  - `thin-slice-app/App.js` constant `PREFERENCES_TAGS_VERSION`
- When in doubt, prefer expanding `tag_synonyms` to preserve backward compatibility, and migrate the database only if tag_ids themselves must change.

