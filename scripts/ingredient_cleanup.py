"""CLI to down-select Woolworths product data into candidate cooking ingredients."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


@dataclass(frozen=True)
class Filters:
    drop_path_contains: Sequence[str]
    drop_categories: Sequence[str]
    allow_categories: Sequence[str]
    drop_keyword_patterns: Sequence[str]
    ready_meal_categories: Sequence[str]
    ready_meal_keywords: Sequence[str]

    @classmethod
    def from_json(cls, path: Path) -> "Filters":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            drop_path_contains=tuple(payload.get("drop_path_contains", ())),
            drop_categories=tuple(payload.get("drop_categories", ())),
            allow_categories=tuple(payload.get("allow_categories", ())),
            drop_keyword_patterns=tuple(payload.get("drop_keyword_patterns", ())),
            ready_meal_categories=tuple(payload.get("ready_meal_categories", ())),
            ready_meal_keywords=tuple(payload.get("ready_meal_keywords", ())),
        )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--products-csv",
        type=Path,
        default=Path("data/product_table_folder/woolworths_products_summary.csv"),
        help="Path to the CSV summary exported by the Woolworths scraper",
    )
    parser.add_argument(
        "--filters",
        type=Path,
        default=Path("data/catalog_filters.json"),
        help="Path to heuristic filter config (JSON)",
    )
    parser.add_argument(
        "--candidates-output",
        type=Path,
        default=Path("data/ingredients/ingredient_candidates.jsonl"),
        help="Where to write JSONL candidate rows after heuristic filtering",
    )
    parser.add_argument(
        "--drops-output",
        type=Path,
        default=Path("data/ingredients/ingredient_drops.jsonl"),
        help="Where to write JSONL rows that were removed by heuristics",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("data/ingredients/reports/filter_summary.json"),
        help="Where to write a summary of filter counts",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit for debugging",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def read_products(csv_path: Path, limit: int | None = None) -> Iterable[dict]:
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader):
            yield row
            if limit is not None and idx + 1 >= limit:
                break


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def parse_path_list(value: str) -> List[str]:
    if not value:
        return []
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return [str(part) for part in parsed]
        return []
    except Exception:
        return []


def normalize_name(name: str, brand: str | None = None) -> str:
    text = name.lower()
    if brand:
        brand_tokens = re.split(r"[^a-z0-9]+", brand.lower())
        for token in filter(None, brand_tokens):
            text = re.sub(rf"\b{re.escape(token)}\b", " ", text)
    text = re.sub(r"woolworths|woolies|ww", " ", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(kg|g|l|ml|pack|pk|cm|mm|count|dose|portion|serves?)\b", " ", text)
    text = re.sub(r"\b\d+x\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def detect_ready_meal(default_category: str, normalized_name: str, filters: Filters) -> bool:
    if default_category in filters.ready_meal_categories:
        return True
    return any(keyword in normalized_name for keyword in filters.ready_meal_keywords)


def should_drop(path_tokens: Sequence[str], default_category: str, normalized_name: str, filters: Filters) -> list[str]:
    reasons: list[str] = []
    allowlisted = default_category in filters.allow_categories

    if not allowlisted:
        path_reason = first_match(path_tokens, filters.drop_path_contains)
        if path_reason:
            reasons.append(f"path:{path_reason}")

        if default_category in filters.drop_categories:
            reasons.append(f"category:{default_category}")

        keyword_reason = first_keyword_match(normalized_name, filters.drop_keyword_patterns)
        if keyword_reason:
            reasons.append(f"keyword:{keyword_reason}")

    return reasons


def first_match(values: Sequence[str], candidates: Sequence[str]) -> str | None:
    normalized = [v.lower() for v in values]
    target = [c.lower() for c in candidates]
    for candidate in target:
        for value in normalized:
            if value == candidate:
                return candidate
    return None


def first_keyword_match(normalized_name: str, keywords: Sequence[str]) -> str | None:
    for keyword in keywords:
        if keyword and keyword in normalized_name:
            return keyword
    return None


def write_jsonl(records: Iterable[dict], path: Path) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")


def main(argv: Iterable[str] | None = None) -> None:
    args = parse_args(argv)
    filters = Filters.from_json(args.filters)

    candidates: list[dict] = []
    drops: list[dict] = []
    drop_counter: Counter[str] = Counter()

    total = 0
    for row in read_products(args.products_csv, args.limit):
        total += 1
        path_tokens = parse_path_list(row.get("path", ""))
        default_category = (row.get("default_category") or "").strip()
        normalized_name = normalize_name(row.get("name", ""), row.get("brand"))
        ready_meal = detect_ready_meal(default_category, normalized_name, filters)
        drop_reasons = should_drop(path_tokens, default_category, normalized_name, filters)

        payload = {
            "product_id": row.get("product_id"),
            "catalog_ref_id": row.get("catalog_ref_id"),
            "sku": row.get("sku"),
            "name": row.get("name"),
            "brand": row.get("brand"),
            "sale_price": float(row["sale_price"]) if row.get("sale_price") else None,
            "detail_url": row.get("detail_url"),
            "default_category": default_category,
            "category_path": path_tokens,
            "department": row.get("department"),
            "normalized_name": normalized_name,
            "ready_meal_hint": ready_meal,
        }

        if drop_reasons:
            payload["drop_reasons"] = drop_reasons
            drops.append(payload)
            for reason in drop_reasons:
                drop_counter[reason] += 1
            continue

        payload["product_type"] = "ready_meal" if ready_meal else "ingredient"
        candidates.append(payload)

    write_jsonl(candidates, args.candidates_output)
    write_jsonl(drops, args.drops_output)

    summary = {
        "total_rows": total,
        "candidates": len(candidates),
        "dropped": len(drops),
        "drop_reason_counts": drop_counter.most_common(),
        "filters_path": str(args.filters),
        "source": str(args.products_csv),
    }
    ensure_parent(args.summary_output)
    args.summary_output.write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    main()
