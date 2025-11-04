from __future__ import annotations

import json
import os
import random
from typing import List

from fastapi import APIRouter, HTTPException, Query

from ..config import get_settings
from ..redis_util import get_redis
from ..schemas import Product


router = APIRouter()


def _load_catalog() -> List[Product]:
    s = get_settings()
    # 1) Try Redis (admin-imported dataset)
    r = get_redis()
    if r is not None:
        raw = r.get("catalog:data")
        if raw:
            try:
                data = json.loads(raw)
                items = data if isinstance(data, list) else data.get("items", [])
                return [
                    Product(
                        productId=it.get("productId"),
                        catalogRefId=it.get("catalogRefId"),
                        title=it.get("name") or it.get("title"),
                        url=it.get("url"),
                        qty=1,
                    )
                    for it in items
                ]
            except Exception:
                pass

    # 2) Fallback to file
    path = s.catalog_path or "resolver/catalog.json"
    if not os.path.exists(path):
        raise HTTPException(status_code=503, detail=f"Catalog file not found at {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Expect either list or object with 'items'
    items = data if isinstance(data, list) else data.get("items", [])
    products: List[Product] = []
    for it in items:
        products.append(Product(
            productId=it.get("productId"),
            catalogRefId=it.get("catalogRefId"),
            title=it.get("name") or it.get("title"),
            url=it.get("url"),
            qty=1,
        ))
    return products


@router.get("/catalog", response_model=List[Product])
def get_catalog(limit: int = Query(100, ge=1, le=1000), randomize: bool = Query(True)):
    products = _load_catalog()
    if randomize:
        random.shuffle(products)
    return products[:limit]
