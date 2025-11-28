import json
import time
import uuid
from typing import Dict

from fastapi import APIRouter, Depends, Header, HTTPException, status, Request
import redis

from ..config import get_settings
from ..auth import get_current_principal
from ..schemas import CreateOrderRequest, CreateOrderResponse, OrderStatusResponse
from ..ratelimit import limiter
from ..services.recommendationlearning import (
    WOOLWORTHS_CART_TRIGGER,
    build_learning_context,
    schedule_recommendation_learning_run,
)


router = APIRouter()


def _redis() -> redis.Redis | None:
    url = get_settings().redis_url
    if not url:
        return None
    return redis.from_url(url, decode_responses=True)


_mem_orders: Dict[str, Dict] = {}
_mem_idem: Dict[str, Dict] = {}


def _idem_get_set(key: str, payload: Dict | None = None, ttl: int = 86400) -> Dict | None:
    r = _redis()
    if r is None:
        if payload is None:
            return _mem_idem.get(key)
        _mem_idem[key] = payload
        return payload
    if payload is None:
        raw = r.get(f"idem:{key}")
        return json.loads(raw) if raw else None
    r.setex(f"idem:{key}", ttl, json.dumps(payload))
    return payload


def _order_put(order: Dict) -> None:
    r = _redis()
    oid = order["order_id"]
    if r is None:
        _mem_orders[oid] = order
        return
    r.hset(f"order:{oid}", mapping={"data": json.dumps(order)})


def _order_get(oid: str) -> Dict | None:
    r = _redis()
    if r is None:
        return _mem_orders.get(oid)
    raw = r.hget(f"order:{oid}", "data")
    return json.loads(raw) if raw else None


@router.post("/orders", response_model=CreateOrderResponse)
@limiter.limit("60/minute")
async def create_order(
    request: Request,
    body: CreateOrderRequest,
    principal=Depends(get_current_principal),
    idempotency_key: str | None = Header(default=None, convert_underscores=False, alias="Idempotency-Key"),
):
    # Idempotency handling
    if idempotency_key:
        cached = _idem_get_set(idempotency_key)
        if cached:
            return CreateOrderResponse(**cached)

    oid = str(uuid.uuid4())
    order = {
        "order_id": oid,
        "user_id": principal.get("sub"),
        "retailer": body.retailer,
        "items": [i.model_dump() for i in body.items],
        "status": "queued",
        "created_at": int(time.time()),
        "events": [],
        "notes": body.notes,
    }
    _order_put(order)
    resp = CreateOrderResponse(order_id=oid, status="queued")
    if idempotency_key:
        _idem_get_set(idempotency_key, resp.model_dump())
    schedule_recommendation_learning_run(
        user_id=principal.get("sub"),
        trigger=WOOLWORTHS_CART_TRIGGER,
        event_context=build_learning_context(
            request_payload=body,
            response_payload={"order": order, "response": resp},
            metadata={
                "orderId": oid,
                "retailer": body.retailer,
                "source": "orders.create",
            },
        ),
    )
    return resp


@router.get("/orders/{order_id}", response_model=OrderStatusResponse)
@limiter.limit("120/minute")
def get_order(request: Request, order_id: str, principal=Depends(get_current_principal)):
    order = _order_get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.get("user_id") != principal.get("sub"):
        raise HTTPException(status_code=403, detail="Forbidden")
    return OrderStatusResponse(
        order_id=order_id,
        status=order.get("status"),
        items=order.get("items"),
        retailer=order.get("retailer"),
        events=order.get("events", []),
    )


@router.post("/orders/{order_id}/ack")
@limiter.limit("120/minute")
def ack_order(request: Request, order_id: str, principal=Depends(get_current_principal)):
    order = _order_get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.get("user_id") != principal.get("sub"):
        raise HTTPException(status_code=403, detail="Forbidden")
    order["status"] = "completed"
    order.setdefault("events", []).append({"type": "ack", "ts": int(time.time())})
    _order_put(order)
    return {"ok": True}
