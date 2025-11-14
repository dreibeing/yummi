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
from typing import Any, Dict, Iterable, Sequence

from llm_utils import OpenAIClientError, call_openai_api


DEFAULT_ARCHETYPE_PATH = Path("data/archetypes/run_20251112T091259Z/curation/archetypes_curated.json")
DEFAULT_TAGS_MANIFEST = Path("data/tags/defined_tags.json")
DEFAULT_TAG_SYNONYMS = Path("data/tags/tag_synonyms.json")
DEFAULT_CORE_ITEMS = Path("data/ingredients/unique_core_items.csv")
DEFAULT_CLASSIFICATIONS = Path("data/ingredients/ingredient_classifications.csv")
DEFAULT_CATALOG = Path("resolver/catalog.json")
DEFAULT_MEALS_DIR = Path("data/meals")
DEFAULT_OUTPUT_DIR = Path("data/meals")

DEFAULT_MEAL_MODEL = "gpt-5"
DEFAULT_PRODUCT_MODEL = "gpt-5"

ALWAYS_REQUIRED_MEAL_CATEGORIES = {
    "Diet",
    "Cuisine",
    "PrepTime",
    "Complexity",
    "HeatSpice",
    "BudgetLevel",
}

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
    parser.add_argument("--archetype-uid", required=True, help="UID of the archetype to generate meals for")
    parser.add_argument("--meal-count", type=int, default=1, help="Number of meals to create in this run")
    parser.add_argument("--archetype-json", type=Path, default=DEFAULT_ARCHETYPE_PATH, help="Path to curated archetype JSON")
    parser.add_argument("--tags-manifest", type=Path, default=DEFAULT_TAGS_MANIFEST, help="Path to defined_tags manifest")
    parser.add_argument("--core-items", type=Path, default=DEFAULT_CORE_ITEMS, help="CSV of canonical core items")
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


def load_existing_meals(base_dir: Path, archetype_uid: str) -> list[dict[str, Any]]:
    archetype_dir = base_dir / archetype_uid
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


def save_meal_record(base_dir: Path, archetype_uid: str, meal_record: dict[str, Any]) -> Path:
    archetype_dir = base_dir / archetype_uid
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
            "Diet": ",".join(tags.get("Diet", [])[:1]),
            "Cuisine": ",".join(tags.get("Cuisine", [])[:2]),
            "PrepTime": ",".join(tags.get("PrepTime", [])[:1]),
            "Complexity": ",".join(tags.get("Complexity", [])[:1]),
        }
        short_ingredients = ", ".join([ing.get("core_item_name") for ing in meal.get("ingredients", [])][:4])
        summary.append(
            {
                "meal_id": meal.get("meal_id"),
                "name": meal.get("name"),
                "highlight": f"Diet={highlight_tags['Diet'] or '-'}; Cuisine={highlight_tags['Cuisine'] or '-'}; Prep={highlight_tags['PrepTime'] or '-'}; Complexity={highlight_tags['Complexity'] or '-'}",
                "key_ingredients": short_ingredients,
            }
        )
    return summary


def determine_required_categories(
    manifest_categories: Iterable[str], archetype: dict[str, Any]
) -> list[str]:
    archetype_tags = (archetype.get("core_tags") or {})
    effective: list[str] = []
    for category in manifest_categories:
        if category in ALWAYS_REQUIRED_MEAL_CATEGORIES:
            effective.append(category)
            continue
        values = archetype_tags.get(category)
        if values:
            effective.append(category)
    return effective


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
    tags_manifest: dict[str, Any],
    required_categories: Sequence[str],
    meals_requested: int,
) -> str:
    tags_version = tags_manifest.get("tags_version")
    archetype_json = json.dumps(archetype, ensure_ascii=False, indent=2)
    summary_json = json.dumps(archetypes_summary, ensure_ascii=False, indent=2)
    existing_json = json.dumps(existing_meals_summary, ensure_ascii=False, indent=2)
    allowed_json = json.dumps([
        {"core_item_name": item.name, "item_type": item.item_type} for item in allowed_core_items
    ], ensure_ascii=False)

    schema = {
        "meal": {
            "name": "string",
            "description": "1–2 sentences",
            "servings": "string (e.g., 'Serves 2 adults')",
            "prep_steps": ["ordered strings describing ingredient prep, chopping, preheating"],
            "cook_steps": ["ordered strings describing actual cooking/assembly"],
            "ingredients": [
                {
                    "core_item_name": "must match canonical ingredient exactly",
                    "quantity": "human-readable quantity",
                    "preparation": "optional prep note"
                }
            ],
            "meal_tags": {
                "Diet": ["values"],
                "Cuisine": ["values"],
                "PrepTime": ["values"],
                "Complexity": ["values"],
                "HeatSpice": ["values"],
                "EthicsReligious": ["values"],
                "Allergens": ["values"],
                "BudgetLevel": ["values"],
                "Audience": ["optional values"],
                "CuisineOpenness": ["optional values"],
                "NutritionFocus": ["optional"],
                "Equipment": ["optional"]
            }
        }
    }

    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)
    instructions = dedent(
        f"""
        Task: Generate {meals_requested} new meal(s) for the target archetype. Every meal must:
        - Use only ingredients listed under "Allowed core items".
        - Fit the archetype's diet, allergens, heat preference, complexity, prep time, and household size.
        - Provide clear servings guidance (e.g., 'Serves 4', 'Single portion').
        - Keep instructions referencing ingredient/common terms only (no retailer product names or package sizes).
        - Split the method into two arrays: `prep_steps` (mise en place) and `cook_steps` (actual cooking), with concise numbered actions.
        - Include a complete tag set covering required categories: {', '.join(required_categories)} using tags_version {tags_version}.
        - Avoid duplicating meal names or concepts from "Existing meals" (if any).
        - Keep ingredient counts manageable (8–12 items) and quantities realistic for the servings size.
        - Return STRICT JSON matching the provided schema, with double quotes around keys and strings.

        Target archetype:
        ```json
        {archetype_json}
        ```

        Snapshot of all archetypes (for awareness of broader coverage, do NOT reference others in the output):
        ```json
        {summary_json}
        ```

        Existing meals for this archetype (avoid repeats):
        ```json
        {existing_json}
        ```

        Allowed core items:
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
    required_categories: Iterable[str],
    tag_catalog: dict[str, set[str]],
    core_item_index: dict[str, CoreItem],
    tag_synonyms: dict[str, dict[str, Any]],
    archetype_tags: dict[str, Any],
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
        if not allowed_values and category in ALWAYS_REQUIRED_MEAL_CATEGORIES:
            raise ValueError(f"Required category '{category}' missing from tag manifest")

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
        fallback_values = archetype_tags.get(category) if isinstance(archetype_tags, dict) else None
        if fallback_values:
            values = fallback_values if isinstance(fallback_values, list) else [fallback_values]
            sanitized_tags[category] = values
            warnings.append(f"Filled missing category '{category}' from archetype defaults")
        else:
            remaining_missing.append(category)
    if remaining_missing:
        raise ValueError(f"Meal tags missing required categories: {remaining_missing}")

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
        Use servings + archetype context to keep quantities realistic. If no candidate fits, set selected_product_id to null and explain in package_notes.
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


def run(args: argparse.Namespace) -> None:
    archetypes = load_archetypes(Path(args.archetype_json))
    archetype = find_archetype(args.archetype_uid, archetypes)
    tags_manifest = load_tags_manifest(Path(args.tags_manifest))
    tag_catalog = build_tag_catalog(tags_manifest)
    tag_synonyms = load_tag_synonyms(Path(args.tag_synonyms))
    manifest_required_categories = tags_manifest.get("required_categories", {}).get("meal", [])
    required_categories = determine_required_categories(manifest_required_categories, archetype)
    tags_version = tags_manifest.get("tags_version")
    core_items, core_item_index = load_core_items(Path(args.core_items))
    classification_index = load_classifications(Path(args.ingredient_classifications))
    catalog = load_catalog(Path(args.catalog))
    meals_dir = Path(args.meals_dir)
    existing_meals = load_existing_meals(meals_dir, args.archetype_uid)

    run_dir = ensure_run_dir(Path(args.output_dir) / "runs")
    write_json(
        run_dir / "metadata.json",
        {
            "archetype_uid": args.archetype_uid,
            "meal_count": args.meal_count,
            "meal_model": args.meal_model,
            "product_model": args.product_model,
            "tags_version": tags_version,
            "timestamp": timestamp_slug(),
        },
    )

    archetypes_summary_payload = archetype_summary(archetypes)
    run_slug = run_dir.name.replace("run_", "")
    meals_created: list[dict[str, Any]] = []

    for meal_index in range(1, args.meal_count + 1):
        existing_summary = summarize_existing_meals(existing_meals, args.archetype_uid, args.existing_meal_summary_count)
        meal_prompt = build_meal_user_prompt(
            archetype=archetype,
            archetypes_summary=archetypes_summary_payload,
            existing_meals_summary=existing_summary,
            allowed_core_items=core_items,
            tags_manifest=tags_manifest,
            required_categories=required_categories,
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
        )
        if meal_warnings:
            for note in meal_warnings:
                print(f"[warn] {note}")

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
            raise RuntimeError(f"Product selection API call failed: {exc}") from exc

        write_json(
            run_dir / f"meal_{meal_index:02d}_selector.json",
            {"prompt": sku_prompt, "response": product_response_text},
        )

        product_payload = parse_product_response(product_response_text)
        matches, final_ingredients = merge_product_matches(meal_payload, product_payload, catalog)

        meal_id = f"meal_{run_slug}_{meal_index:02d}_{uuid.uuid4().hex[:6]}"
        created_at = timestamp_slug()
        prep_steps = meal_payload.get("prep_steps", [])
        cook_steps = meal_payload.get("cook_steps", [])
        combined_instructions = [*prep_steps, *cook_steps]
        final_record = {
            "meal_id": meal_id,
            "archetype_uid": archetype.get("uid"),
            "archetype_name": archetype.get("name"),
            "name": meal_payload.get("name"),
            "description": meal_payload.get("description"),
            "servings": meal_payload.get("servings"),
            "prep_steps": prep_steps,
            "cook_steps": cook_steps,
            "instructions": combined_instructions,
            "ingredients": meal_payload.get("ingredients"),
            "meal_tags": meal_payload.get("meal_tags"),
            "product_matches": matches,
            "final_ingredients": final_ingredients,
            "warnings": meal_warnings,
            "metadata": {
                "created_at": created_at,
                "meal_model": args.meal_model,
                "product_model": args.product_model,
                "tags_version": tags_version,
                "run_dir": str(run_dir),
            },
        }
        record_path = save_meal_record(meals_dir, args.archetype_uid, final_record)
        existing_meals.append(final_record)
        meals_created.append({"meal_id": meal_id, "path": str(record_path)})

    print(f"Created {len(meals_created)} meal(s). Latest file(s):")
    for entry in meals_created:
        print(f"  - {entry['meal_id']} -> {entry['path']}")


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    run(args)


if __name__ == "__main__":  # pragma: no cover
    main()
