#!/usr/bin/env python3
"""Run the archetype generation prompt (single shot or batches) and persist output."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from typing import Any, Dict, List, Optional

from llm_utils import call_openai_api, OpenAIClientError


DEFAULT_PROMPT_PATH = Path("data/prompts/archetype_generation_prompt.md")
DEFAULT_CONSTRAINT_PATH = Path("data/tags/archetype_constraint_brief.md")
DEFAULT_TAGS_PATH = Path("data/tags/defined_tags.json")
DEFAULT_OUTPUT_DIR = Path("data/archetypes")
SNAPSHOT_FILENAME = "archetypes_so_far.json"


@dataclass
class PromptTemplate:
    system: str
    user: str


class PromptTemplateError(RuntimeError):
    pass


def _load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_file(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return path.read_text(encoding="utf-8")


def _extract_block(lines: List[str], header: str) -> str:
    header_lower = header.lower().strip()
    try:
        start_idx = next(i for i, line in enumerate(lines) if line.strip().lower() == header_lower)
    except StopIteration as exc:  # pragma: no cover - defensive
        raise PromptTemplateError(f"Could not find section '{header}'.") from exc

    idx = start_idx + 1
    while idx < len(lines) and not lines[idx].strip().startswith("```"):
        idx += 1
    if idx >= len(lines):
        raise PromptTemplateError(f"Missing code fence for '{header}'.")

    fence = lines[idx].strip()
    idx += 1
    block: List[str] = []
    while idx < len(lines) and lines[idx].strip() != fence:
        block.append(lines[idx])
        idx += 1
    if idx >= len(lines):
        raise PromptTemplateError(f"Unterminated code fence in '{header}'.")
    return "".join(block).strip()


def load_prompt_template(path: Path) -> PromptTemplate:
    text = _read_file(path)
    lines = text.splitlines(keepends=True)
    system = _extract_block(lines, "## System Message")
    user = _extract_block(lines, "## User Message")
    return PromptTemplate(system=system, user=user)


def scrub_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def parse_response_payload(payload_text: str) -> Dict[str, Any]:
    body = scrub_json_text(payload_text)
    data = json.loads(body)
    if "archetypes" not in data or not isinstance(data["archetypes"], list):
        raise ValueError("Response missing 'archetypes' list.")
    return data


def _fill_placeholders(template: str, values: Dict[str, Any]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered


def render_user_prompt(
    template: str,
    *,
    market_brief: str,
    tags_version: str,
    required_categories: List[str],
    archetype_count: int,
) -> str:
    replacements = {
        "market_coverage_brief": market_brief.strip(),
        "tags_version": tags_version,
        "required_categories_archetype": ", ".join(required_categories),
        "archetype_count": archetype_count,
    }
    return _fill_placeholders(template, replacements)


def render_system_prompt(template: str, *, archetype_count: int) -> str:
    return _fill_placeholders(template, {"archetype_count": archetype_count})


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_output_dir(base_dir: Path) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / f"run_{timestamp_slug()}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def save_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_constraint_brief(path: Path) -> str:
    return dedent(_read_file(path)).strip()


def load_archetype_snapshot(run_dir: Path) -> List[Dict[str, Any]]:
    snapshot_path = run_dir / SNAPSHOT_FILENAME
    if not snapshot_path.exists():
        return []
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    archetypes = data.get("archetypes")
    return archetypes if isinstance(archetypes, list) else []


def write_archetype_snapshot(run_dir: Path, archetypes: List[Dict[str, Any]], tags_version: str) -> None:
    snapshot_path = run_dir / SNAPSHOT_FILENAME
    payload = {"tags_version": tags_version, "archetypes": archetypes}
    save_json(snapshot_path, payload)


def summarize_archetypes(archetypes: List[Dict[str, Any]], max_items: int) -> str:
    """Return a compact summary (2 lines per item) using only keywords.

    We summarize the last N archetypes to keep context current and short.
    Line 1: <n>. <name>
    Line 2: D=<..>; C=<..>; A=<..>; B=<..>; H=<..>; P=<..>; X=<..>; E=<..>
    (Diet, Cuisine, Audience, Budget, Heat, Prep, Complexity, Ethics)
    """
    lines: List[str] = []
    window = archetypes[-max_items:] if max_items > 0 else []
    for idx, archetype in enumerate(window, start=1):
        name = archetype.get("name", "Unknown")
        tags = archetype.get("core_tags") or {}
        diet = ",".join(tags.get("Diet", [])[:2]) or "-"
        cuisine = ",".join(tags.get("Cuisine", [])[:2]) or "-"
        audience = ",".join(tags.get("Audience", [])[:1]) or "-"
        budget = ",".join(tags.get("BudgetLevel", [])[:1]) or "-"
        heat = ",".join(tags.get("HeatSpice", [])[:1]) or archetype.get("heat_band", "-")
        prep = ",".join(tags.get("PrepTime", [])[:1]) or "-"
        complexity = ",".join(tags.get("Complexity", [])[:1]) or archetype.get("complexity", "-")
        ethics = ",".join(tags.get("EthicsReligious", [])[:1]) or "-"
        lines.append(f"{idx}. {name}")
        lines.append(
            f"D={diet}; C={cuisine}; A={audience}; B={budget}; H={heat}; P={prep}; X={complexity}; E={ethics}"
        )
    return "\n".join(lines) if lines else ""


def run_batches(args: argparse.Namespace) -> None:
    tags_manifest = _load_json(Path(args.tags_manifest))
    tags_version: str = tags_manifest.get("tags_version", "unknown")
    required_categories = tags_manifest.get("required_categories", {}).get("archetype", [])
    if not required_categories:
        raise RuntimeError("tags manifest missing required_categories.archetype entries.")

    prompt = load_prompt_template(Path(args.prompt_template))
    market_brief = load_constraint_brief(Path(args.constraint_brief))

    batch_size = args.batch_size or args.archetype_count
    total = args.archetype_count
    batches: List[int] = []
    remaining = total
    while remaining > 0:
        size = min(batch_size, remaining)
        batches.append(size)
        remaining -= size

    output_dir = ensure_output_dir(Path(args.output_dir))
    # Support resumable context by loading any previously written snapshot.
    all_archetypes: List[Dict[str, Any]] = load_archetype_snapshot(output_dir)
    metadata = {
        "model": args.model,
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "max_output_tokens": args.max_output_tokens,
        "reasoning_effort": args.reasoning_effort,
        "tags_version": tags_version,
        "prompt_template": str(Path(args.prompt_template)),
        "constraint_brief": str(Path(args.constraint_brief)),
        "batches": [],
        "total_requested": total,
    }

    for idx, batch_count in enumerate(batches, start=1):
        system_prompt = render_system_prompt(prompt.system, archetype_count=batch_count)
        user_prompt = render_user_prompt(
            prompt.user,
            market_brief=market_brief,
            tags_version=tags_version,
            required_categories=required_categories,
            archetype_count=batch_count,
        )

        context_block = ""
        prior_archetypes = load_archetype_snapshot(output_dir) or all_archetypes
        if prior_archetypes:
            summary_text = summarize_archetypes(prior_archetypes, args.context_summary_max)
            context_block = (
                "\n\nPrior Archetypes (do-not-repeat; keywords only):\n"
                f"{summary_text}\n\n"
                "Use the list above strictly as an exclusion guide. Do not generate archetypes with materially similar "
                "Diet×Cuisine×Audience×Budget×Heat×Prep×Complexity×Ethics combinations. Prefer broad, mainstream family archetypes "
                "(Omnivore/Vegetarian, Balanced/Affordable, Mild, 15–30, Simple, SA/ModernAmerican) unless the brief explicitly "
                "asks for specialty cohorts."
            )

        user_prompt_with_context = user_prompt + context_block

        print(f"Preparing batch {idx}/{len(batches)} (target {batch_count} archetypes)...")
        if args.dry_run:
            preview_path = output_dir / f"batch_{idx:02d}_prompt.txt"
            preview_path.write_text(
                f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt_with_context}\n",
                encoding="utf-8",
            )
            print(f"[dry-run] Wrote prompt preview to {preview_path}")
            continue

        raw_text = call_openai_api(
            system_prompt=system_prompt,
            user_prompt=user_prompt_with_context,
            model=args.model,
            temperature=args.temperature,
            top_p=args.top_p,
            max_tokens=args.max_tokens,
            reasoning_effort=args.reasoning_effort,
            max_output_tokens=args.max_output_tokens,
        )
        raw_file = output_dir / f"batch_{idx:02d}_raw.txt"
        raw_file.write_text(raw_text, encoding="utf-8")

        parsed = parse_response_payload(raw_text)
        archetypes = parsed.get("archetypes", [])
        all_archetypes.extend(archetypes)
        write_archetype_snapshot(output_dir, all_archetypes, tags_version)
        metadata["batches"].append(
            {
                "batch": idx,
                "requested_count": batch_count,
                "received_count": len(archetypes),
                "raw_file": raw_file.name,
            }
        )
        print(f"Batch {idx}: requested {batch_count}, received {len(archetypes)} archetypes")

    if args.dry_run:
        print(f"Dry run complete. Prompts saved under {output_dir}")
        return

    output_payload = {"archetypes": all_archetypes, "tags_version": tags_version}
    aggregated_file = output_dir / "archetypes_aggregated.json"
    save_json(aggregated_file, output_payload)
    save_json(output_dir / "run_metadata.json", metadata)

    print(f"Saved {len(all_archetypes)} archetypes to {aggregated_file}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM runner for archetype generation prompt.")
    parser.add_argument("--prompt-template", default=str(DEFAULT_PROMPT_PATH), help="Path to prompt template markdown.")
    parser.add_argument("--constraint-brief", default=str(DEFAULT_CONSTRAINT_PATH), help="Path to coverage brief injected into the prompt.")
    parser.add_argument("--tags-manifest", default=str(DEFAULT_TAGS_PATH), help="Path to defined_tags manifest.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for run artifacts.")
    parser.add_argument("--archetype-count", type=int, default=24, help="Total number of archetypes to request across batches.")
    parser.add_argument("--batch-size", type=int, default=None, help="Optional batch size (defaults to total, meaning single call).")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), help="Chat/response model to call.")
    parser.add_argument("--temperature", type=float, default=0.4, help="Sampling temperature for the LLM call.")
    parser.add_argument("--top-p", type=float, default=1.0, help="Nucleus sampling parameter (top_p).")
    parser.add_argument("--max-tokens", type=int, default=2000, help="max_tokens for chat completion responses.")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4096,
        help="max_output_tokens for reasoning models such as GPT-5.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default="low",
        help="Reasoning effort hint for GPT-5 models (default: low to reduce token usage).",
    )
    parser.add_argument(
        "--context-summary-max",
        type=int,
        default=10,
        help="Maximum number of existing archetypes to include in the context summary for each batch.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip API calls and just materialize the rendered prompts.")
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_batches(args)
    except OpenAIClientError as exc:  # pragma: no cover - CLI surface
        parser.error(str(exc))
    except Exception as exc:  # pragma: no cover
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
