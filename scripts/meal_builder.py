#!/usr/bin/env python3
"""Generate archetype-aligned meals and map them to real Woolworths products."""

from __future__ import annotations

import argparse
import csv
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Sequence

from llm_utils import OpenAIClientError, call_openai_api


DEFAULT_TAGS_MANIFEST = Path("data/tags/defined_tags.json")
DEFAULT_TAG_SYNONYMS = Path("data/tags/tag_synonyms.json")
DEFAULT_CORE_ITEMS = Path("data/ingredients/unique_core_items.csv")
DEFAULT_CLASSIFICATIONS = Path("data/ingredients/ingredient_classifications.csv")
DEFAULT_CATALOG = Path("resolver/catalog.json")
DEFAULT_MEALS_DIR = Path("data/meals")
DEFAULT_OUTPUT_DIR = Path("data/meals")

DEFAULT_MEAL_MODEL = "gpt-5"
DEFAULT_PRODUCT_MODEL = "gpt-5"

CURATED_INGREDIENTS_FILENAME = "curated_ingredients.json"
INGREDIENT_CURATION_SUBDIR = "ingredient_curation"
ARH_COMBINED_FILENAME = "archetypes_combined.json"

MEAL_TAG_CATEGORY_ORDER = [
    "DietaryRestrictions",
    "Audience",
    "Cuisine",
    "PrepTime",
    "Complexity",
    "HeatSpice",
    "Allergens",
    "NutritionFocus",
    "Equipment",
    "MealComponentPreference",
]

MULTI_VALUE_TAG_CATEGORIES = {
    "Cuisine",
    "Equipment",
    "MealComponentPreference",
}

CRITICAL_MATCH_CATEGORIES = ("DietaryRestrictions", "Audience")

ALLERGEN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Gluten": (
        "bread",
        "wheat",
        "flour",
        "pasta",
        "noodle",
        "roti",
        "chapati",
        "naan",
        "paratha",
        "wrap",
        "bun",
        "tortilla",
        "couscous",
        "barley",
        "rye",
        "seitan",
        "farro",
    ),
    "Dairy": (
        "cheese",
        "milk",
        "butter",
        "yogurt",
        "cream",
        "paneer",
        "ghee",
        "ricotta",
        "mascarpone",
    ),
    "Egg": (
        "egg",
        "mayo",
        "mayonnaise",
    ),
    "Soy": (
        "soy",
        "tofu",
        "tempeh",
        "edamame",
        "miso",
        "tamari",
    ),
    "Peanut": ("peanut", "groundnut"),
    "TreeNut": (
        "almond",
        "cashew",
        "walnut",
        "pecan",
        "hazelnut",
        "pistachio",
        "macadamia",
        "brazil nut",
        "pine nut",
    ),
    "Sesame": ("sesame", "tahini", "gomashio"),
    "Mustard": ("mustard",),
    "Fish": ("salmon", "tuna", "fish", "anchovy", "sardine", "hake"),
    "Shellfish": ("shrimp", "prawn", "lobster", "crab", "mussel", "clam", "scallop"),
}


@dataclass
class CoreItem:
    name: str
    item_type: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--predefined-dir",
        type=Path,
        required=True,
        help="Predefined archetype scope directory (contains archetypes_combined.json and curated ingredients).",
    )
    parser.add_argument("--archetype-uid", required=True, help="UID of the archetype to generate meals for")
    parser.add_argument("--meal-count", type=int, default=1, help="Number of meals to create in this run")
    parser.add_argument(
        "--archetype-json",
        type=Path,
        default=None,
        help="Path to archetype JSON (aggregated run output). Defaults to <predefined-dir>/archetypes_combined.json.",
    )
    parser.add_argument("--tags-manifest", type=Path, default=DEFAULT_TAGS_MANIFEST, help="Path to defined_tags manifest")
    parser.add_argument("--core-items", type=Path, default=DEFAULT_CORE_ITEMS, help="CSV of canonical core items")
    parser.add_argument(
        "--curated-ingredients",
        type=Path,
        default=None,
        help="Path to curated ingredient list. Defaults to <predefined-dir>/ingredient_curation/curated_ingredients.json.",
    )
    parser.add_argument(
        "--ingredient-classifications",
        type=Path,
        default=DEFAULT_CLASSIFICATIONS,
        help="CSV mapping product IDs to core items (from ingredient classification run)",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG, help="Resolver catalog JSON with Woolworths products")
    parser.add_argument(
        "--meals-dir",
        type=Path,
        default=DEFAULT_MEALS_DIR,
        help="Directory containing per-archetype meal files (one JSON per meal)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Base directory for run artifacts + meal store")
    parser.add_argument(
        "--tag-synonyms",
        type=Path,
        default=DEFAULT_TAG_SYNONYMS,
        help="Optional JSON mapping of category -> {raw_value: canonical_value|null} to normalize model tags",
    )
    parser.add_argument("--meal-model", default=DEFAULT_MEAL_MODEL, help="OpenAI model for the meal generation call")
    parser.add_argument("--meal-temperature", type=float, default=0.4)
    parser.add_argument("--meal-top-p", type=float, default=None)
    parser.add_argument("--meal-max-output-tokens", type=int, default=1600)
    parser.add_argument("--meal-reasoning-effort", default="low")
    parser.add_argument("--product-model", default=DEFAULT_PRODUCT_MODEL, help="OpenAI model for SKU selection")
    parser.add_argument("--product-temperature", type=float, default=0.2)
    parser.add_argument("--product-top-p", type=float, default=None)
    parser.add_argument("--product-max-output-tokens", type=int, default=1200)
    parser.add_argument("--product-reasoning-effort", default="low")
    parser.add_argument("--product-candidate-limit", type=int, default=5, help="Max SKU candidates to supply per ingredient")
    parser.add_argument("--existing-meal-summary-count", type=int, default=8, help="How many prior meals to summarize for the prompt")
    return parser.parse_args(list(argv) if argv is not None else None)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_archetypes(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    archetypes = payload.get("archetypes") or []
    if not archetypes:
        raise RuntimeError(f"No archetypes found in {path}")
    return archetypes


def find_archetype(uid: str, archetypes: list[dict[str, Any]]) -> dict[str, Any]:
    for item in archetypes:
        if item.get("uid") == uid:
            return item
    raise RuntimeError(f"Archetype uid '{uid}' not found")


def load_tags_manifest(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    if "tags_version" not in manifest:
        raise RuntimeError(f"tags manifest missing tags_version: {path}")
    return manifest


def load_tag_synonyms(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    payload = read_json(path)
    normalized: dict[str, dict[str, Any]] = {}
    for category, mapping in payload.items():
        if not isinstance(mapping, dict):
            continue
        normalized[category] = {str(key): mapping[key] for key in mapping}
    return normalized


def load_curated_ingredient_sets(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        raise FileNotFoundError(f"Curated ingredient file not found: {path}")
    payload = read_json(path)
    entries = payload.get("archetype_ingredient_sets") or []
    if not entries:
        raise RuntimeError(f"No curated ingredient sets found in {path}")
    curated: dict[str, list[str]] = {}
    for entry in entries:
        uid = entry.get("uid")
        if not uid:
            continue
        names: list[str] = []
        for raw in entry.get("ingredient_names") or []:
            name = str(raw).strip()
            if name:
                names.append(name)
        if not names:
            continue
        curated[uid] = names
    if not curated:
        raise RuntimeError(f"Curated ingredient file {path} did not yield usable entries")
    return curated


def build_tag_catalog(manifest: dict[str, Any]) -> dict[str, set[str]]:
    catalog: dict[str, set[str]] = {}
    for entry in manifest.get("defined_tags", []):
        category = entry.get("category")
        value = entry.get("value")
        if not category or not value:
            continue
        bucket = catalog.setdefault(category, set())
        bucket.add(value)
    return catalog


def build_tag_value_reference(tag_catalog: dict[str, set[str]], categories: Sequence[str]) -> dict[str, list[str]]:
    reference: dict[str, list[str]] = {}
    for category in categories:
        values = sorted(tag_catalog.get(category, []))
        if values:
            reference[category] = values
    return reference


def load_core_items(path: Path) -> tuple[list[CoreItem], dict[str, CoreItem]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing core items CSV: {path}")
    items: list[CoreItem] = []
    index: dict[str, CoreItem] = {}
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get("core_item_name") or "").strip()
            item_type = (row.get("item_type") or "").strip()
            if not name:
                continue
            item = CoreItem(name=name, item_type=item_type or "ingredient")
            items.append(item)
            index[name.lower()] = item
    if not items:
        raise RuntimeError(f"No rows found in {path}")
    return items, index


def select_allowed_core_items(
    archetype_uid: str,
    curated_sets: dict[str, list[str]],
    core_item_index: dict[str, CoreItem],
) -> tuple[list[CoreItem], list[str]]:
    names = curated_sets.get(archetype_uid)
    if not names:
        raise RuntimeError(f"No curated ingredients found for archetype '{archetype_uid}'")
    unique_names: set[str] = set()
    allowed: list[CoreItem] = []
    missing: list[str] = []
    for raw_name in names:
        key = raw_name.lower()
        if key in unique_names:
            continue
        unique_names.add(key)
        item = core_item_index.get(key)
        if item:
            allowed.append(item)
        else:
            missing.append(raw_name)
    allowed.sort(key=lambda item: item.name.lower())
    return allowed, missing


def load_classifications(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing ingredient classifications CSV: {path}")
    mapping: dict[str, list[dict[str, Any]]] = {}
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            core_name = (row.get("core_item_name") or "").strip()
            product_id = (row.get("product_id") or "").strip()
            if not core_name or not product_id:
                continue
            mapping.setdefault(core_name.lower(), []).append({
                "core_item_name": core_name,
                "product_id": product_id,
                "item_type": row.get("item_type"),
                "batch_id": row.get("batch_id"),
            })
    return mapping


def load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    by_id: dict[str, dict[str, Any]] = {}
    for _, product in payload.items():
        product_id = str(product.get("productId") or product.get("product_id") or "").strip()
        if not product_id:
            continue
        product_copy = dict(product)
        product_copy.setdefault("display_name", product.get("name"))
        by_id[product_id] = product_copy
    if not by_id:
        raise RuntimeError(f"Resolver catalog {path} did not yield any product entries")
    return by_id


def load_existing_meals(base_dir: Path, scope_slug: str, archetype_uid: str) -> list[dict[str, Any]]:
    archetype_dir = base_dir / scope_slug / archetype_uid
    if not archetype_dir.exists():
        return []
    records: list[dict[str, Any]] = []
    for meal_path in sorted(archetype_dir.glob("*.json")):
        try:
            record = json.loads(meal_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[warn] Skipping unreadable meal file {meal_path}: {exc}")
            continue
        records.append(record)
    return records


def save_meal_record(base_dir: Path, scope_slug: str, archetype_uid: str, meal_record: dict[str, Any]) -> Path:
    archetype_dir = base_dir / scope_slug / archetype_uid
    archetype_dir.mkdir(parents=True, exist_ok=True)
    path = archetype_dir / f"{meal_record['meal_id']}.json"
    path.write_text(json.dumps(meal_record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_run_dir(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    attempt = 0
    while True:
        slug = timestamp_slug()
        run_dir = base_dir / f"run_{slug}"
        if not run_dir.exists():
            run_dir.mkdir(parents=True, exist_ok=False)
            return run_dir
        attempt += 1
        if attempt > 5:
            raise RuntimeError("Could not create a unique run directory after multiple attempts")


def archetype_summary(archetypes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in archetypes:
        tags = entry.get("core_tags") or {}
        summary.append(
            {
                "uid": entry.get("uid"),
                "name": entry.get("name"),
                "Diet": tags.get("Diet"),
                "Cuisine": tags.get("Cuisine"),
                "Audience": tags.get("Audience"),
                "BudgetLevel": tags.get("BudgetLevel"),
                "Complexity": tags.get("Complexity"),
                "PrepTime": tags.get("PrepTime"),
            }
        )
    return summary


def summarize_existing_meals(meals: list[dict[str, Any]], archetype_uid: str, limit: int) -> list[dict[str, Any]]:
    relevant = [meal for meal in meals if meal.get("archetype_uid") == archetype_uid]
    relevant = relevant[-limit:] if limit and len(relevant) > limit else relevant
    summary: list[dict[str, Any]] = []
    for meal in relevant:
        tags = meal.get("meal_tags") or {}
        highlight_tags = {
            "DietaryRestrictions": ",".join(tags.get("DietaryRestrictions", [])[:1]),
            "Cuisine": ",".join(tags.get("Cuisine", [])[:2]),
            "PrepTime": ",".join(tags.get("PrepTime", [])[:1]),
            "Complexity": ",".join(tags.get("Complexity", [])[:1]),
        }
        short_ingredients = ", ".join([ing.get("core_item_name") for ing in meal.get("ingredients", [])][:4])
        summary.append(
            {
                "meal_id": meal.get("meal_id"),
                "name": meal.get("name"),
                "highlight": f"DietaryRestrictions={highlight_tags['DietaryRestrictions'] or '-'}; Cuisine={highlight_tags['Cuisine'] or '-'}; Prep={highlight_tags['PrepTime'] or '-'}; Complexity={highlight_tags['Complexity'] or '-'}",
                "key_ingredients": short_ingredients,
            }
        )
    return summary


def determine_required_categories(tag_catalog: dict[str, set[str]]) -> list[str]:
    ordered: list[str] = [category for category in MEAL_TAG_CATEGORY_ORDER if category in tag_catalog]
    remaining = sorted(category for category in tag_catalog.keys() if category not in MEAL_TAG_CATEGORY_ORDER)
    return ordered + remaining


def infer_allergens_from_ingredients(ingredients: list[dict[str, Any]]) -> list[str]:
    detected: set[str] = set()
    texts: list[str] = []
    for ingredient in ingredients or []:
        parts = [
            (ingredient.get("core_item_name") or "").lower(),
            (ingredient.get("quantity") or "").lower(),
            (ingredient.get("preparation") or "").lower(),
        ]
        texts.append(" ".join(part for part in parts if part))
    for allergen, keywords in ALLERGEN_KEYWORDS.items():
        for text in texts:
            if any(keyword in text for keyword in keywords):
                detected.add(allergen)
                break
    return sorted(detected)


def normalize_tag_value(
    *,
    category: str,
    value: str,
    allowed_values: set[str] | None,
    tag_synonyms: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    raw_value = (value or "").strip()
    if not raw_value:
        return None, None
    if allowed_values and raw_value in allowed_values:
        return raw_value, None

    synonym_map = tag_synonyms.get(category) or {}
    if raw_value in synonym_map:
        canonical = synonym_map[raw_value]
        if canonical is None:
            return None, f"Category '{category}': dropped '{raw_value}' via synonym mapping"
        canonical_str = str(canonical)
        if allowed_values and canonical_str not in allowed_values:
            return None, f"Category '{category}': synonym '{raw_value}' -> '{canonical_str}' not present in manifest"
        return canonical_str, f"Category '{category}': normalized '{raw_value}' -> '{canonical_str}'"

    if allowed_values:
        return None, f"Category '{category}': value '{raw_value}' not in defined_tags"
    return None, f"Category '{category}' not in manifest; dropped '{raw_value}'"


def scrub_json_block(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    return stripped.strip()


def build_meal_system_prompt() -> str:
    return dedent(
        """
        You are a culinary planner who produces JSON meal cards. Every response must be strictly valid JSON.
        Use only the provided canonical ingredients list and respect the archetype's diet, allergens, and household sizing.
        Keep instructions practical (step-by-step) and avoid flowery language.
        """
    ).strip()


def build_meal_user_prompt(
    *,
    archetype: dict[str, Any],
    archetypes_summary: list[dict[str, Any]],
    existing_meals_summary: list[dict[str, Any]],
    allowed_core_items: list[CoreItem],
    tags_version: str,
    tag_value_reference: dict[str, list[str]],
    required_categories: Sequence[str],
    multi_value_categories: set[str],
    meals_requested: int,
) -> str:
    archetype_json = json.dumps(archetype, ensure_ascii=False, indent=2)
    summary_json = json.dumps(archetypes_summary, ensure_ascii=False, indent=2)
    existing_json = json.dumps(existing_meals_summary, ensure_ascii=False, indent=2)
    allowed_json = json.dumps(
        [{"core_item_name": item.name, "item_type": item.item_type} for item in allowed_core_items],
        ensure_ascii=False,
        indent=2,
    )
    tag_reference_rows = [
        {
            "category": category,
            "allowed_values": tag_value_reference.get(category, []),
            "min_values": 2 if category in multi_value_categories else 1,
        }
        for category in required_categories
    ]
    tag_reference_json = json.dumps(tag_reference_rows, ensure_ascii=False, indent=2)

    schema = {
        "meal": {
            "name": "string",
            "description": "1–2 sentences",
            "servings": "string (e.g., 'Serves 2 adults')",
            "prep_steps": ["ordered strings describing ingredient prep, chopping, preheating"],
            "cook_steps": ["ordered strings describing actual cooking/assembly"],
            "ingredients": [
                {
                    "core_item_name": "must match curated ingredient exactly",
                    "quantity": "human-readable quantity",
                    "preparation": "optional prep note",
                }
            ],
            "meal_tags": {
                "DietaryRestrictions": ["values"],
                "Audience": ["values"],
                "Cuisine": ["values"],
                "PrepTime": ["values"],
                "Complexity": ["values"],
                "HeatSpice": ["values"],
                "Allergens": ["values"],
                "NutritionFocus": ["values"],
                "Equipment": ["values"],
                "MealComponentPreference": ["values"],
            },
        }
    }

    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)
    multi_value_text = ", ".join(sorted(multi_value_categories)) or "None"
    critical_text = ", ".join(CRITICAL_MATCH_CATEGORIES)
    instructions = dedent(
        f"""
        Task: Generate {meals_requested} new meal(s) for the target archetype (see JSON below).
        Hard requirements:
        - Use only the curated ingredients listed under "Allowed core items"; do not introduce items that are not listed.
        - Every ingredient must remain individually shoppable: keep `core_item_name` identical to the curated list entry that inspired it so the downstream allocator can attach a product SKU. If a recipe concept requires ingredients beyond the allowed list, pick a different concept that fits.
        - The meal must explicitly satisfy the archetype's DietaryRestrictions and Audience tags; include those exact values in `meal_tags`.
        - Serve the household described by the archetype (Audience) and keep effort aligned to its Complexity tag (simpler households need simpler instructions).
        - Provide servings guidance (e.g., "Serves 4" or "Single portion").
        - Keep directions practical and step-by-step: split into `prep_steps` (mise en place) and `cook_steps` (actual cooking/assembly) with short, direct actions appropriate for the archetype's skill level.
        - For every tag category listed in the table below, provide at least one canonical value (tags_version {tags_version}). Categories marked as multi valued should include two or more values when naturally true.
        - If a category truly has no special focus, choose the value "None" (when available) rather than leaving the list empty.
        - Avoid duplicating existing meal names or core ideas shown in the "Existing meals" section.
        - Return STRICT JSON following the schema exactly (double quotes, no trailing commentary).

        Tag coverage plan (use these canonical values):
        ```json
        {tag_reference_json}
        ```

        Categories that often need multiple values: {multi_value_text}
        Critical categories that must mirror the archetype: {critical_text}

        Target archetype (uid + tags supplied to the LLM generating ingredients):
        ```json
        {archetype_json}
        ```

        Snapshot of all archetypes (for coverage awareness only; do NOT reference them explicitly):
        ```json
        {summary_json}
        ```

        Existing meals for this archetype (avoid repeats):
        ```json
        {existing_json}
        ```

        Allowed core items (curated list for this archetype):
        ```json
        {allowed_json}
        ```

        Output schema:
        ```json
        {schema_json}
        ```
        """
    ).strip()
    return instructions


def parse_meal_response(text: str) -> dict[str, Any]:
    body = scrub_json_block(text)
    data = json.loads(body)
    if "meal" not in data:
        raise ValueError("Model response missing 'meal' root object")
    return data["meal"]


def validate_meal_payload(
    meal: dict[str, Any],
    *,
    required_categories: Sequence[str],
    tag_catalog: dict[str, set[str]],
    core_item_index: dict[str, CoreItem],
    tag_synonyms: dict[str, dict[str, Any]],
    archetype_tags: dict[str, Any],
    multi_value_categories: set[str],
) -> list[str]:
    missing_fields = [
        key
        for key in ("name", "description", "servings", "prep_steps", "cook_steps", "ingredients", "meal_tags")
        if key not in meal
    ]
    if missing_fields:
        raise ValueError(f"Meal payload missing fields: {missing_fields}")

    for field in ("prep_steps", "cook_steps"):
        steps = meal.get(field)
        if not isinstance(steps, list):
            raise ValueError(f"Meal field '{field}' must be a list")
        if field == "cook_steps" and not steps:
            raise ValueError("Meal cook_steps must contain at least one instruction")
        if field == "prep_steps" and steps is None:
            meal[field] = []
    if not isinstance(meal.get("ingredients"), list) or not meal["ingredients"]:
        raise ValueError("Meal ingredients must be a non-empty list")

    for ingredient in meal["ingredients"]:
        core_name = (ingredient.get("core_item_name") or "").strip().lower()
        if core_name not in core_item_index:
            raise ValueError(f"Ingredient '{ingredient.get('core_item_name')}' not in canonical core item list")

    tags = meal.get("meal_tags") or {}
    warnings: list[str] = []
    sanitized_tags: dict[str, list[str]] = {}

    for category, values in tags.items():
        if not isinstance(values, list):
            raise ValueError(f"Tag category '{category}' must be a list")
        allowed_values = tag_catalog.get(category)
        if not allowed_values:
            warnings.append(f"Category '{category}' not defined in tags manifest; dropping values")
            continue

        normalized_values: list[str] = []
        for value in values:
            canonical, note = normalize_tag_value(
                category=category,
                value=value,
                allowed_values=allowed_values,
                tag_synonyms=tag_synonyms,
            )
            if canonical:
                normalized_values.append(canonical)
            if note:
                warnings.append(note)

        if not normalized_values:
            if category in required_categories:
                raise ValueError(f"Meal tags missing required categories: {category}")
            warnings.append(f"Category '{category}' dropped; no valid values after normalization")
            continue

        sanitized_tags[category] = sorted(set(normalized_values))

    if not sanitized_tags.get("Allergens"):
        inferred_allergens = infer_allergens_from_ingredients(meal.get("ingredients", []))
        if inferred_allergens:
            sanitized_tags["Allergens"] = inferred_allergens
            warnings.append(
                "Inferred allergens from ingredient list: " + ", ".join(inferred_allergens)
            )

    remaining_missing: list[str] = []
    for category in required_categories:
        if sanitized_tags.get(category):
            continue
        fallback_values = None
        if category in CRITICAL_MATCH_CATEGORIES and isinstance(archetype_tags, dict):
            fallback_values = archetype_tags.get(category)
        if fallback_values:
            values = fallback_values if isinstance(fallback_values, list) else [fallback_values]
            sanitized_tags[category] = values
            warnings.append(f"Filled missing category '{category}' from archetype defaults")
            continue
        remaining_missing.append(category)
    if remaining_missing:
        raise ValueError(f"Meal tags missing required categories: {remaining_missing}")

    for category in CRITICAL_MATCH_CATEGORIES:
        archetype_values = archetype_tags.get(category) if isinstance(archetype_tags, dict) else None
        if not archetype_values:
            continue
        expected_values = archetype_values if isinstance(archetype_values, list) else [archetype_values]
        provided = sanitized_tags.get(category, [])
        missing_expected = [value for value in expected_values if value not in provided]
        if missing_expected:
            raise ValueError(f"Meal tags for '{category}' must include archetype values: {missing_expected}")

    for category in multi_value_categories:
        values = sanitized_tags.get(category) or []
        if 0 < len(values) < 2:
            warnings.append(f"Category '{category}' ideally includes 2+ tags; received {values}")

    meal["meal_tags"] = sanitized_tags
    return warnings


def build_product_candidate_payload(
    meal: dict[str, Any],
    classification_index: dict[str, list[dict[str, Any]]],
    catalog: dict[str, dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for ingredient in meal.get("ingredients", []):
        core_name = ingredient.get("core_item_name")
        candidates: list[dict[str, Any]] = []
        records = classification_index.get((core_name or "").lower(), [])
        for record in records:
            product = catalog.get(record.get("product_id"))
            if not product:
                continue
            candidates.append(
                {
                    "product_id": record.get("product_id"),
                    "name": product.get("name") or product.get("display_name"),
                    "brand": product.get("brand"),
                    "sale_price": product.get("salePrice"),
                    "detail_url": product.get("detailUrl"),
                    "default_category": product.get("defaultCategory"),
                    "package_hint": product.get("name"),
                }
            )
            if len(candidates) >= limit:
                break
        payload.append(
            {
                "core_item_name": core_name,
                "ingredient_quantity": ingredient.get("quantity"),
                "candidates": candidates,
            }
        )
    return payload


def build_product_system_prompt() -> str:
    return dedent(
        """
        You are a Woolworths product specialist. Given meal ingredients and SKU options, pick the best product and package count.
        Every ingredient must map to a SKU whenever candidates exist—missing links block the meal from shipping.
        Always emit valid JSON and do not rewrite cooking instructions.
        """
    ).strip()


def build_product_user_prompt(
    *,
    archetype: dict[str, Any],
    meal: dict[str, Any],
    sku_payload: list[dict[str, Any]],
) -> str:
    archetype_brief = json.dumps(
        {
            "uid": archetype.get("uid"),
            "name": archetype.get("name"),
            "core_tags": archetype.get("core_tags"),
        },
        ensure_ascii=False,
        indent=2,
    )
    meal_json = json.dumps(meal, ensure_ascii=False, indent=2)
    sku_json = json.dumps(sku_payload, ensure_ascii=False, indent=2)

    schema = {
        "product_matches": [
            {
                "core_item_name": "string",
                "selected_product_id": "string or null",
                "package_quantity": "float or int indicating number of retail packs",
                "package_notes": "brief rationale",
                "ingredient_line": "optional text using product name",
            }
        ]
    }
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)

    return dedent(
        f"""
        Task: For each ingredient, choose the best Woolworths SKU (from candidates) and suggest how many retail packs are needed.
        Use servings + archetype context to keep quantities realistic. If candidates are provided, you MUST choose one of them (even if it slightly overshoots quantity) and justify the pick—never return null when at least one candidate exists.
        Only when no candidates are supplied may you set `selected_product_id` to null, and those notes must begin with `MISSING_SKU:` so the pipeline can flag the meal for removal.
        Do NOT rewrite the instructions or mention retailer names in the method; only provide product matches.

        Archetype snapshot:
        ```json
        {archetype_brief}
        ```

        Meal draft:
        ```json
        {meal_json}
        ```

        SKU candidates per ingredient:
        ```json
        {sku_json}
        ```

        Output schema:
        ```json
        {schema_json}
        ```
        """
    ).strip()


def parse_product_response(text: str) -> dict[str, Any]:
    body = scrub_json_block(text)
    data = json.loads(body)
    if "product_matches" not in data:
        raise ValueError("Product selection response missing 'product_matches'")
    return data


def merge_product_matches(
    meal: dict[str, Any],
    product_response: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches = product_response.get("product_matches", [])
    final_matches: list[dict[str, Any]] = []
    final_ingredients: list[dict[str, Any]] = []

    match_by_name = { (entry.get("core_item_name") or "").lower(): entry for entry in matches }
    for ingredient in meal.get("ingredients", []):
        key = (ingredient.get("core_item_name") or "").lower()
        match = match_by_name.get(key)
        product_payload = None
        ingredient_line = None
        package_qty = None
        if match:
            product_id = match.get("selected_product_id")
            package_qty = match.get("package_quantity")
            ingredient_line = match.get("ingredient_line")
            product_payload = catalog.get(product_id) if product_id else None
        final_matches.append(match or {"core_item_name": ingredient.get("core_item_name")})
        final_ingredients.append(
            {
                "core_item_name": ingredient.get("core_item_name"),
                "quantity": ingredient.get("quantity"),
                "preparation": ingredient.get("preparation"),
                "selected_product": {
                    "product_id": match.get("selected_product_id"),
                    "package_quantity": package_qty,
                    "name": product_payload.get("name") if product_payload else None,
                    "detail_url": product_payload.get("detailUrl") if product_payload else None,
                    "sale_price": product_payload.get("salePrice") if product_payload else None,
                } if match else None,
                "ingredient_line": ingredient_line,
            }
        )

    return final_matches, final_ingredients


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_meals_for_archetype(
    *,
    archetype: dict[str, Any],
    args: argparse.Namespace,
    scope_slug: str,
    predefined_dir: Path,
    curated_path: Path,
    run_dir_root: Path,
    allowed_core_items: list[CoreItem] | None,
    curated_sets: dict[str, list[str]],
    core_item_index: dict[str, CoreItem],
    tag_catalog: dict[str, set[str]],
    tag_synonyms: dict[str, dict[str, Any]],
    tag_value_reference: dict[str, list[str]],
    required_categories: Sequence[str],
    multi_value_categories: set[str],
    tags_version: str,
    archetypes_summary_payload: list[dict[str, Any]],
    classification_index: dict[str, list[dict[str, Any]]],
    catalog: dict[str, dict[str, Any]],
    meals_dir: Path,
    archetype_json_path: Path,
    curated_source_path: Path,
) -> int:
    archetype_uid = archetype.get("uid")
    if not archetype_uid:
        raise ValueError("Archetype is missing a uid")

    if allowed_core_items is None:
        allowed_core_items, missing_curated = select_allowed_core_items(
            archetype_uid, curated_sets, core_item_index
        )
        if not allowed_core_items:
            raise RuntimeError(f"Curated ingredients for {archetype_uid} produced no usable items")
        if missing_curated:
            preview = ", ".join(missing_curated[:5])
            print(
                f"[warn] {len(missing_curated)} curated ingredient(s) missing from canonical catalog "
                f"for {archetype_uid} (showing up to 5): {preview}"
            )
        print(
            f"[info] Loaded {len(allowed_core_items)} curated ingredient(s) for {archetype_uid} "
            f"from {curated_source_path}"
        )

    existing_meals = load_existing_meals(meals_dir, scope_slug, archetype_uid)
    run_dir = ensure_run_dir(run_dir_root)
    write_json(
        run_dir / "metadata.json",
        {
            "predefined_scope": scope_slug,
            "predefined_dir": str(predefined_dir),
            "archetype_uid": archetype_uid,
            "meal_count": args.meal_count,
            "meal_model": args.meal_model,
            "product_model": args.product_model,
            "tags_version": tags_version,
            "curated_ingredients_path": str(curated_source_path),
            "archetype_source": str(archetype_json_path),
            "timestamp": timestamp_slug(),
        },
    )

    meals_created: list[dict[str, Any]] = []
    run_slug = run_dir.name.replace("run_", "")

    for meal_index in range(1, args.meal_count + 1):
        existing_summary = summarize_existing_meals(
            existing_meals, archetype_uid, args.existing_meal_summary_count
        )
        meal_prompt = build_meal_user_prompt(
            archetype=archetype,
            archetypes_summary=archetypes_summary_payload,
            existing_meals_summary=existing_summary,
            allowed_core_items=allowed_core_items,
            tags_version=tags_version,
            tag_value_reference=tag_value_reference,
            required_categories=required_categories,
            multi_value_categories=multi_value_categories,
            meals_requested=1,
        )
        print(f"[meal] Generating meal {meal_index}/{args.meal_count} for {archetype.get('name')}")
        try:
            meal_response_text = call_openai_api(
                system_prompt=build_meal_system_prompt(),
                user_prompt=meal_prompt,
                model=args.meal_model,
                temperature=args.meal_temperature,
                top_p=args.meal_top_p,
                max_tokens=args.meal_max_output_tokens,
                reasoning_effort=args.meal_reasoning_effort,
                max_output_tokens=args.meal_max_output_tokens,
            )
        except OpenAIClientError as exc:
            raise RuntimeError(f"Meal generation API call failed: {exc}") from exc

        run_meal_payload_path = run_dir / f"meal_{meal_index:02d}_generator.json"
        write_json(run_meal_payload_path, {"prompt": meal_prompt, "response": meal_response_text})

        meal_payload = parse_meal_response(meal_response_text)
        meal_warnings = validate_meal_payload(
            meal_payload,
            required_categories=required_categories,
            tag_catalog=tag_catalog,
            core_item_index=core_item_index,
            tag_synonyms=tag_synonyms,
            archetype_tags=archetype.get("core_tags") or {},
            multi_value_categories=multi_value_categories,
        )
        if meal_warnings:
            for note in meal_warnings:
                print(f"[warn] {note}")

        meal_id = f"meal_{run_slug}_{meal_index:02d}_{uuid.uuid4().hex[:6]}"
        created_at = timestamp_slug()
        base_record = {
            "meal_id": meal_id,
            "archetype_uid": archetype_uid,
            "archetype_name": archetype.get("name"),
            "name": meal_payload.get("name"),
            "description": meal_payload.get("description"),
            "servings": meal_payload.get("servings"),
            "prep_steps": meal_payload.get("prep_steps", []),
            "cook_steps": meal_payload.get("cook_steps", []),
            "instructions": [*meal_payload.get("prep_steps", []), *meal_payload.get("cook_steps", [])],
            "ingredients": meal_payload.get("ingredients"),
            "meal_tags": meal_payload.get("meal_tags"),
            "product_matches": [],
            "final_ingredients": [
                {
                    "core_item_name": ingredient.get("core_item_name"),
                    "quantity": ingredient.get("quantity"),
                    "preparation": ingredient.get("preparation"),
                    "selected_product": None,
                    "ingredient_line": None,
                }
                for ingredient in meal_payload.get("ingredients", [])
            ],
            "warnings": meal_warnings,
            "metadata": {
                "created_at": created_at,
                "meal_model": args.meal_model,
                "product_model": args.product_model,
                "tags_version": tags_version,
                "run_dir": str(run_dir),
                "predefined_scope": scope_slug,
                "product_selection_status": "pending",
            },
        }
        record_path = save_meal_record(meals_dir, scope_slug, archetype_uid, base_record)

        sku_payload = build_product_candidate_payload(
            meal_payload, classification_index, catalog, args.product_candidate_limit
        )
        sku_prompt = build_product_user_prompt(archetype=archetype, meal=meal_payload, sku_payload=sku_payload)
        print(f"[products] Selecting SKUs for meal {meal_index:02d}")
        try:
            product_response_text = call_openai_api(
                system_prompt=build_product_system_prompt(),
                user_prompt=sku_prompt,
                model=args.product_model,
                temperature=args.product_temperature,
                top_p=args.product_top_p,
                max_tokens=args.product_max_output_tokens,
                reasoning_effort=args.product_reasoning_effort,
                max_output_tokens=args.product_max_output_tokens,
            )
        except OpenAIClientError as exc:
            base_record["metadata"]["product_selection_status"] = "failed"
            base_record["metadata"]["product_selection_error"] = str(exc)
            save_meal_record(meals_dir, scope_slug, archetype_uid, base_record)
            raise RuntimeError(f"Product selection API call failed: {exc}") from exc

        write_json(
            run_dir / f"meal_{meal_index:02d}_selector.json",
            {"prompt": sku_prompt, "response": product_response_text},
        )

        product_payload = parse_product_response(product_response_text)
        matches, final_ingredients = merge_product_matches(meal_payload, product_payload, catalog)

        base_record["product_matches"] = matches
        base_record["final_ingredients"] = final_ingredients
        missing_product_links = sorted(
            ingredient.get("core_item_name")
            for ingredient in final_ingredients
            if not (
                ingredient.get("selected_product")
                and ingredient["selected_product"].get("product_id")
            )
        )
        if missing_product_links:
            note = (
                "Missing product matches for: "
                + ", ".join(item for item in missing_product_links if item)
            )
            print(f"[warn] {note}")
            base_record.setdefault("warnings", []).append(note)
            base_record["metadata"]["product_selection_status"] = "incomplete_missing_products"
            base_record["metadata"]["missing_product_links"] = missing_product_links
        else:
            base_record["metadata"]["product_selection_status"] = "completed"
            base_record["metadata"].pop("missing_product_links", None)
        base_record["metadata"].pop("product_selection_error", None)
        record_path = save_meal_record(meals_dir, scope_slug, archetype_uid, base_record)
        existing_meals.append(base_record)
        meals_created.append({"meal_id": meal_id, "path": str(record_path)})

    print(f"[info] Created {len(meals_created)} meal(s) for archetype {archetype_uid}")
    return len(meals_created)


def run(args: argparse.Namespace) -> None:
    predefined_dir = Path(args.predefined_dir)
    if not predefined_dir.exists():
        raise FileNotFoundError(f"Predefined archetype directory not found: {predefined_dir}")
    scope_slug = predefined_dir.name
    archetype_json_path = Path(args.archetype_json) if args.archetype_json else predefined_dir / ARH_COMBINED_FILENAME
    curated_path = (
        Path(args.curated_ingredients)
        if args.curated_ingredients
        else predefined_dir / INGREDIENT_CURATION_SUBDIR / CURATED_INGREDIENTS_FILENAME
    )

    archetypes = load_archetypes(archetype_json_path)
    archetype_lookup = {entry.get("uid"): entry for entry in archetypes if entry.get("uid")}
    tags_manifest = load_tags_manifest(Path(args.tags_manifest))
    tag_catalog = build_tag_catalog(tags_manifest)
    required_categories = determine_required_categories(tag_catalog)
    tag_value_reference = build_tag_value_reference(tag_catalog, required_categories)
    tag_synonyms = load_tag_synonyms(Path(args.tag_synonyms))
    tags_version = tags_manifest.get("tags_version")
    multi_value_categories = {category for category in required_categories if category in MULTI_VALUE_TAG_CATEGORIES}

    _, core_item_index = load_core_items(Path(args.core_items))
    curated_sets = load_curated_ingredient_sets(curated_path)
    classification_index = load_classifications(Path(args.ingredient_classifications))
    catalog = load_catalog(Path(args.catalog))
    meals_dir = Path(args.meals_dir)
    run_dir_root = Path(args.output_dir) / "runs" / scope_slug
    archetypes_summary_payload = archetype_summary(archetypes)

    target_uid_raw = (args.archetype_uid or "").strip()
    if not target_uid_raw:
        raise ValueError("--archetype-uid is required (use 'all' to process every archetype in the scope)")
    target_all = target_uid_raw.lower() == "all"
    if target_all:
        target_uids = sorted(archetype_lookup.keys())
        if not target_uids:
            raise RuntimeError(f"No archetypes discovered for scope {scope_slug}")
        print(f"[info] Processing all {len(target_uids)} archetypes for scope {scope_slug}")
    else:
        target_uids = [target_uid_raw]

    precomputed_allowed: list[CoreItem] | None = None
    if not target_all:
        precomputed_allowed, missing_curated = select_allowed_core_items(
            target_uid_raw, curated_sets, core_item_index
        )
        if not precomputed_allowed:
            raise RuntimeError(f"Curated ingredients for {target_uid_raw} produced no usable items")
        if missing_curated:
            preview = ", ".join(missing_curated[:5])
            print(
                f"[warn] {len(missing_curated)} curated ingredient(s) missing from canonical catalog "
                f"(showing up to 5): {preview}"
            )
        print(
            f"[info] Loaded {len(precomputed_allowed)} curated ingredient(s) for {target_uid_raw} "
            f"from {curated_path}"
        )

    total_created = 0
    failures: list[str] = []

    for index, archetype_uid in enumerate(target_uids, start=1):
        archetype = archetype_lookup.get(archetype_uid)
        if not archetype:
            msg = f"[warn] Archetype uid '{archetype_uid}' not found in {archetype_json_path}; skipping"
            print(msg)
            failures.append(msg)
            continue
        print(f"[info] ({index}/{len(target_uids)}) Starting generation for {archetype.get('name')} ({archetype_uid})")
        try:
            meals_created = generate_meals_for_archetype(
                archetype=archetype,
                args=args,
                scope_slug=scope_slug,
                predefined_dir=predefined_dir,
                curated_path=curated_path,
                run_dir_root=run_dir_root,
                allowed_core_items=precomputed_allowed if not target_all else None,
                curated_sets=curated_sets,
                core_item_index=core_item_index,
                tag_catalog=tag_catalog,
                tag_synonyms=tag_synonyms,
                tag_value_reference=tag_value_reference,
                required_categories=required_categories,
                multi_value_categories=multi_value_categories,
                tags_version=tags_version,
                archetypes_summary_payload=archetypes_summary_payload,
                classification_index=classification_index,
                catalog=catalog,
                meals_dir=meals_dir,
                archetype_json_path=archetype_json_path,
                curated_source_path=curated_path,
            )
            total_created += meals_created
        except Exception as exc:  # noqa: BLE001
            msg = f"[error] Failed to generate meals for {archetype_uid}: {exc}"
            print(msg)
            failures.append(msg)

    print(f"[done] Created {total_created} meal(s) across {len(target_uids)} archetype(s).")
    if failures:
        raise RuntimeError(
            f"Completed with {len(failures)} failure(s). First error: {failures[0]} (see logs for details)."
        )


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    run(args)


if __name__ == "__main__":  # pragma: no cover
    main()
