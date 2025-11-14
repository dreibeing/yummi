"""Create compact GPT batch payloads from ingredient candidates."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Sequence


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidates-file",
        type=Path,
        default=Path("data/ingredients/ingredient_candidates.jsonl"),
        help="Path to the heuristic-filtered candidate JSONL",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/ingredients/llm_batches"),
        help="Directory to write batch JSON files",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Number of records per batch file",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional clamp for quick tests",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def load_candidates(path: Path, limit: int | None = None) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fh:
        for idx, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)
            if limit is not None and idx + 1 >= limit:
                break


def trim_record(record: dict) -> dict:
    return {
        "product_id": record.get("product_id"),
        "product_type": record.get("product_type"),
        "ready_meal_hint": record.get("ready_meal_hint", False),
        "normalized_name": record.get("normalized_name"),
        "default_category": record.get("default_category"),
        "category_path": record.get("category_path") or [],
    }


def chunk_records(records: Iterable[dict], size: int) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for record in records:
        batch.append(trim_record(record))
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def write_batch(batch_id: str, items: list[dict], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "batch_id": batch_id,
        "item_count": len(items),
        "items": items,
    }
    path = output_dir / f"{batch_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    batches_written: list[str] = []

    for idx, batch in enumerate(chunk_records(load_candidates(args.candidates_file, args.limit), args.batch_size), start=1):
        batch_id = f"ingredient_batch_{idx:04d}"
        write_batch(batch_id, batch, args.output_dir)
        batches_written.append(batch_id)

    manifest = {
        "source": str(args.candidates_file),
        "output_dir": str(args.output_dir),
        "batch_size": args.batch_size,
        "limit": args.limit,
        "total_batches": len(batches_written),
        "batch_ids": batches_written,
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    main()
