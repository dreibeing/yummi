from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List


def extract_key_ingredients(meal: Dict[str, Any], limit: int = 6) -> List[Dict[str, Any]]:
    """Return a compact list of key ingredients for prompting."""
    collection: List[Dict[str, Any]] = []
    for ingredient in (meal.get("final_ingredients") or meal.get("ingredients") or []):
        label = ingredient.get("core_item_name") or ingredient.get("ingredient_line")
        if not label:
            continue
        entry = {
            "name": label,
            "quantity": ingredient.get("quantity"),
            "product": (ingredient.get("selected_product") or {}).get("name"),
        }
        collection.append(entry)
        if len(collection) >= limit:
            break
    return collection


def extract_sku_snapshot(meal: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    """Provide a concise SKU summary for downstream consumers."""
    snapshots: List[Dict[str, Any]] = []
    for ingredient in meal.get("final_ingredients") or []:
        product = ingredient.get("selected_product") or {}
        if not any(
            [
                product.get("product_id"),
                product.get("name"),
                product.get("detail_url"),
                product.get("sale_price"),
            ]
        ):
            continue
        snapshots.append(
            {
                "productId": product.get("product_id"),
                "name": product.get("name"),
                "detailUrl": product.get("detail_url"),
                "salePrice": product.get("sale_price"),
            }
        )
        if len(snapshots) >= limit:
            break
    return snapshots


def format_json(payload: Any) -> str:
    """Pretty-print objects for prompt context without breaking datetime fields."""

    def _default(value: Any):
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    return json.dumps(payload, indent=2, default=_default)
