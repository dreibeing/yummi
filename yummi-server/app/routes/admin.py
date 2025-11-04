from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_principal
from ..config import get_settings
from ..redis_util import get_redis


router = APIRouter()


def _require_admin(principal: Dict[str, Any]):
    s = get_settings()
    email = principal.get("email")
    if s.admin_emails:
        if not email or email.lower() not in [e.lower() for e in s.admin_emails]:
            raise HTTPException(status_code=403, detail="Admin only")
    else:
        # If no admin list configured and not dev, block
        if s.environment not in ("dev", "development"):
            raise HTTPException(status_code=403, detail="Admin not configured")


@router.post("/admin/catalog/import")
def catalog_import(payload: Any, principal=Depends(get_current_principal)):
    _require_admin(principal)
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis not configured")
    data = payload
    if not isinstance(data, (list, dict)):
        raise HTTPException(status_code=400, detail="Expected list or object with items[]")
    try:
        # Validate minimal shape
        items = data if isinstance(data, list) else data.get("items", [])
        if not isinstance(items, list) or len(items) == 0:
            raise HTTPException(status_code=400, detail="No items to import")
        r.set("catalog:data", json.dumps(data))
        return {"ok": True, "count": len(items), "source": "redis"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")


@router.get("/admin/catalog/source")
def catalog_source(principal=Depends(get_current_principal)):
    _require_admin(principal)
    s = get_settings()
    r = get_redis()
    src = "file"
    count = None
    if r is not None:
        raw = r.get("catalog:data")
        if raw:
            try:
                data = json.loads(raw)
                items = data if isinstance(data, list) else data.get("items", [])
                src = "redis"
                count = len(items)
            except Exception:
                pass
    return {"source": src, "redis": bool(r is not None), "file": s.catalog_path, "count": count}

