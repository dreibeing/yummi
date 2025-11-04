"""Category discovery for Woolworths Food navigation."""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional, Set
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .client import FetchError, WoolworthsClient
from .parser import absolute_url, extract_breadcrumb_labels, extract_nav_urls
from .scraper import CategoryConfig

DEFAULT_ROOT = "https://www.woolworths.co.za/dept/Food/_/N-1z13sk5?No=0"


def discover_food_categories(
    client: WoolworthsClient,
    *,
    root_url: str = DEFAULT_ROOT,
) -> List[CategoryConfig]:
    """Return discovered Food category configs starting from the root navigation."""

    queue: deque[str] = deque([_ensure_offset(root_url, 0)])
    visited: Set[str] = set()
    categories: Dict[str, Dict[str, object]] = {}

    while queue:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        try:
            state = client.fetch_initial_state(url)
        except FetchError:
            continue

        for nav in extract_nav_urls(state):
            full = absolute_url(nav)
            if not full:
                continue
            full = full.strip()
            base = _normalize_category_url(full)
            if not base or not _is_food_category(base):
                continue
            categories.setdefault(base, {})
            next_url = _ensure_offset(base, 0)
            if next_url not in visited:
                queue.append(next_url)

        base_current = _normalize_category_url(url)
        if base_current and _is_food_category(base_current):
            path = extract_breadcrumb_labels(state)
            if path:
                entry = categories.setdefault(base_current, {})
                entry["path"] = path
                entry.setdefault("name", path[-1])

    configs: List[CategoryConfig] = []
    for base in sorted(categories.keys()):
        meta = categories[base]
        path = meta.get("path") or []
        if isinstance(path, list):
            clean_path = [str(label) for label in path]
        else:
            clean_path = []
        name = meta.get("name")
        if not isinstance(name, str) or not name:
            name = clean_path[-1] if clean_path else _fallback_name(base)
        configs.append(CategoryConfig(name=name, url=base, path=clean_path))

    return configs


def _normalize_category_url(url: str) -> str:
    full = absolute_url(url)
    if not full:
        return ""
    full = full.strip()
    parsed = urlparse(full)
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _is_food_category(url: str) -> bool:
    parsed = urlparse(url)
    if not parsed.path.startswith("/cat/Food/"):
        return False
    stem = parsed.path.split("/_/")[0]
    return stem.count("/") >= 3


def _ensure_offset(url: str, offset: int) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["No"] = [str(offset)]
    new_query = urlencode(qs, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


def _fallback_name(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if "/_/" in path:
        stem = path.split("/_/")[0]
    else:
        stem = path
    segment = stem.split("/")[-1] if stem else path
    segment = segment.replace("-", " ").strip()
    return segment.title() if segment else url
