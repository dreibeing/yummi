# Meal Builder CLI

Automates per-archetype meal generation + SKU selection using GPT-5. Each run reads the curated archetype JSON, canonical tags, and normalized ingredient catalog, then produces new meals saved under `data/meals/<archetype_uid>/<meal_id>.json`.

## Key Behaviors
- Enforces canonical ingredient usage (`core_item_name` must exist in `data/ingredients/unique_core_items.csv`).
- Summaries of prior meals feed the prompt to avoid duplicates (lightweight: name + highlight + top ingredients).
- Instructions require two lists: `prep_steps` (mise en place) and `cook_steps` (execution). Combined `instructions` is stored for backward compatibility.
- Brand references stay out of instructions; product names only surface in `product_matches`/`final_ingredients`.
- Optional tag values (Equipment/NutritionFocus/etc.) are normalized via `data/tags/tag_synonyms.json`. Unknown values are dropped with warnings.
- Required tag categories fall back to archetype defaults; if `Allergens` is empty, heuristics infer likely allergens from ingredient text before failing the build.
- Outputs are appended as individual JSON files so meals can be reviewed/deleted without editing a monolithic manifest. Raw prompts/responses land under `data/meals/runs/run_<timestamp>/`.

## Prerequisites
1. `OPENAI_API_KEY` set and GPT-5 access enabled.
2. Curated archetypes: `data/archetypes/run_20251112T091259Z/curation/archetypes_curated.json` (or pass `--archetype-json`).
3. Locked tags manifest `data/tags/defined_tags.json` plus synonyms file (`data/tags/tag_synonyms.json`).
4. Canonical ingredients + product mapping: `data/ingredients/unique_core_items.csv` and `data/ingredients/ingredient_classifications.csv`.
5. Resolver catalog `resolver/catalog.json` populated with Woolworths products.

## CLI Flags (excerpt)
| Flag | Description |
| --- | --- |
| `--archetype-uid` | Target archetype UID (required). |
| `--meal-count` | Number of new meals to generate (default 1). |
| `--meal-max-output-tokens` | GPT-5 Responses `max_output_tokens` for the recipe call. Recommended **5000** for multi-step meals. |
| `--product-max-output-tokens` | `max_output_tokens` for SKU selection; **2500** works well for ~10 ingredients. |
| `--meal-reasoning-effort` / `--product-reasoning-effort` | GPT-5 reasoning hints (`minimal`, `low`, `medium`, `high`). Defaults to `low`; override to `minimal` when testing speed. |
| `--meals-dir` | Root folder for per-archetype meal JSON (default `data/meals`). |
| `--output-dir` | Base directory for run artifacts (defaults to `data/meals`; run logs go under `runs/`). |

Run `python scripts/meal_builder.py --help` to see all options.

## Example Command
```
python .\scripts\meal_builder.py --archetype-uid arch_3f4k9z --meal-count 3 --meal-max-output-tokens 5000 --product-max-output-tokens 2500
```
This will generate 3 meals for archetype `arch_3f4k9z`, allowing generous budgets for both GPT calls. Outputs appear in `data/meals/arch_3f4k9z/` plus run artifacts in `data/meals/runs/run_<timestamp>/`.

To test GPT-5 minimal reasoning, add `--meal-reasoning-effort minimal --product-reasoning-effort minimal`.
