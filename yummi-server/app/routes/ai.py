from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from openai import OpenAI

from ..config import get_settings
from ..auth import get_current_principal


router = APIRouter()
from ..ratelimit import limiter


@router.post("/ai/complete")
@limiter.limit("20/minute")
def ai_complete(
    request: Request,
    payload: Dict[str, Any],
    principal=Depends(get_current_principal),
    idempotency_key: Optional[str] = Header(default=None, convert_underscores=False, alias="Idempotency-Key"),
):
    s = get_settings()
    if not s.openai_api_key:
        raise HTTPException(status_code=503, detail="OpenAI not configured")
    model = payload.get("model") or s.openai_default_model
    if model not in s.openai_allowed_models:
        raise HTTPException(status_code=400, detail="Model not allowed")

    client = OpenAI(api_key=s.openai_api_key)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages[] required")

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=payload.get("temperature", 0.2),
            max_tokens=payload.get("max_tokens", 512),
        )
        return resp.model_dump()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OpenAI error: {e}")
