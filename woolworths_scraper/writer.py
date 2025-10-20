"""Output helpers for scraped product data."""

from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping, Sequence

logger = logging.getLogger(__name__)


def ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_jsonl(records: Iterable[Mapping[str, object]], path: Path) -> None:
    ensure_dir(path)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")


def write_csv(records: Iterable[Mapping[str, object]], path: Path, *, fieldnames: list[str]) -> None:
    ensure_dir(path)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key) for key in fieldnames})


def write_catalog(records: Sequence[Mapping[str, object]], path: Path) -> None:
    """Write resolver catalog mapping normalized titles to canonical IDs."""

    catalog: dict[str, MutableMapping[str, object]] = {}
    collision_count = 0

    for record in records:
        name = record.get("name")
        product_id = record.get("product_id") or record.get("productId")
        if not name or not product_id:
            continue

        normalized = _normalize_title(str(name))
        if not normalized:
            continue

        entry = _build_catalog_entry(record)
        if not entry:
            continue

        existing = catalog.get(normalized)
        if existing:
            if existing.get("productId") == entry.get("productId"):
                continue
            alternates = existing.setdefault("alternates", [])
            if isinstance(alternates, list) and all(
                alt.get("productId") != entry.get("productId") for alt in alternates if isinstance(alt, dict)
            ):
                alternates.append(entry)
                collision_count += 1
            continue

        catalog[normalized] = entry

    ensure_dir(path)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(
            dict(sorted(catalog.items(), key=lambda item: item[0])),
            fh,
            ensure_ascii=False,
            indent=2,
        )
        fh.write("\n")

    if collision_count:
        logger.info("Catalog collisions resolved via alternates: %s", collision_count)


def _normalize_title(name: str) -> str:
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", " ", slug)
    return " ".join(slug.split())


def _build_catalog_entry(record: Mapping[str, object]) -> MutableMapping[str, object] | None:
    product_id = _as_str(record.get("product_id") or record.get("productId"))
    catalog_ref = _as_str(record.get("catalog_ref_id") or record.get("catalogRefId") or product_id)
    sku = _as_str(record.get("sku") or record.get("SKU") or product_id)
    detail_url = _as_str(record.get("detail_url") or record.get("detailUrl"))
    brand = _as_str(record.get("brand"))
    default_category = _as_str(record.get("default_category") or record.get("defaultCategory"))
    path_raw = record.get("path")
    sale_price = record.get("sale_price") or record.get("salePrice")
    image_url = _as_str(record.get("image_url") or record.get("imageUrl"))

    if not product_id:
        return None

    entry: MutableMapping[str, object] = {
        "name": _as_str(record.get("name")) or "",
        "productId": product_id,
        "catalogRefId": catalog_ref or product_id,
        "sku": sku or product_id,
    }

    if detail_url:
        entry["detailUrl"] = detail_url
    if image_url:
        entry["imageUrl"] = image_url
    if brand:
        entry["brand"] = brand
    if default_category:
        entry["defaultCategory"] = default_category
    path = _coerce_str_list(path_raw)
    if path:
        entry["path"] = path
    if isinstance(sale_price, (int, float)):
        entry["salePrice"] = float(sale_price)

    return entry


def _coerce_str_list(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if item is not None]
    return []


def _as_str(value: object) -> str:
    if value is None:
        return ""
    return str(value)
