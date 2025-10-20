"""Helpers to interpret Woolworths `window.__INITIAL_STATE__` payloads."""

from __future__ import annotations

from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Set


def iter_records(state: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """Yield product records from a category initial state."""

    def _walk(node: Any) -> Iterable[Dict[str, Any]]:
        if isinstance(node, dict):
            records = node.get("records")
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict):
                        yield record
            for value in node.values():
                yield from _walk(value)
        elif isinstance(node, list):
            for item in node:
                yield from _walk(item)

    seen_ids: set[str] = set()
    for record in _walk(state):
        product_id = record.get("attributes", {}).get("p_productid")
        if product_id and product_id not in seen_ids:
            seen_ids.add(product_id)
            yield record


def get_total_records(state: Dict[str, Any]) -> Optional[int]:
    """Extract total results count from category state."""

    node: Any = state
    try:
        node = node["clp"]["SLPData"][0]["mainContent"][0]["contents"][0]
        pagination = node.get("pagination")
        if isinstance(pagination, dict):
            total = pagination.get("totalNumRecs")
            if isinstance(total, int):
                return total
    except (KeyError, IndexError, TypeError):
        return None
    return None


def build_product_summary(
    record: Dict[str, Any], *, category_path: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Normalise a single category record into a product summary dict."""

    attributes = record.get("attributes", {})
    product_id = str(attributes.get("p_productid") or "").strip()
    catalog_ref_id = str(attributes.get("p_styleColourId") or product_id or "").strip()
    sku = str(attributes.get("p_SKU") or product_id or "").strip()
    name = attributes.get("p_displayName") or attributes.get("product.displayName")
    brand = attributes.get("Brands") or attributes.get("brand")
    department = attributes.get("p_department")
    default_category = attributes.get("p_defaultCategoryName")
    category_id = attributes.get("p_defaultCategoryId")
    detail_path = attributes.get("detailPageURL")
    image_relative = attributes.get("p_imageReference")
    image_external = attributes.get("p_externalImageReference")

    price_info = record.get("startingPrice", {})
    sale_price = _first_price(
        price_info,
        ["p_pl00", "p_pl10", "p_pl30", "p_pl60"],
    )
    sale_price = float(sale_price) if sale_price is not None else None

    summary = {
        "product_id": product_id,
        "catalog_ref_id": catalog_ref_id or product_id,
        "sku": sku or product_id,
        "name": name,
        "brand": brand,
        "department": department,
        "default_category": default_category,
        "default_category_id": category_id,
        "detail_url": absolute_url(detail_path),
        "image_url": image_external or absolute_url(image_relative),
        "sale_price": sale_price,
        "attributes": attributes,
    }

    if category_path:
        summary["path"] = category_path

    return summary


def extract_product_detail(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the rich product detail payload from a PDP initial state."""

    pdp = state.get("pdp")
    if not isinstance(pdp, dict):
        return None

    info = pdp.get("productInfo")
    if not isinstance(info, dict):
        return None

    # When multiple keys exist, the main product information is stored directly
    # on the dict under scalar keys (longDescription, images, etc.).
    return info


def extract_nav_urls(state: Dict[str, Any]) -> Set[str]:
    """Collect navigation URLs (`/cat/...`) present in the page state."""

    navs: Set[str] = set()
    queue: deque[Any] = deque([state])
    while queue:
        node = queue.popleft()
        if isinstance(node, dict):
            nav = node.get("navigationURL") or node.get("navigationUrl")
            if isinstance(nav, str):
                navs.add(nav.strip())
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)
    return navs


def extract_breadcrumb_labels(state: Dict[str, Any]) -> List[str]:
    breadcrumbs = _find_breadcrumbs(state)
    labels: List[str] = []
    for crumb in breadcrumbs:
        label = (crumb.get("label") or crumb.get("displayName") or "").strip()
        if label:
            labels.append(label)
    return labels


def _find_breadcrumbs(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    queue: deque[Any] = deque([state])
    while queue:
        node = queue.popleft()
        if isinstance(node, dict):
            crumbs = node.get("breadcrumbs")
            if isinstance(crumbs, list):
                return crumbs
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)
    return []


def absolute_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith("http"):
        return path
    if not path.startswith("/"):
        path = "/" + path
    return f"https://www.woolworths.co.za{path}"


def _first_price(source: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    for key in keys:
        value = source.get(key)
        if value is None or value == "":
            continue
        try:
            as_float = float(value)
        except (TypeError, ValueError):
            continue
        if as_float > 0:
            return as_float
    return None
