"""High-level orchestration for Woolworths product scraping."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Set
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from .client import FetchError, WoolworthsClient
from .parser import build_product_summary, get_total_records, iter_records

logger = logging.getLogger(__name__)


@dataclass
class CategoryConfig:
    name: str
    url: str
    path: List[str] = field(default_factory=list)
    enabled: bool = True


class WoolworthsScraper:
    """Scrape all configured categories and produce canonical product rows."""

    def __init__(
        self,
        client: WoolworthsClient,
    ) -> None:
        self.client = client
        self._global_seen: Set[str] = set()

    def scrape_category(self, category: CategoryConfig) -> Iterator[Dict[str, object]]:
        if not category.enabled:
            logger.info("Skipping disabled category %s", category.name)
            return

        offset = 0
        total = None
        seen: Set[str] = set()
        consecutive_empty = 0

        while True:
            page_url = _with_offset(category.url, offset)
            logger.debug("Fetching %s (offset=%s)", category.name, offset)
            try:
                state = self.client.fetch_initial_state(page_url)
            except FetchError as exc:
                logger.error(
                    "Failed to fetch %s at offset %s (%s); skipping remainder of category",
                    category.name,
                    offset,
                    exc,
                )
                break

            if total is None:
                total = get_total_records(state)
                if total is not None:
                    logger.debug("%s total records reported: %s", category.name, total)

            records = list(iter_records(state))
            if not records:
                consecutive_empty += 1
                if consecutive_empty >= 2:
                    logger.warning("No records for %s at offset %s; stopping", category.name, offset)
                    break
                offset += 24  # heuristic fallback
                continue

            consecutive_empty = 0

            for record in records:
                summary = build_product_summary(record, category_path=category.path)
                pid = summary.get("product_id")
                if not pid:
                    continue
                if pid in seen:
                    continue
                seen.add(pid)
                if pid not in self._global_seen:
                    self._global_seen.add(pid)
                yield summary

            offset += len(records)
            if total is not None and offset >= total:
                break

    def scrape(self, categories: Iterable[CategoryConfig]) -> Iterator[Dict[str, object]]:
        for category in categories:
            yield from self.scrape_category(category)


def _with_offset(url: str, offset: int) -> str:
    if offset <= 0:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    query["No"] = [str(offset)]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))
