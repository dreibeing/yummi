from __future__ import annotations

import json
import os
import random
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, ConfigDict

from ..config import get_settings
from ..redis_util import get_redis


router = APIRouter()


class ThinProduct(BaseModel):
    key: str
    title: Optional[str] = None
    productId: Optional[str] = None
    catalogRefId: Optional[str] = None
    sku: Optional[str] = None
    qty: int = Field(default=1, ge=1)
    url: Optional[str] = None
    detailUrl: Optional[str] = None
    price: Optional[float] = None
    imageUrl: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ThinCatalogResponse(BaseModel):
    count: int
    seed: Optional[str] = None
    items: List[ThinProduct]


class ThinOrderPlaceRequest(BaseModel):
    items: List[Dict[str, Any]] = Field(min_length=1)
    metadata: Dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class ThinOrderPlaceResponse(BaseModel):
    status: str
    orderId: str
    redirectUrl: str
    message: str
    receivedItems: int


class ThinOrderResponse(BaseModel):
    id: str
    status: str
    items: List[Dict[str, Any]]
    metadata: Dict[str, Any] | None = None
    createdAt: str
    claimedAt: Optional[str] = None
    claimedBy: Optional[str] = None
    completedAt: Optional[str] = None
    result: Dict[str, Any] | None = None
    source: Optional[str] = None


class ThinOrderAckRequest(BaseModel):
    status: Optional[str] = Field(default=None)
    error: Optional[str] = None
    processedItems: Any = None

    model_config = ConfigDict(extra="allow")


class ThinLogLine(BaseModel):
    line: str


THIN_PENDING_KEY = "thin:orders:pending"
THIN_ORDER_KEY_PREFIX = "thin:order:"

_catalog_lock = threading.Lock()
_catalog_cache: List[ThinProduct] | None = None
_catalog_mtime: float = 0.0

_mem_lock = threading.Lock()
_mem_orders: Dict[str, Dict[str, Any]] = {}
_mem_pending: List[str] = []


def _catalog_path() -> str:
    settings = get_settings()
    return settings.catalog_path or "resolver/catalog.json"


def _load_catalog() -> List[ThinProduct]:
    path = _catalog_path()
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"Catalog missing at {path}")

    with _catalog_lock:
        global _catalog_cache, _catalog_mtime
        if _catalog_cache is not None and stat.st_mtime <= _catalog_mtime:
            # Return a shallow copy to avoid accidental mutations
            return list(_catalog_cache)

        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        items: List[ThinProduct] = []
        if isinstance(raw, dict):
            for key, value in raw.items():
                if not isinstance(value, dict):
                    continue
                product_id = value.get("productId")
                catalog_ref = value.get("catalogRefId")
                detail_url = (
                    value.get("detailUrl")
                    or value.get("detailURL")
                    or value.get("url")
                    or value.get("productUrl")
                    or value.get("link")
                    or value.get("href")
                )
                if not detail_url:
                    if product_id:
                        detail_url = f"https://www.woolworths.co.za/prod/_/A-{product_id}"
                    elif catalog_ref:
                        detail_url = f"https://www.woolworths.co.za/prod/_/A-{catalog_ref}"
                product = ThinProduct(
                    key=str(key),
                    title=value.get("name") or str(key),
                    productId=str(product_id) if product_id is not None else None,
                    catalogRefId=str(catalog_ref) if catalog_ref is not None else None,
                    sku=str(value.get("sku")) if value.get("sku") is not None else None,
                    qty=max(1, int(value.get("qty", 1))) if isinstance(value.get("qty"), (int, float)) else 1,
                    url=detail_url,
                    detailUrl=detail_url,
                    price=value.get("price") or value.get("salePrice") or value.get("pricePerUnit"),
                    imageUrl=value.get("image") or value.get("imageUrl") or value.get("thumbnail") or value.get("primaryImage"),
                    metadata=value,
                )
                items.append(product)
        elif isinstance(raw, list):
            for idx, value in enumerate(raw):
                if not isinstance(value, dict):
                    continue
                key = value.get("key") or value.get("title") or f"item_{idx}"
                product = ThinProduct(
                    key=str(key),
                    title=value.get("title") or value.get("name") or str(key),
                    productId=str(value.get("productId")) if value.get("productId") is not None else None,
                    catalogRefId=str(value.get("catalogRefId")) if value.get("catalogRefId") is not None else None,
                    sku=str(value.get("sku")) if value.get("sku") is not None else None,
                    qty=int(value.get("qty")) if isinstance(value.get("qty"), (int, float)) else 1,
                    url=value.get("url") or value.get("detailUrl"),
                    detailUrl=value.get("detailUrl") or value.get("url"),
                    price=value.get("price") or value.get("salePrice"),
                    imageUrl=value.get("imageUrl") or value.get("image"),
                    metadata=value,
                )
                items.append(product)
        else:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unsupported catalog format")

        _catalog_cache = items
        _catalog_mtime = stat.st_mtime
        return list(items)


def _require_enabled() -> None:
    if not get_settings().thin_slice_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Thin slice API disabled")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store_order(order: Dict[str, Any]) -> None:
    redis_client = get_redis()
    data = json.dumps(order)
    if redis_client is not None:
        redis_client.hset(f"{THIN_ORDER_KEY_PREFIX}{order['id']}", mapping={"data": data})
    else:
        with _mem_lock:
            _mem_orders[order["id"]] = order


def _queue_order(order_id: str) -> None:
    redis_client = get_redis()
    if redis_client is not None:
        redis_client.rpush(THIN_PENDING_KEY, order_id)
    else:
        with _mem_lock:
            _mem_pending.append(order_id)


def _next_pending() -> Optional[str]:
    redis_client = get_redis()
    if redis_client is not None:
        return redis_client.lpop(THIN_PENDING_KEY)
    with _mem_lock:
        if _mem_pending:
            return _mem_pending.pop(0)
    return None


def _get_order(order_id: str) -> Optional[Dict[str, Any]]:
    redis_client = get_redis()
    if redis_client is not None:
        raw = redis_client.hget(f"{THIN_ORDER_KEY_PREFIX}{order_id}", "data")
        return json.loads(raw) if raw else None
    with _mem_lock:
        order = _mem_orders.get(order_id)
        return json.loads(json.dumps(order)) if order is not None else None


def _count_pending() -> int:
    redis_client = get_redis()
    if redis_client is not None:
        return int(redis_client.llen(THIN_PENDING_KEY))
    with _mem_lock:
        return len(_mem_pending)


def _shuffle_items(items: List[ThinProduct], count: int, seed: Optional[str]) -> List[ThinProduct]:
    pool = list(items)
    if seed:
        rng = random.Random(seed)
        rng.shuffle(pool)
    else:
        random.shuffle(pool)
    return pool[:count]


def _log_path() -> str:
    return get_settings().thin_runner_log_path


def _ensure_log_dir() -> None:
    path = _log_path()
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)


@router.get("/health")
def thin_health() -> Dict[str, Any]:
    _require_enabled()
    catalog_items = _load_catalog()
    return {
        "ok": True,
        "catalogSize": len(catalog_items),
        "pendingOrders": _count_pending(),
    }


@router.get("/products/random", response_model=ThinCatalogResponse)
def thin_products_random(count: int = Query(100, ge=1, le=250), seed: Optional[str] = Query(None)) -> ThinCatalogResponse:
    _require_enabled()
    catalog_items = _load_catalog()
    if not catalog_items:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Catalog unavailable")
    selection = _shuffle_items(catalog_items, count, seed)
    return ThinCatalogResponse(count=len(selection), seed=seed, items=selection)


@router.post("/orders/place", response_model=ThinOrderPlaceResponse)
def thin_orders_place(body: ThinOrderPlaceRequest) -> ThinOrderPlaceResponse:
    _require_enabled()
    received_items = len(body.items)
    order_id = str(uuid.uuid4())
    now = _now_iso()
    metadata = body.metadata or {}
    order = {
        "id": order_id,
        "status": "pending",
        "createdAt": now,
        "source": metadata.get("source") or "thin-slice-app",
        "items": body.items,
        "metadata": metadata,
        "result": None,
    }
    _store_order(order)
    _queue_order(order_id)
    return ThinOrderPlaceResponse(
        status="queued",
        orderId=order_id,
        redirectUrl="https://www.woolworths.co.za/login",
        message="Order hand-off queued. Log into Woolworths to continue.",
        receivedItems=received_items,
    )


@router.get("/orders/next", response_model=ThinOrderResponse, status_code=status.HTTP_200_OK)
def thin_orders_next(workerId: Optional[str] = Query(default=None)) -> Response | ThinOrderResponse:
    _require_enabled()
    attempt = 0
    while attempt < 3:
        attempt += 1
        order_id = _next_pending()
        if not order_id:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        order = _get_order(order_id)
        if not order:
            continue
        order["status"] = "claimed"
        order["claimedAt"] = _now_iso()
        order["claimedBy"] = workerId
        _store_order(order)
        return ThinOrderResponse(**order)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/orders/{order_id}/ack")
def thin_orders_ack(order_id: str, body: ThinOrderAckRequest) -> Dict[str, Any]:
    _require_enabled()
    order = _get_order(order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order_not_found")
    status_value = body.status or "completed"
    if status_value not in {"completed", "failed"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_status")
    order["status"] = status_value
    order["completedAt"] = _now_iso()
    order["result"] = {
        "processedItems": body.processedItems,
        "error": body.error,
    }
    _store_order(order)
    return {"ok": True}


@router.get("/orders/{order_id}", response_model=ThinOrderResponse)
def thin_orders_get(order_id: str) -> ThinOrderResponse:
    _require_enabled()
    order = _get_order(order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order_not_found")
    return ThinOrderResponse(**order)


@router.post("/logs/runner/reset")
def thin_logs_reset() -> Dict[str, Any]:
    _require_enabled()
    _ensure_log_dir()
    path = _log_path()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("")
    return {"ok": True, "path": path}


@router.post("/logs/runner/append")
def thin_logs_append(payload: ThinLogLine) -> Dict[str, Any]:
    _require_enabled()
    message = payload.line.strip()
    if not message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_line")
    _ensure_log_dir()
    path = _log_path()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"{message}\n")
    return {"ok": True}
