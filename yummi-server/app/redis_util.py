from __future__ import annotations

import redis
from .config import get_settings


def get_redis() -> redis.Redis | None:
    url = get_settings().redis_url
    if not url:
        return None
    return redis.from_url(url, decode_responses=True)

