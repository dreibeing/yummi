"""Command-line interface for the Woolworths product scraper."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Iterable, List

from .client import ClientConfig, WoolworthsClient
from .discover import DEFAULT_ROOT, discover_food_categories
from .scraper import CategoryConfig, WoolworthsScraper
from .writer import write_catalog, write_csv, write_jsonl


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Woolworths product tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Discover Food category URLs")
    discover.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        help="Root URL to begin discovery (default: Food department)",
    )
    discover.add_argument(
        "--output",
        type=Path,
        default=Path("woolworths_scraper/config/categories.food.json"),
        help="Path to write discovered categories JSON",
    )
    discover.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    discover.set_defaults(func=run_discover)

    scrape = subparsers.add_parser("scrape", help="Scrape product catalog")
    group = scrape.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--categories",
        type=Path,
        help="Path to categories JSON file",
    )
    group.add_argument(
        "--auto-food",
        action="store_true",
        help="Automatically discover Food categories before scraping",
    )
    scrape.add_argument(
        "--root",
        default=DEFAULT_ROOT,
        help="Root URL for auto discovery when --auto-food is used",
    )
    scrape.add_argument(
        "--output-json",
        type=Path,
        default=Path("data/product_table_folder/woolworths_products_raw.jsonl"),
        help="Path to write JSONL output",
    )
    scrape.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/product_table_folder/woolworths_products_summary.csv"),
        help="Path to write CSV summary",
    )
    scrape.add_argument(
        "--catalog-output",
        type=Path,
        default=Path("resolver/catalog.json"),
        help="Path to write resolver catalog JSON",
    )
    scrape.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of products scraped (debug)",
    )
    scrape.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    scrape.set_defaults(func=run_scrape)

    return parser


def load_categories(path: Path) -> List[CategoryConfig]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    categories: List[CategoryConfig] = []
    for entry in payload:
        categories.append(
            CategoryConfig(
                name=entry["name"],
                url=entry["url"],
                path=entry.get("path", []),
                enabled=entry.get("enabled", True),
            )
        )
    return categories


def run_discover(args: argparse.Namespace) -> None:
    logging.basicConfig(level=getattr(logging, args.log_level))
    client = WoolworthsClient(config=ClientConfig())
    try:
        categories = discover_food_categories(client, root_url=args.root)
    finally:
        client.close()

    logging.info("Discovered %s categories", len(categories))
    payload = [
        {
            "name": cfg.name,
            "url": cfg.url,
            "path": cfg.path,
            "enabled": cfg.enabled,
        }
        for cfg in categories
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote categories -> %s", args.output)


def run_scrape(args: argparse.Namespace) -> None:
    logging.basicConfig(level=getattr(logging, args.log_level))
    config = ClientConfig()
    client = WoolworthsClient(config=config)

    try:
        if getattr(args, "auto_food", False):
            categories = discover_food_categories(client, root_url=args.root)
            logging.info("Auto-discovered %s Food categories", len(categories))
        else:
            categories = load_categories(args.categories)
            logging.info("Loaded %s categories from %s", len(categories), args.categories)

        scraper = WoolworthsScraper(client)

        products = []
        for product in scraper.scrape(categories):
            products.append(product)
            if args.limit and len(products) >= args.limit:
                break
    finally:
        client.close()

    logging.info("Collected %s products", len(products))

    if not products:
        logging.warning("No products scraped; skipping write")
        return

    write_jsonl(products, args.output_json)
    logging.info("Wrote JSONL -> %s", args.output_json)

    csv_fields = [
        "product_id",
        "catalog_ref_id",
        "sku",
        "name",
        "brand",
        "sale_price",
        "detail_url",
        "default_category",
        "department",
        "path",
    ]
    write_csv(products, args.output_csv, fieldnames=csv_fields)
    logging.info("Wrote CSV -> %s", args.output_csv)

    write_catalog(products, args.catalog_output)
    logging.info("Wrote catalog -> %s", args.catalog_output)


def main(argv: Iterable[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    args.func(args)


if __name__ == "__main__":  # pragma: no cover
    main()
