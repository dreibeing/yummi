from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import threading
from collections import defaultdict
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

_product_index_lock = threading.Lock()
_ingredient_product_index: Dict[str, List[Dict[str, Any]]] | None = None
_ingredient_product_index_path: str | None = None
_ingredient_product_index_mtime: float = 0.0

MAX_CLASSIFIED_PRODUCTS = 12
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
    llm_items = await _score_ingredient_groups(
        meals=meals,
        ingredient_groups=ingredient_groups,
        system_prompt=system_prompt,
        settings=settings,
    )
    items: List[ShoppingListResultItem] = []
    for group in ingredient_groups:
        group_key = str(group.get("group_key"))
        items.append(llm_items.get(group_key) or _build_result_item(group, None))
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
            package_count = _derive_package_count(ingredient, requirement_measurement)
            product_meta = _normalize_product_meta(ingredient)
            package_measurement = None
            if product_meta:
                package_measurement = _parse_measurement_value(
                    product_meta.get("ingredient_line")
                    or product_meta.get("name")
                    or fallback
                )
            required_quantity = package_count if package_count is not None else 1.0
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
                    "package_count": package_count,
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
        base_products = _build_linked_products(entries)
        classified_products = _lookup_group_product_options(group)
        group["linked_products"] = _merge_product_lists(base_products, classified_products)
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
                "imageUrl": product.get("image_url") or product.get("imageUrl"),
            }
        )
    return products


def _derive_package_count(
    ingredient: Dict[str, Any], requirement_measurement: Dict[str, Any] | None
) -> float | None:
    raw_package_quantity = ingredient.get("package_quantity")
    package_count = _parse_numeric_quantity(raw_package_quantity)
    if package_count is not None:
        return package_count
    if (
        requirement_measurement
        and requirement_measurement.get("unit_type") == "count"
        and isinstance(requirement_measurement.get("base_amount"), (int, float))
    ):
        amount = float(requirement_measurement["base_amount"])
        if math.isfinite(amount):
            return amount
    return None


def _lookup_group_product_options(group: Dict[str, Any]) -> List[Dict[str, Any]]:
    labels: List[str] = []
    for key in ("group_key", "label"):
        value = group.get(key)
        if isinstance(value, str):
            labels.append(value)
    for entry in group.get("entries", []):
        entry_labels = entry.get("labels") or []
        for label in entry_labels:
            if isinstance(label, str):
                labels.append(label)
    return _lookup_indexed_products(labels)


def _merge_product_lists(
    primary: Sequence[Dict[str, Any]], secondary: Sequence[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _append(items: Sequence[Dict[str, Any]]) -> None:
        for item in items or []:
            key = (
                item.get("productId")
                or item.get("product_id")
                or item.get("catalogRefId")
                or item.get("catalog_ref_id")
                or item.get("name")
            )
            if not key:
                continue
            normalized_key = str(key)
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            merged.append(item)

    _append(primary)
    _append(secondary)
    return merged


def _lookup_indexed_products(labels: Sequence[str]) -> List[Dict[str, Any]]:
    if not labels:
        return []
    index = _load_ingredient_product_index()
    if not index:
        return []
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for label in labels:
        normalized = _normalize_label(label)
        if not normalized:
            continue
        for option in index.get(normalized) or []:
            key = option.get("productId") or option.get("catalogRefId") or option.get("name")
            if not key:
                continue
            normalized_key = str(key)
            if normalized_key in seen:
                continue
            seen.add(normalized_key)
            results.append(dict(option))
            if len(results) >= MAX_CLASSIFIED_PRODUCTS:
                return results
    return results


def _summarize_requirement(entries: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    unit_type: str | None = None
    unit_label: str | None = None
    total_amount = 0.0
    has_amount = False
    for entry in entries:
        measurement = entry.get("requirement_measurement")
        if (
            not measurement
            or measurement.get("base_amount") is None
            or not isinstance(measurement.get("base_amount"), (int, float))
        ):
            continue
        if not unit_type:
            unit_type = measurement.get("unit_type")
            unit_label = measurement.get("unit_label")
            total_amount = float(measurement.get("base_amount") or 0.0)
            has_amount = True
            continue
        if unit_type == measurement.get("unit_type"):
            total_amount += float(measurement.get("base_amount") or 0.0)
            has_amount = True
        else:
            unit_type = None
            unit_label = None
            total_amount = 0.0
            has_amount = False
            break
    return {
        "unit_type": unit_type,
        "unit_label": unit_label,
        "amount": total_amount if has_amount and unit_type else None,
    }


def _build_system_prompt() -> str:
    return (
        "You are Yummi's grocery planning assistant. Every ingredient you see already maps to the provided Woolworths "
        "products, so you must choose one of them. Determine whether the user should pick it up now or treat it as a "
        "pantry staple, then calculate the number of whole retail packs (rounding up so the meals have enough). "
        "Never invent products, never skip an entry, and only return the requested identifiers and integer pack counts."
    )


def _build_user_prompt(
    meals: Sequence[ShoppingListMealPayload],
    ingredient_groups: Sequence[Dict[str, Any]],
    limit_meal_ids: Sequence[str] | None = None,
) -> str:
    meal_context = _build_meal_context(meals, limit_meal_ids)
    group_context: List[Dict[str, Any]] = []
    for group in ingredient_groups:
        requirements = []
        for entry in group.get("entries", []):
            requirements.append(
                {
                    "entry_id": entry.get("entry_id"),
                    "meal_id": entry.get("meal_id"),
                    "meal_name": entry.get("meal_name"),
                    "quantity_text": entry.get("quantity_text"),
                    "requirement_measurement": entry.get("requirement_measurement"),
                    "display": entry.get("display_text"),
                    "labels": entry.get("labels"),
                }
            )
        group_context.append(
            {
                "group_key": group.get("group_key"),
                "label": group.get("label"),
                "total_entries": len(group.get("entries", [])),
                "requirements": requirements,
                "requirement_summary": group.get("requirement_summary"),
                "linked_products": group.get("linked_products"),
            }
        )
    schema = {
        "items": [
            {
                "group_key": "string identifier from ingredient_groups[*].group_key",
                "classification": "pickup | pantry",
                "product_id": "productId drawn from linked_products[].productId (omit only when no product can satisfy the requirement)",
                "catalog_ref_id": "optional catalogRefId when product_id is unavailable",
                "packages": "integer number of retail packs to buy (>= 0; 0 allowed only for pantry classification)",
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
        "You receive exactly one ingredient group per request. For that group:\n"
        "1. Study each `requirements[]` entry. Use the text plus any `requirement_measurement` hints to understand the total quantity needed across all meals.\n"
        "2. Review `linked_products` to see every Woolworths SKU already linked to this ingredient. ALL ingredients already map to these SKUs, so you must choose one of them—never invent or skip a product.\n"
        "3. Decide whether the user needs to purchase the ingredient now (`classification = pickup`) or if it is a pantry staple already on hand (`classification = pantry`). Pantry items must always use the provided product but set `packages = 0`.\n"
        "4. Compute how many retail packs to buy for the chosen product. `packages` must be a WHOLE number (integer) and for pickup items it must be at least 1. Round up so the user always has enough to satisfy every meal.\n"
        "5. Return exactly one JSON entry that matches the schema—include both `product_id` (from `linked_products[].productId`) and the closest `catalog_ref_id` when present."
    )
    return (
        f"{instructions}\nContext JSON:\n```json\n{context_json}\n```\n"
        f"Output schema:\n```json\n{schema_json}\n```"
    )


def _build_meal_context(
    meals: Sequence[ShoppingListMealPayload],
    limit_meal_ids: Sequence[str] | None,
) -> List[Dict[str, Any]]:
    allowed = {str(meal_id) for meal_id in limit_meal_ids or [] if meal_id is not None} or None

    def _serialize(meal: ShoppingListMealPayload) -> Dict[str, Any]:
        return {
            "meal_id": meal.meal_id,
            "name": meal.name,
            "servings": meal.servings,
            "ingredient_count": len(getattr(meal, "ingredients", []) or []),
        }

    context: List[Dict[str, Any]] = []
    for meal in meals:
        meal_id = getattr(meal, "meal_id", None)
        meal_id_str = str(meal_id) if meal_id is not None else None
        if allowed and (meal_id_str is None or meal_id_str not in allowed):
            continue
        context.append(_serialize(meal))
    if not context:
        context = [_serialize(meal) for meal in meals]
    return context


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


async def _score_ingredient_groups(
    *,
    meals: Sequence[ShoppingListMealPayload],
    ingredient_groups: Sequence[Dict[str, Any]],
    system_prompt: str,
    settings,
) -> Dict[str, ShoppingListResultItem]:
    if not ingredient_groups:
        return {}
    tasks = [
        asyncio.create_task(
            _score_single_group(
                meals=meals,
                group=group,
                system_prompt=system_prompt,
                settings=settings,
            )
        )
        for group in ingredient_groups
    ]
    results: Dict[str, ShoppingListResultItem] = {}
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    for group, outcome in zip(ingredient_groups, responses):
        group_key = str(group.get("group_key"))
        if isinstance(outcome, Exception):
            logger.warning(
                "Shopping list group run failed group=%s error=%s",
                group_key,
                outcome,
            )
            results[group_key] = _build_manual_selection_item(
                group,
                reason="Unable to select a Woolworths product automatically.",
            )
            continue
        results[group_key] = outcome
    return results


async def _score_single_group(
    *,
    meals: Sequence[ShoppingListMealPayload],
    group: Dict[str, Any],
    system_prompt: str,
    settings,
) -> ShoppingListResultItem:
    meal_ids = list(_collect_group_meal_ids(group))
    user_prompt = _build_user_prompt(
        meals,
        [group],
        limit_meal_ids=meal_ids or None,
    )
    llm_text = await asyncio.to_thread(
        call_openai_responses,
        model=settings.openai_shopping_list_model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_output_tokens=settings.openai_shopping_list_max_output_tokens,
        top_p=settings.openai_shopping_list_top_p,
        reasoning_effort=settings.openai_shopping_list_reasoning_effort,
    )
    payload = _parse_model_response(llm_text)
    llm_entry = _extract_group_entry(payload, group.get("group_key"))
    return _build_result_item(group, llm_entry)


def _extract_group_entry(payload: Dict[str, Any], group_key: Any) -> Dict[str, Any] | None:
    entries = payload.get("items") or []
    if not isinstance(entries, list):
        return None
    target = str(group_key) if group_key is not None else None
    fallback: Dict[str, Any] | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("group_key") or entry.get("key") or entry.get("id")
        if key is not None and target is not None and str(key) == target:
            return entry
        if fallback is None:
            fallback = entry
    if len(entries) == 1:
        return fallback
    return None


def _build_result_item(group: Dict[str, Any], llm_entry: Dict[str, Any] | None) -> ShoppingListResultItem:
    group_label = group.get("label") or group.get("group_key") or "Ingredient"
    display_name = group_label
    products = group.get("linked_products") or []
    if not llm_entry:
        return _build_manual_selection_item(group, reason="Model returned no selection.")
    classification = _coerce_classification(llm_entry.get("classification"))
    if not classification:
        return _build_manual_selection_item(
            group,
            reason=f"No classification returned for {group_label}.",
        )
    packages = _parse_llm_packages(llm_entry.get("packages"), classification)
    if packages is None:
        return _build_manual_selection_item(
            group,
            reason=f"No valid package count returned for {group_label}.",
        )
    product_id = llm_entry.get("product_id") or llm_entry.get("productId")
    catalog_ref_id = llm_entry.get("catalog_ref_id") or llm_entry.get("catalogRefId")
    selected_product = _select_product_option(products, product_id, catalog_ref_id)
    if classification == "pickup" and not selected_product:
        return _build_manual_selection_item(
            group,
            reason=f"No viable Woolworths product was returned for {group_label}.",
        )
    products = [selected_product] if selected_product else []
    product_display_name = _resolve_product_display_name(
        selected_product,
        fallback_product_id=product_id,
        fallback_catalog_ref_id=catalog_ref_id,
    )
    if product_display_name:
        display_name = product_display_name
        if selected_product is not None:
            selected_product["name"] = product_display_name
    required_quantity = float(packages)
    default_quantity = 0.0 if classification == "pantry" else required_quantity
    unit_price = _resolve_unit_price(products, group.get("entries") or [])
    unit_price_minor = _to_minor_units(unit_price)
    selection_models = (
        [
            _build_product_selection_model(selected_product, packages)
        ]
        if selected_product
        else []
    )
    return ShoppingListResultItem(
        id=group.get("group_key") or display_name,
        groupKey=group.get("group_key") or display_name,
        text=str(display_name),
        classification=classification,
        productName=product_display_name or display_name,
        requiredQuantity=required_quantity,
        defaultQuantity=default_quantity,
        notes=None,
        linkedProducts=selection_models,
        unitPrice=unit_price,
        unitPriceMinor=unit_price_minor,
    )


def _build_manual_selection_item(group: Dict[str, Any], reason: str | None = None) -> ShoppingListResultItem:
    group_label = group.get("label") or group.get("group_key") or "Ingredient"
    note = reason or f"We couldn't find a Woolworths product for {group_label}."
    return ShoppingListResultItem(
        id=group.get("group_key") or group_label,
        groupKey=group.get("group_key") or group_label,
        text=str(group_label),
        productName=None,
        classification="pickup",
        requiredQuantity=0.0,
        defaultQuantity=0.0,
        notes=note,
        linkedProducts=[],
        unitPrice=None,
        unitPriceMinor=None,
        needsManualProductSelection=True,
    )


def _select_product_option(
    products: Sequence[Dict[str, Any]],
    product_id: Any,
    catalog_ref_id: Any,
) -> Dict[str, Any] | None:
    if not products:
        return None
    target_id = str(product_id) if product_id is not None else None
    target_catalog = str(catalog_ref_id) if catalog_ref_id is not None else None
    for product in products:
        pid = product.get("productId") or product.get("product_id")
        cid = product.get("catalogRefId") or product.get("catalog_ref_id")
        if target_id and pid and str(pid) == target_id:
            return dict(product)
        if target_catalog and cid and str(cid) == target_catalog:
            return dict(product)
    return None


def _extract_pack_measurement(
    product: Dict[str, Any] | None,
) -> tuple[float | None, str | None]:
    if not product:
        return None, None
    measurement = product.get("packageMeasurement") or product.get("package_measurement")
    if measurement:
        amount = measurement.get("base_amount")
        unit_type = measurement.get("unit_type")
        if isinstance(amount, (int, float)) and math.isfinite(amount) and unit_type:
            return float(amount), unit_type
    package_quantity = product.get("packageQuantity")
    if isinstance(package_quantity, (int, float)) and math.isfinite(package_quantity):
        return float(package_quantity), "count"
    return None, None


def _build_product_selection_model(
    product: Dict[str, Any] | None,
    packages: float,
) -> ShoppingListProductSelection:
    product = product or {}
    product_name = _resolve_product_display_name(product)
    return ShoppingListProductSelection(
        productId=product.get("productId") or product.get("product_id"),
        catalogRefId=product.get("catalogRefId") or product.get("catalog_ref_id"),
        name=product_name,
        detailUrl=product.get("detailUrl") or product.get("detail_url"),
        salePrice=product.get("salePrice"),
        packages=packages,
        imageUrl=product.get("imageUrl") or product.get("image_url"),
    )


def _resolve_product_display_name(
    product: Dict[str, Any] | None,
    *,
    fallback_product_id: Any | None = None,
    fallback_catalog_ref_id: Any | None = None,
) -> str | None:
    product = product or {}
    product_id = (
        fallback_product_id
        or product.get("productId")
        or product.get("product_id")
        or product.get("id")
    )
    catalog_ref_id = (
        fallback_catalog_ref_id
        or product.get("catalogRefId")
        or product.get("catalog_ref_id")
    )
    catalog_entry = _lookup_catalog_product(product_id, catalog_ref_id)
    if catalog_entry:
        for key in ("name", "title"):
            value = catalog_entry.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("name", "ingredientLine", "ingredient_line"):
        value = product.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _coerce_classification(value: Any) -> str | None:
    if not value:
        return None
    lowered = str(value).strip().lower()
    if lowered in {"pickup", "pantry"}:
        return lowered
    return None


def _parse_llm_packages(value: Any, classification: str) -> float | None:
    parsed = _parse_numeric_quantity(value)
    if parsed is None:
        return None
    if not isinstance(parsed, (int, float)) or not math.isfinite(parsed):
        return None
    if parsed < 0:
        return None
    if classification == "pantry":
        return 0.0
    return float(max(1, math.ceil(parsed)))


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


def _load_ingredient_product_index() -> Dict[str, List[Dict[str, Any]]]:
    settings = get_settings()
    path = settings.ingredient_classifications_path
    if not path:
        return {}
    try:
        stat = os.stat(path)
    except OSError:
        return {}
    global _ingredient_product_index, _ingredient_product_index_mtime, _ingredient_product_index_path
    with _product_index_lock:
        if (
            _ingredient_product_index is not None
            and _ingredient_product_index_path == path
            and _ingredient_product_index_mtime >= stat.st_mtime
        ):
            return _ingredient_product_index
        mapping: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        seen_keys: Dict[str, set[str]] = defaultdict(set)
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                core_name = payload.get("core_item_name") or payload.get("coreItemName")
                normalized = _normalize_label(core_name)
                if not normalized:
                    continue
                product_id = (
                    payload.get("product_id")
                    or payload.get("productId")
                    or payload.get("sku")
                )
                catalog_ref_id = payload.get("catalog_ref_id") or payload.get("catalogRefId")
                option = _build_classified_product_option(product_id, catalog_ref_id, core_name)
                if not option:
                    continue
                key = (
                    option.get("productId")
                    or option.get("catalogRefId")
                    or option.get("name")
                )
                if not key:
                    continue
                normalized_key = str(key)
                if normalized_key in seen_keys[normalized]:
                    continue
                seen_keys[normalized].add(normalized_key)
                mapping[normalized].append(option)
        _ingredient_product_index = dict(mapping)
        _ingredient_product_index_path = path
        _ingredient_product_index_mtime = stat.st_mtime
        return _ingredient_product_index


def _build_classified_product_option(
    product_id: Any, catalog_ref_id: Any, fallback_name: str | None
) -> Dict[str, Any] | None:
    if product_id is None and catalog_ref_id is None and not fallback_name:
        return None
    catalog_entry = _lookup_catalog_product(product_id, catalog_ref_id)
    name = fallback_name
    detail_url = None
    sale_price = None
    package_quantity = None
    ingredient_line = fallback_name
    image_url = None
    if catalog_entry:
        if product_id is None:
            catalog_product_id = (
                catalog_entry.get("productId")
                or catalog_entry.get("product_id")
                or catalog_entry.get("catalogRefId")
                or catalog_entry.get("catalog_ref_id")
            )
            if catalog_product_id is not None:
                product_id = catalog_product_id
        if catalog_ref_id is None:
            catalog_ref_id = (
                catalog_entry.get("catalogRefId") or catalog_entry.get("catalog_ref_id")
            )
        name = name or catalog_entry.get("name") or catalog_entry.get("title")
        detail_url = catalog_entry.get("detailUrl") or catalog_entry.get("url")
        sale_price = _parse_numeric_quantity(
            catalog_entry.get("salePrice") or catalog_entry.get("sale_price") or catalog_entry.get("price")
        )
        package_quantity = (
            _parse_numeric_quantity(
                catalog_entry.get("packageQuantity")
                or catalog_entry.get("package_quantity")
                or catalog_entry.get("size")
            )
            or package_quantity
        )
        ingredient_line = ingredient_line or name
        image_url = catalog_entry.get("imageUrl") or catalog_entry.get("image_url")
    if product_id is None and catalog_ref_id is None and not name:
        return None
    return {
        "productId": str(product_id) if product_id is not None else None,
        "catalogRefId": str(catalog_ref_id) if catalog_ref_id is not None else None,
        "name": name,
        "detailUrl": detail_url,
        "salePrice": sale_price,
        "packageQuantity": package_quantity,
        "ingredientLine": ingredient_line,
        "imageUrl": image_url,
    }


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
    image_url = _coalesce(
        product.get("image_url"),
        product.get("imageUrl"),
        ingredient.get("image_url"),
        ingredient.get("imageUrl"),
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
        if image_url is None:
            image_url = catalog_entry.get("imageUrl") or catalog_entry.get("image_url")
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
        "image_url": image_url,
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
def _collect_group_meal_ids(group: Dict[str, Any]) -> set[str]:
    meal_ids: set[str] = set()
    for entry in group.get("entries") or []:
        meal_id = entry.get("meal_id")
        if meal_id:
            meal_ids.add(str(meal_id))
    return meal_ids
