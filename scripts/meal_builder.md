# Meal Builder CLI

Automates per-archetype meal generation + SKU selection using GPT-5. Each run reads the aggregated archetype JSON, canonical tags, curated ingredient lists, and normalized product catalog, then writes meals to `data/meals/<predefined_scope>/<archetype_uid>/<meal_id>.json` with run logs under `data/meals/runs/<predefined_scope>/`.

## Key Behaviors
- Requires a predefined scope directory (`--predefined-dir data/archetypes/predefined/<slug>`) so the prompt can load both `archetypes_combined.json` and the scope’s curated ingredient pool.
- Enforces canonical ingredient usage (`core_item_name` must exist in `data/ingredients/unique_core_items.csv`).
- Automatically pulls the curated ingredient list for the target archetype (from `<scope>/ingredient_curation/curated_ingredients.json`) and refuses to generate meals if the UID lacks an ingredient pool.
- Summaries of prior meals feed the prompt to avoid duplicates (lightweight: name + highlight + top ingredients).
- Instructions require two lists: `prep_steps` (mise en place) and `cook_steps` (execution). Combined `instructions` is stored for backward compatibility.
- Brand references stay out of instructions; product names only surface in `product_matches`/`final_ingredients`.
- Optional tag values (Equipment/NutritionFocus/etc.) are normalized via `data/tags/tag_synonyms.json`. Unknown values are dropped with warnings.
- Required tag categories fall back to archetype defaults; if `Allergens` is empty, heuristics infer likely allergens from ingredient text before failing the build.
- Outputs are appended as individual JSON files so meals can be reviewed/deleted without editing a monolithic manifest. Raw prompts/responses land under `data/meals/runs/<scope>/run_<timestamp>/`.

## Prerequisites
1. `OPENAI_API_KEY` set and GPT-5 access enabled.
2. Combined archetype file per predefined scope: run `python scripts/predefined_archetype_aggregator.py` so each scope exposes `<scope>/archetypes_combined.json`, or pass `--archetype-json` manually.
3. Locked tags manifest `data/tags/defined_tags.json` plus synonyms file (`data/tags/tag_synonyms.json`).
4. Canonical ingredients + product mapping: `data/ingredients/unique_core_items.csv` and `data/ingredients/ingredient_classifications.csv`.
5. Resolver catalog `resolver/catalog.json` populated with Woolworths products.

## CLI Flags (excerpt)
| Flag | Description |
| --- | --- |
| `--predefined-dir` | Predefined scope folder containing `archetypes_combined.json` and curated ingredients (required). |
| `--archetype-uid` | Target archetype UID, or `all` to process every UID inside the scope. |
| `--meal-count` | Number of new meals to generate (default 1). |
| `--meal-max-output-tokens` | GPT-5 Responses `max_output_tokens` for the recipe call. Recommended **5000** for multi-step meals. |
| `--product-max-output-tokens` | `max_output_tokens` for SKU selection; **2500** works well for ~10 ingredients. |
| `--meal-reasoning-effort` / `--product-reasoning-effort` | GPT-5 reasoning hints (`minimal`, `low`, `medium`, `high`). Defaults to `low`; override to `minimal` when testing speed. |
| `--meals-dir` | Root folder for per-archetype meal JSON (default `data/meals`). |
| `--output-dir` | Base directory for run artifacts (defaults to `data/meals`; run logs go under `runs/`). |

Run `python scripts/meal_builder.py --help` to see all options.

## Example Command
```
python .\scripts\meal_builder.py `
  --predefined-dir .\data\archetypes\predefined\none_family_none `
  --archetype-uid arch_AF01 `
  --meal-count 1 `
  --meal-max-output-tokens 5000 `
  --product-max-output-tokens 2500
```
This generates one meal for archetype `arch_AF01` inside the `none_family_none` scope. Outputs appear in `data/meals/none_family_none/arch_AF01/` plus run artifacts in `data/meals/runs/none_family_none/run_<timestamp>/`.

To test GPT-5 minimal reasoning, add `--meal-reasoning-effort minimal --product-reasoning-effort minimal`.

Batch mode example (one meal per archetype in the scope):
```
python .\scripts\meal_builder.py --predefined-dir .\data\archetypes\predefined\none_family_none --archetype-uid all --meal-count 1 --meal-max-output-tokens 5000
```
