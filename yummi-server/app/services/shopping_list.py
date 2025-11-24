from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence

from fastapi import HTTPException, status

from ..config import get_settings
from ..schemas import (
    ShoppingListBuildRequest,
    ShoppingListBuildResponse,
    ShoppingListMealPayload,
    ShoppingListProductSelection,
    ShoppingListResultItem,
)
from .openai_responses import call_openai_responses

logger = logging.getLogger(__name__)

_catalog_lock = threading.Lock()
_catalog_by_product_id: Dict[str, Dict[str, Any]] | None = None
_catalog_by_catalog_ref: Dict[str, Dict[str, Any]] | None = None
_catalog_cache_mtime: float = 0.0
_catalog_cache_path: str | None = None

UNIT_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "g": {"unit_type": "weight", "unit_label": "g", "multiplier": 1},
    "gram": {"unit_type": "weight", "unit_label": "g", "multiplier": 1},
    "grams": {"unit_type": "weight", "unit_label": "g", "multiplier": 1},
    "kg": {"unit_type": "weight", "unit_label": "g", "multiplier": 1000},
    "kilogram": {"unit_type": "weight", "unit_label": "g", "multiplier": 1000},
    "kilograms": {"unit_type": "weight", "unit_label": "g", "multiplier": 1000},
    "mg": {"unit_type": "weight", "unit_label": "g", "multiplier": 0.001},
    "milligram": {"unit_type": "weight", "unit_label": "g", "multiplier": 0.001},
    "milligrams": {"unit_type": "weight", "unit_label": "g", "multiplier": 0.001},
    "l": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1000},
    "liter": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1000},
    "liters": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1000},
    "litre": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1000},
    "litres": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1000},
    "ml": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1},
    "milliliter": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1},
    "milliliters": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1},
    "millilitre": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1},
    "millilitres": {"unit_type": "volume", "unit_label": "ml", "multiplier": 1},
}

MULTIPLIER_PATTERN = re.compile(
    r"(\d+(?:[\.,]\d+)?(?:\s+\d+/\d+)?)\s*[x×]\s*(\d+(?:[\.,]\d+)?(?:\s+\d+/\d+)?)(?:\s*(kg|g|grams?|kilograms?|mg|milligrams?|l|ml|litres?|liters?|millilitres?|milliliters?))?",
    re.IGNORECASE,
)
UNIT_PATTERN = re.compile(
    r"(\d+(?:[\.,]\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*(kg|g|grams?|kilograms?|mg|milligrams?|l|ml|litres?|liters?|millilitres?|milliliters?)",
    re.IGNORECASE,
)
ATTACHED_UNIT_PATTERN = re.compile(r"(\d+(?:[\.,]\d+)?)(kg|g|mg|l|ml)", re.IGNORECASE)
COUNT_PATTERN = re.compile(
    r"(\d+(?:[\.,]\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s*(packets?|packs?|pk|pieces?|pcs|bunch(?:es)?|loaves?|bottles?|jars?|tins?|cans?|sticks?|wraps?|buns?)",
    re.IGNORECASE,
)
DEFAULT_NUMBER_PATTERN = re.compile(r"(\d+(?:[\.,]\d+)?(?:\s+\d+/\d+)?|\d+/\d+)")
CODE_FENCE_PATTERN = re.compile(r"```(?:json)?(.*?)```", re.IGNORECASE | re.DOTALL)

WATER_LABELS = {
    "water",
    "warm water",
    "cold water",
    "ice water",
    "hot water",
    "boiling water",
    "tap water",
    "filtered water",
    "room temperature water",
}


async def run_shopping_list_workflow(
    *,
    user_id: str,
    request: ShoppingListBuildRequest,
) -> ShoppingListBuildResponse:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OpenAI not configured",
        )
    meals = request.meals or []
    if not meals:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one meal selection is required",
        )
    ingredient_groups = _aggregate_ingredient_groups(meals)
    if not ingredient_groups:
        return ShoppingListBuildResponse(
            status="completed",
            generatedAt=datetime.now(timezone.utc),
            items=[],
        )
    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(meals, ingredient_groups)
    llm_text = call_openai_responses(
        model=settings.openai_shopping_list_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_output_tokens=settings.openai_shopping_list_max_output_tokens,
        top_p=settings.openai_shopping_list_top_p,
        reasoning_effort=settings.openai_shopping_list_reasoning_effort,
    )
    llm_payload = _parse_model_response(llm_text)
    items = _normalize_result_items(ingredient_groups, llm_payload)
    return ShoppingListBuildResponse(
        status="completed",
        generatedAt=datetime.now(timezone.utc),
        items=items,
    )


def _aggregate_ingredient_groups(
    meals: Sequence[ShoppingListMealPayload],
) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}
    for meal in meals:
        try:
            ingredients = meal.ingredients or []
        except AttributeError:
            ingredients = []
        for index, ingredient_model in enumerate(ingredients):
            ingredient = (
                ingredient_model.model_dump(mode="python")
                if hasattr(ingredient_model, "model_dump")
                else dict(ingredient_model or {})
            )
            fallback = _build_display_text(ingredient, index)
            labels = _collect_labels(ingredient, fallback)
            if _is_water_ingredient(labels):
                continue
            group_key = _derive_group_key(ingredient, fallback) or f"{meal.meal_id or 'meal'}-{index}"
            entry_id = ingredient.get("id") or f"{meal.meal_id or 'meal'}-{index}"
            requirement_measurement = _parse_measurement_value(ingredient.get("quantity") or ingredient.get("text"))
            product_meta = _normalize_product_meta(ingredient)
            package_measurement = None
            if product_meta:
                package_measurement = _parse_measurement_value(
                    product_meta.get("ingredient_line")
                    or product_meta.get("name")
                    or fallback
                )
            required_quantity = _parse_numeric_quantity(
                ingredient.get("package_quantity")
            ) or _parse_numeric_quantity(ingredient.get("quantity")) or 1.0
            group = groups.setdefault(
                group_key,
                {
                    "group_key": group_key,
                    "label": ingredient.get("core_item_name")
                    or ingredient.get("name")
                    or ingredient.get("product_name")
                    or ingredient.get("productName")
                    or fallback,
                    "entries": [],
                },
            )
            group["entries"].append(
                {
                    "entry_id": str(entry_id),
                    "meal_id": meal.meal_id,
                    "meal_name": meal.name,
                    "display_text": fallback,
                    "labels": labels,
                    "quantity_text": ingredient.get("quantity") or ingredient.get("text"),
                    "required_quantity": required_quantity,
                    "requirement_measurement": requirement_measurement,
                    "package_measurement": package_measurement,
                    "product": product_meta,
                    "preparation": ingredient.get("preparation"),
                }
            )
    aggregated: List[Dict[str, Any]] = []
    for group in groups.values():
        entries = group.get("entries", [])
        if not entries:
            continue
        summary = _summarize_requirement(entries)
        group["requirement_summary"] = summary
        group["fallback_packages"] = summary.get("fallback_packages", 1)
        group["linked_products"] = _build_linked_products(entries)
        group["label"] = group.get("label") or entries[0].get("display_text") or group.get("group_key")
        aggregated.append(group)
    aggregated.sort(key=lambda item: (item.get("label") or item.get("group_key") or "").lower())
    return aggregated


def _build_linked_products(entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    products: List[Dict[str, Any]] = []
    for entry in entries:
        product = entry.get("product") or {}
        if not product:
            continue
        key = product.get("id") or product.get("name") or product.get("ingredient_line")
        if not key or key in seen:
            continue
        seen.add(key)
        measurement = _parse_measurement_value(product.get("ingredient_line") or product.get("name"))
        products.append(
            {
                "productId": product.get("id"),
                "catalogRefId": product.get("catalog_ref_id") or product.get("catalogRefId"),
                "name": product.get("name"),
                "detailUrl": product.get("detail_url"),
                "salePrice": product.get("sale_price"),
                "packageQuantity": product.get("package_quantity"),
                "ingredientLine": product.get("ingredient_line"),
                "packageMeasurement": measurement,
            }
        )
    return products


def _summarize_requirement(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    unit_type = None
    total_amount = 0.0
    fallback_packages = 0.0
    for entry in entries:
        qty = entry.get("required_quantity")
        if isinstance(qty, (int, float)) and math.isfinite(qty):
            fallback_packages += qty
        else:
            fallback_packages += 1
        measurement = entry.get("requirement_measurement")
        if measurement and measurement.get("base_amount"):
            if not unit_type:
                unit_type = measurement.get("unit_type")
                total_amount = measurement.get("base_amount") or 0.0
            elif unit_type == measurement.get("unit_type"):
                total_amount += measurement.get("base_amount") or 0.0
            else:
                unit_type = None
                total_amount = 0.0
    if fallback_packages <= 0:
        fallback_packages = 1
    return {
        "unit_type": unit_type,
        "amount": total_amount if unit_type else None,
        "fallback_packages": max(1, math.ceil(fallback_packages)),
    }


def _build_system_prompt() -> str:
    return (
        "You are Yummi's grocery planning assistant. Combine meal ingredients into a shopping list "
        "using Woolworths retail packs. Always return valid JSON, respect the provided quantities, "
        "and classify each ingredient as either 'pickup' (buy now) or 'pantry' (likely already on hand)."
    )


def _build_user_prompt(
    meals: Sequence[ShoppingListMealPayload],
    ingredient_groups: Sequence[Dict[str, Any]],
) -> str:
    meal_context = [
        {
            "meal_id": meal.meal_id,
            "name": meal.name,
            "servings": meal.servings,
            "ingredient_count": len(getattr(meal, "ingredients", []) or []),
        }
        for meal in meals
    ]
    group_context: List[Dict[str, Any]] = []
    for group in ingredient_groups:
        entry_examples = []
        for entry in group.get("entries", [])[:5]:
            entry_examples.append(
                {
                    "meal_id": entry.get("meal_id"),
                    "meal_name": entry.get("meal_name"),
                    "quantity": entry.get("quantity_text"),
                    "display": entry.get("display_text"),
                    "product": (entry.get("product") or {}).get("name"),
                }
            )
        group_context.append(
            {
                "group_key": group.get("group_key"),
                "label": group.get("label"),
                "total_entries": len(group.get("entries", [])),
                "requirement_summary": group.get("requirement_summary"),
                "fallback_packages": group.get("fallback_packages"),
                "linked_products": group.get("linked_products"),
                "entry_examples": entry_examples,
            }
        )
    schema = {
        "items": [
            {
                "group_key": "string identifier from ingredient_groups[*].group_key",
                "display_name": "string label for the ingredient line",
                "classification": "pickup | pantry",
                "packages_needed": "non-negative integer describing how many retail packs to buy",
                "notes": "optional short rationale",
                "product_selections": [
                    {
                        "product_id": "sku drawn from linked_products[].productId (or null if unavailable)",
                        "name": "product label",
                        "packages": "float or int count of packs to buy",
                    }
                ],
            }
        ]
    }
    context_json = json.dumps(
        {
            "meals": meal_context,
            "ingredient_groups": group_context,
        },
        ensure_ascii=False,
        indent=2,
    )
    schema_json = json.dumps(schema, ensure_ascii=False, indent=2)
    instructions = (
        "For every ingredient group you must return exactly one line in the output. "
        "Classify staples or tiny amounts as 'pantry' (default quantity zero) and everything else as 'pickup'. "
        "Use the linked Woolworths products whenever available and round packages to practical retail counts."
    )
    return (
        f"{instructions}\nContext JSON:\n```json\n{context_json}\n```\n"
        f"Output schema:\n```json\n{schema_json}\n```"
    )


def _parse_model_response(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    match = CODE_FENCE_PATTERN.search(stripped)
    if match:
        stripped = match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.error("Unable to parse shopping list model output: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Shopping list model returned invalid JSON",
        ) from exc


def _normalize_result_items(
    ingredient_groups: Sequence[Dict[str, Any]],
    llm_payload: Dict[str, Any],
) -> List[ShoppingListResultItem]:
    llm_lookup: Dict[str, Dict[str, Any]] = {}
    for entry in llm_payload.get("items") or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("group_key") or entry.get("key") or entry.get("id")
        if not key:
            continue
        llm_lookup[str(key)] = entry
    items: List[ShoppingListResultItem] = []
    for group in ingredient_groups:
        group_key = str(group.get("group_key"))
        llm_entry = llm_lookup.get(group_key)
        items.append(_build_result_item(group, llm_entry))
    return items


def _build_result_item(group: Dict[str, Any], llm_entry: Dict[str, Any] | None) -> ShoppingListResultItem:
    fallback_packages = float(group.get("fallback_packages") or 1)
    classification = "pickup"
    display_name = group.get("label") or group.get("group_key") or "Ingredient"
    notes = None
    packages = fallback_packages
    products = group.get("linked_products") or []
    if llm_entry:
        classification = _coerce_classification(llm_entry.get("classification")) or classification
        display_name = llm_entry.get("display_name") or display_name
        notes = llm_entry.get("notes")
        packages = _coerce_packages(llm_entry.get("packages_needed"), fallback_packages, classification)
        model_products = _coerce_product_selections(llm_entry.get("product_selections"))
        if model_products:
            products = model_products
    default_quantity = 0.0 if classification == "pantry" else packages
    unit_price = _resolve_unit_price(products, group.get("entries") or [])
    unit_price_minor = _to_minor_units(unit_price)
    selection_models = [
        ShoppingListProductSelection(
            productId=product.get("productId") or product.get("product_id"),
            catalogRefId=product.get("catalogRefId") or product.get("catalog_ref_id"),
            name=product.get("name"),
            detailUrl=product.get("detailUrl") or product.get("detail_url"),
            salePrice=product.get("salePrice"),
            packages=product.get("packages"),
        )
        for product in products
    ]
    return ShoppingListResultItem(
        id=group.get("group_key") or display_name,
        groupKey=group.get("group_key") or display_name,
        text=str(display_name),
        classification=classification,
        requiredQuantity=float(packages),
        defaultQuantity=float(default_quantity),
        notes=notes,
        linkedProducts=selection_models,
        unitPrice=unit_price,
        unitPriceMinor=unit_price_minor,
    )


def _coerce_classification(value: Any) -> str | None:
    if not value:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"pickup", "pantry"}:
        return lowered
    return None


def _coerce_packages(value: Any, fallback: float, classification: str) -> float:
    if isinstance(value, (int, float)) and math.isfinite(value):
        safe_value = max(0.0, float(value))
    else:
        safe_value = fallback
    if classification == "pickup" and safe_value < 1:
        return max(1.0, fallback)
    if classification == "pantry":
        return max(0.0, safe_value)
    return safe_value or fallback


def _coerce_product_selections(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    selections: List[Dict[str, Any]] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        selections.append(
            {
                "productId": entry.get("product_id") or entry.get("productId"),
                "name": entry.get("name"),
                "detailUrl": entry.get("detail_url") or entry.get("detailUrl"),
                "salePrice": entry.get("sale_price") or entry.get("salePrice"),
                "packages": entry.get("packages"),
            }
        )
    return selections


def _build_display_text(ingredient: Dict[str, Any], index: int) -> str:
    product_name = _coalesce(
        ingredient.get("product_name"),
        ingredient.get("productName"),
        ingredient.get("ingredient_line"),
    )
    if product_name:
        return str(product_name)
    parts: List[str] = []
    for key in ("quantity", "unit", "name", "core_item_name"):
        value = ingredient.get(key)
        if value:
            parts.append(str(value))
    preparation = ingredient.get("preparation")
    if preparation:
        parts.append(f"({preparation})")
    if parts:
        return " ".join(part.strip() for part in parts if str(part).strip())
    return f"Ingredient {index + 1}"


def _collect_labels(ingredient: Dict[str, Any], fallback: str | None) -> List[str]:
    labels: List[str] = []
    for key in (
        "core_item_name",
        "coreItemName",
        "name",
        "ingredient_line",
        "ingredientLine",
        "product_name",
        "productName",
    ):
        value = ingredient.get(key)
        if isinstance(value, str) and value.strip():
            labels.append(value)
    selected = ingredient.get("selected_product") or ingredient.get("selectedProduct")
    if selected and isinstance(selected, dict):
        name = selected.get("name")
        if isinstance(name, str) and name.strip():
            labels.append(name)
    if fallback:
        labels.append(fallback)
    return labels


def _is_water_ingredient(labels: Sequence[str]) -> bool:
    for label in labels or []:
        normalized = _normalize_label(label)
        if normalized and normalized in WATER_LABELS:
            return True
    return False


def _derive_group_key(ingredient: Dict[str, Any], fallback: str | None) -> str | None:
    for candidate in (
        ingredient.get("core_item_name"),
        ingredient.get("coreItemName"),
        ingredient.get("name"),
        ingredient.get("product_name"),
        ingredient.get("productName"),
        fallback,
    ):
        normalized = _normalize_label(candidate)
        if normalized:
            return normalized
    return None


def _normalize_label(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(
        r"\b(\d+|ml|l|g|kg|cups?|cup|teaspoons?|tablespoons?|tsp|tbsp|pack|packs|pk|x)\b",
        " ",
        text,
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _load_catalog_entries() -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    settings = get_settings()
    path = settings.catalog_path or "resolver/catalog.json"
    try:
        stat = os.stat(path)
    except OSError:
        return {}, {}
    with _catalog_lock:
        global _catalog_by_product_id, _catalog_by_catalog_ref, _catalog_cache_mtime, _catalog_cache_path
        if (
            _catalog_by_product_id is not None
            and _catalog_cache_path == path
            and _catalog_cache_mtime >= stat.st_mtime
        ):
            return _catalog_by_product_id or {}, _catalog_by_catalog_ref or {}
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        entries: List[Dict[str, Any]] = []
        if isinstance(payload, list):
            entries = [entry for entry in payload if isinstance(entry, dict)]
        elif isinstance(payload, dict):
            if isinstance(payload.get("items"), list):
                entries = [entry for entry in payload["items"] if isinstance(entry, dict)]
            else:
                entries = [entry for entry in payload.values() if isinstance(entry, dict)]
        by_product: Dict[str, Dict[str, Any]] = {}
        by_catalog_ref: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            product_id = entry.get("productId") or entry.get("product_id") or entry.get("sku")
            catalog_ref = entry.get("catalogRefId") or entry.get("catalog_ref_id")
            if product_id is not None:
                by_product[str(product_id)] = entry
            if catalog_ref is not None:
                by_catalog_ref[str(catalog_ref)] = entry
        _catalog_by_product_id = by_product
        _catalog_by_catalog_ref = by_catalog_ref
        _catalog_cache_mtime = stat.st_mtime
        _catalog_cache_path = path
        return by_product, by_catalog_ref


def _lookup_catalog_product(product_id: Any, catalog_ref_id: Any) -> Dict[str, Any] | None:
    if product_id is None and catalog_ref_id is None:
        return None
    by_product, by_catalog_ref = _load_catalog_entries()
    if product_id is not None:
        entry = by_product.get(str(product_id))
        if entry:
            return entry
    if catalog_ref_id is not None:
        entry = by_catalog_ref.get(str(catalog_ref_id))
        if entry:
            return entry
    return None


def _normalize_product_meta(ingredient: Dict[str, Any]) -> Dict[str, Any] | None:
    selected = ingredient.get("selected_product") or ingredient.get("selectedProduct")
    if hasattr(selected, "model_dump"):
        selected = selected.model_dump(mode="python")
    product = dict(selected or {})
    product_id = _coalesce(
        product.get("product_id"),
        product.get("productId"),
        ingredient.get("product_id"),
        ingredient.get("productId"),
        ingredient.get("catalog_ref_id"),
        ingredient.get("catalogRefId"),
    )
    catalog_ref_id = (
        product.get("catalog_ref_id")
        or product.get("catalogRefId")
        or ingredient.get("catalog_ref_id")
        or ingredient.get("catalogRefId")
    )
    name = _coalesce(product.get("name"), ingredient.get("product_name"), ingredient.get("productName"))
    package_quantity = (
        _parse_numeric_quantity(product.get("package_quantity"))
        or _parse_numeric_quantity(ingredient.get("package_quantity"))
    )
    detail_url = _coalesce(
        product.get("detail_url"),
        product.get("detailUrl"),
        ingredient.get("detail_url"),
        ingredient.get("detailUrl"),
    )
    sale_price = _parse_numeric_quantity(
        product.get("sale_price")
        or product.get("salePrice")
        or ingredient.get("sale_price")
        or ingredient.get("salePrice")
    )
    ingredient_line = _coalesce(
        product.get("ingredient_line"),
        product.get("ingredientLine"),
        ingredient.get("ingredient_line"),
        ingredient.get("ingredientLine"),
    )
    catalog_ref_id = ingredient.get("catalog_ref_id") or ingredient.get("catalogRefId")
    catalog_entry = _lookup_catalog_product(product_id, catalog_ref_id)
    if catalog_entry:
        if product_id is None:
            catalog_product_id = (
                catalog_entry.get("productId")
                or catalog_entry.get("product_id")
                or catalog_entry.get("catalogRefId")
                or catalog_entry.get("catalog_ref_id")
            )
            if catalog_product_id is not None:
                product_id = str(catalog_product_id)
        name = name or catalog_entry.get("name") or catalog_entry.get("title")
        detail_url = detail_url or catalog_entry.get("detailUrl") or catalog_entry.get("url")
        if sale_price is None:
            sale_price = _parse_numeric_quantity(
                catalog_entry.get("salePrice")
                or catalog_entry.get("sale_price")
                or catalog_entry.get("price")
            )
        if ingredient_line is None:
            ingredient_line = catalog_entry.get("name") or catalog_entry.get("title")
    if not any([product_id, name, ingredient_line, detail_url]):
        return None
    return {
        "id": str(product_id) if product_id is not None else None,
        "catalog_ref_id": str(catalog_ref_id) if catalog_ref_id is not None else None,
        "name": name,
        "detail_url": detail_url,
        "sale_price": sale_price,
        "package_quantity": package_quantity,
        "ingredient_line": ingredient_line or name,
    }


def _parse_numeric_quantity(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    if value is None:
        return None
    return _parse_fractional_number(str(value))


def _parse_measurement_value(value: Any) -> Dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return {"base_amount": float(value), "unit_type": "count", "unit_label": "count"}
    text = str(value).strip().lower()
    if not text:
        return None
    multiplier_match = MULTIPLIER_PATTERN.search(text)
    if multiplier_match:
        count_value = _parse_fractional_number(multiplier_match.group(1))
        per_value = _parse_fractional_number(multiplier_match.group(2))
        unit_key = multiplier_match.group(3)
        if count_value is not None and per_value is not None:
            if unit_key and unit_key in UNIT_DEFINITIONS:
                definition = UNIT_DEFINITIONS[unit_key]
                base_amount = count_value * per_value * definition["multiplier"]
                return {
                    "base_amount": base_amount,
                    "unit_type": definition["unit_type"],
                    "unit_label": definition["unit_label"],
                }
            return {
                "base_amount": count_value * per_value,
                "unit_type": "count",
                "unit_label": "count",
            }
    unit_match = UNIT_PATTERN.search(text)
    if unit_match:
        amount = _parse_fractional_number(unit_match.group(1))
        unit_key = unit_match.group(2)
        if amount is not None and unit_key in UNIT_DEFINITIONS:
            definition = UNIT_DEFINITIONS[unit_key]
            return {
                "base_amount": amount * definition["multiplier"],
                "unit_type": definition["unit_type"],
                "unit_label": definition["unit_label"],
            }
    attached_match = ATTACHED_UNIT_PATTERN.search(text)
    if attached_match:
        amount = _parse_fractional_number(attached_match.group(1))
        unit_key = attached_match.group(2)
        if amount is not None and unit_key in UNIT_DEFINITIONS:
            definition = UNIT_DEFINITIONS[unit_key]
            return {
                "base_amount": amount * definition["multiplier"],
                "unit_type": definition["unit_type"],
                "unit_label": definition["unit_label"],
            }
    count_match = COUNT_PATTERN.search(text)
    if count_match:
        amount = _parse_fractional_number(count_match.group(1))
        if amount is not None:
            return {"base_amount": amount, "unit_type": "count", "unit_label": "count"}
    number_match = DEFAULT_NUMBER_PATTERN.search(text)
    if number_match:
        amount = _parse_fractional_number(number_match.group(1))
        if amount is not None:
            return {"base_amount": amount, "unit_type": "count", "unit_label": "count"}
    return None


def _parse_fractional_number(value: str) -> float | None:
    normalized = value.strip().replace(",", ".")
    compound_match = re.match(r"^(\d+)\s+(\d+)/(\d+)$", normalized)
    if compound_match:
        whole = float(compound_match.group(1))
        numerator = float(compound_match.group(2))
        denominator = float(compound_match.group(3))
        if denominator != 0:
            return whole + numerator / denominator
    fraction_match = re.match(r"^(\d+)/(\d+)$", normalized)
    if fraction_match:
        numerator = float(fraction_match.group(1))
        denominator = float(fraction_match.group(2))
        if denominator != 0:
            return numerator / denominator
    try:
        return float(re.findall(r"-?\d+(?:\.\d+)?", normalized)[0])
    except (IndexError, ValueError):
        return None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        text = str(value) if isinstance(value, (int, float)) else value
        if isinstance(text, str) and text.strip():
            return text
    return None


def _resolve_unit_price(products: Sequence[Dict[str, Any]], entries: Sequence[Dict[str, Any]]) -> float | None:
    for product in products or []:
        price = _coerce_sale_price_value(product)
        if price is not None:
            return price
        price = _lookup_catalog_price(product)
        if price is not None:
            return price
    for entry in entries or []:
        product_meta = entry.get("product") or {}
        price = _coerce_sale_price_value(product_meta)
        if price is not None:
            return price
        price = _lookup_catalog_price(product_meta)
        if price is not None:
            return price
    return None


def _coerce_sale_price_value(data: Dict[str, Any] | None) -> float | None:
    if not data:
        return None
    for key in ("sale_price", "salePrice", "price"):
        candidate = data.get(key)
        if candidate is None:
            continue
        if isinstance(candidate, dict):
            for nested_key in ("amount", "value", "price", "amountMinor", "amount_minor"):
                nested_value = candidate.get(nested_key)
                parsed = _parse_numeric_quantity(nested_value)
                if parsed is not None:
                    if "minor" in nested_key.lower():
                        return parsed / 100.0
                    return parsed
        else:
            parsed = _parse_numeric_quantity(candidate)
            if parsed is not None:
                return parsed
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        for key in ("salePrice", "price", "amount", "value"):
            candidate = metadata.get(key)
            parsed = _parse_numeric_quantity(candidate)
            if parsed is not None:
                return parsed
    return None


def _lookup_catalog_price(record: Dict[str, Any]) -> float | None:
    if not record:
        return None
    product_id = _coalesce(
        record.get("productId"),
        record.get("product_id"),
        record.get("id"),
    )
    catalog_ref_id = _coalesce(
        record.get("catalogRefId"),
        record.get("catalog_ref_id"),
    )
    if product_id is None and catalog_ref_id is None:
        return None
    entry = _lookup_catalog_product(product_id, catalog_ref_id)
    if not entry:
        return None
    return _parse_numeric_quantity(entry.get("salePrice") or entry.get("sale_price") or entry.get("price"))


def _to_minor_units(value: float | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return int(round(float(value) * 100))
