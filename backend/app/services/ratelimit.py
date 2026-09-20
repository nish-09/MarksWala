"""Small fixed-window rate limiter on Redis. Fails open (with a warning) if Redis is unreachable."""
from __future__ import annotations

import logging
from functools import lru_cache

import redis

from app.core.config import settings
from app.core.errors import RateLimited

log = logging.getLogger("MarksWala.ratelimit")


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2, decode_responses=True, protocol=2)


def hit(bucket: str, *, limit: int, window_seconds: int) -> None:
    """Count one attempt; raise RateLimited when over the limit."""
    key = f"rl:{bucket}"
    try:
        r = get_redis()
        n = r.incr(key)
        if n == 1:
            r.expire(key, window_seconds)
        if n > limit:
            ttl = r.ttl(key)
            raise RateLimited(
                f"Too many attempts. Try again in {max(ttl, 1)} seconds.",
                details={"retry_after_seconds": max(ttl, 1)},
            )
    except redis.RedisError as e:
        log.warning("rate limiter unavailable (%s); allowing request", e)


def reset(bucket: str) -> None:
    try:
        get_redis().delete(f"rl:{bucket}")
    except redis.RedisError:
        pass
