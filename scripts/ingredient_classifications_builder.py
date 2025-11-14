#!/usr/bin/env python3
"""Consolidate ingredient LLM responses into reusable tables."""

from __future__ import annotations

import argparse
import json
import csv
from pathlib import Path
from typing import Iterable, List, Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--responses-dir",
        type=Path,
        default=Path("data/ingredients/llm_batches/responses"),
        help="Directory containing per-batch response JSON files",
    )
    parser.add_argument(
        "--all-results",
        type=Path,
        default=Path("data/ingredients/llm_batches/responses/all_results.jsonl"),
        help="Path to the aggregated JSONL created during classification",
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=Path("data/ingredients/ingredient_classifications.jsonl"),
        help="Path to write consolidated product-level results",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/ingredients/ingredient_classifications.csv"),
        help="Path to write consolidated results as CSV",
    )
    parser.add_argument(
        "--clean-output",
        type=Path,
        default=Path("data/ingredients/unique_core_items.csv"),
        help="Path to write the deduplicated (core_item_name, item_type) table",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def read_all_results(path: Path) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing aggregated results file: {path}")
    records: List[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            records.append(payload)
    if not records:
        raise ValueError(f"No records found in {path}")
    return records


def write_jsonl(records: Iterable[dict], path: Path) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")


def write_csv(records: Iterable[dict], fieldnames: Sequence[str], path: Path) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in fieldnames})


def build_clean_items(records: Iterable[dict]) -> List[dict]:
    seen = set()
    clean_rows: List[dict] = []
    for record in records:
        name = (record.get("core_item_name") or "").strip()
        item_type = (record.get("item_type") or "").strip()
        if not name or not item_type:
            continue
        key = (name.lower(), item_type.lower())
        if key in seen:
            continue
        seen.add(key)
        clean_rows.append({"core_item_name": name, "item_type": item_type})
    clean_rows.sort(key=lambda row: (row["item_type"], row["core_item_name"]))
    return clean_rows


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    records = read_all_results(args.all_results)

    essential_fields = ("product_id", "core_item_name", "item_type", "batch_id")
    normalized = []
    for record in records:
        normalized.append({field: record.get(field) for field in essential_fields})

    write_jsonl(normalized, args.output_jsonl)
    write_csv(normalized, essential_fields, args.output_csv)

    clean_rows = build_clean_items(normalized)
    write_csv(clean_rows, ("core_item_name", "item_type"), args.clean_output)

    print(f"Wrote {len(normalized)} classified rows -> {args.output_jsonl}")
    print(f"Wrote {len(clean_rows)} unique core items -> {args.clean_output}")


if __name__ == "__main__":  # pragma: no cover
    main()
