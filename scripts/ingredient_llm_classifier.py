#!/usr/bin/env python3
"""Run GPT batches to classify Woolworths products into canonical ingredients or ready meals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from textwrap import dedent
from typing import Any, Iterable, List, Sequence

from llm_utils import OpenAIClientError, call_openai_api


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-manifest",
        type=Path,
        default=Path("data/ingredients/llm_batches/manifest.json"),
        help="Path to the batch manifest produced by ingredient_batch_builder.py",
    )
    parser.add_argument(
        "--batch-dir",
        type=Path,
        default=Path("data/ingredients/llm_batches"),
        help="Directory containing batch JSON files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/ingredients/llm_batches/responses"),
        help="Where to store model responses per batch",
    )
    parser.add_argument(
        "--model",
        default="gpt-5-nano-2025-08-07",
        help="OpenAI model identifier (default: gpt-5-nano-2025-08-07; pass --model gpt-5-mini-2025-08-07 for higher fidelity)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.1,
    )
    parser.add_argument(
        "--top-p",
        type=float,
        default=None,
        help="Optional top_p for supported models (leave unset for GPT-5 nano/mini responses API)"
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=1200,
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Optional clamp on how many batches to process",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-run even if a response file already exists for the batch",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_batch(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_system_prompt() -> str:
    return dedent(
        """
        You classify grocery SKUs for meal planning. Given minimal metadata, extract the simplest
        canonical item that a product represents (e.g., "spinach", "canned chickpeas", "Butter chicken ready meal").
        Flag whether the product is an ingredient (raw or semi-processed component) or a ready meal that is heated/eaten with minimal prep.
        Never group distinct concepts together and never invent new products.

        Always return STRICT JSON matching the requested schema. If data is insufficient, still make the best determination.
        """
    ).strip()


def build_user_prompt(items: List[dict[str, Any]]) -> str:
    instructions = dedent(
        """
        For EACH item below, output a JSON object matching this schema:
        {
          "product_id": "string",
          "core_item_name": "the simplest ingredient or ready meal name",
          "item_type": "ingredient" | "ready_meal"
        }

        Rules:
        - Treat single raw items, pantry staples, condiments, and cooking components as "ingredient".
        - Treat heat-and-eat dishes, prepared salads, frozen meals, and other ready-to-serve mains as "ready_meal".
        - If something is clearly NOT useful for cooking or meals (e.g., candles), skip it by emitting the closest applicable food category (e.g., "seasonal sweets"). This keeps downstream logic simple.
        - Use the provided normalized_name for guidance but simplify to the canonical term (e.g., "baby spinach leaves" -> "spinach").
        - Consider ready_meal_hint=true as a strong signal but override if the data contradicts it.

        Respond with a JSON array containing one object per product, in the same order. Keep responses terse—no prose beyond the field values.
        Items:
        """
    ).strip()

    lines = []
    for idx, item in enumerate(items, start=1):
        path_display = " > ".join(item.get("category_path") or [])
        line = {
            "idx": idx,
            "product_id": item.get("product_id"),
            "normalized_name": item.get("normalized_name"),
            "product_type": item.get("product_type"),
            "ready_meal_hint": item.get("ready_meal_hint"),
            "default_category": item.get("default_category"),
            "category_path": path_display,
        }
        lines.append(line)
    items_json = json.dumps(lines, ensure_ascii=False, indent=2)
    return f"{instructions}\n{items_json}"


def scrub_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
    return stripped.strip()


def parse_response(payload: str, expected_count: int) -> list[dict[str, Any]]:
    data = json.loads(scrub_json(payload))
    if not isinstance(data, list):
        raise ValueError("Model output must be a JSON array.")
    if len(data) != expected_count:
        raise ValueError(f"Expected {expected_count} entries, got {len(data)}.")
    normalized: list[dict[str, Any]] = []
    for entry in data:
        record = {
            "product_id": entry.get("product_id"),
            "core_item_name": entry.get("core_item_name"),
            "item_type": entry.get("item_type"),
        }
        missing = [k for k, v in record.items() if v in (None, "")]
        if missing:
            raise ValueError(f"Model output missing fields {missing} for product_id {entry.get('product_id')}.")
        if record["item_type"] not in {"ingredient", "ready_meal"}:
            raise ValueError(f"Invalid item_type '{record['item_type']}' for product_id {record['product_id']}.")
        normalized.append(record)
    return normalized


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_response(batch_id: str, results: list[dict[str, Any]], output_dir: Path, metadata: dict[str, Any]) -> Path:
    ensure_dir(output_dir)
    payload = {
        "batch_id": batch_id,
        "metadata": metadata,
        "results": results,
    }
    path = output_dir / f"{batch_id}.response.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def append_aggregate(batch_id: str, results: list[dict[str, Any]], aggregate_path: Path) -> None:
    ensure_dir(aggregate_path.parent)
    with aggregate_path.open("a", encoding="utf-8") as fh:
        for record in results:
            entry = dict(record)
            entry["batch_id"] = batch_id
            fh.write(json.dumps(entry, ensure_ascii=False))
            fh.write("\n")


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    manifest = load_manifest(args.batch_manifest)
    batch_ids: List[str] = manifest.get("batch_ids", [])
    args.output_dir.mkdir(parents=True, exist_ok=True)
    aggregate_path = args.output_dir / "all_results.jsonl"
    if args.overwrite and aggregate_path.exists():
        aggregate_path.unlink()

    processed = 0
    total_batches = len(batch_ids)
    for batch_index, batch_id in enumerate(batch_ids, start=1):
        if args.max_batches is not None and processed >= args.max_batches:
            break
        batch_path = args.batch_dir / f"{batch_id}.json"
        if not batch_path.exists():
            raise FileNotFoundError(f"Missing batch file: {batch_path}")
        response_path = args.output_dir / f"{batch_id}.response.json"
        if response_path.exists() and not args.overwrite:
            processed += 1
            continue

        batch = load_batch(batch_path)
        items = batch.get("items") or []
        for item_index, item in enumerate(items, start=1):
            print(f"[{batch_index}/{total_batches}] {batch_id} :: product {item_index}/{len(items)} -> {item.get('product_id')}")
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(items)

        text = call_openai_api(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=args.model,
            temperature=args.temperature,
            top_p=args.top_p,
            max_tokens=0,
            max_output_tokens=args.max_output_tokens,
            reasoning_effort=None,
        )
        results = parse_response(text, len(items))
        metadata = {
            "model": args.model,
            "temperature": args.temperature,
            "top_p": args.top_p,
            "max_output_tokens": args.max_output_tokens,
        }
        write_response(batch_id, results, args.output_dir, metadata)
        append_aggregate(batch_id, results, aggregate_path)
        processed += 1


if __name__ == "__main__":  # pragma: no cover
    try:
        main()
    except OpenAIClientError as exc:
        raise SystemExit(f"OpenAI error: {exc}")
