"""Dramatiq broker on Redis. Imported by both the API (to enqueue) and the worker (to consume)."""
import dramatiq
import redis
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AgeLimit, Callbacks, Pipelines, Retries, ShutdownNotifications, TimeLimit

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

# Retries are handled by app.services.jobs (which owns job state), so Dramatiq's own retry is disabled.
# RESP2 is pinned explicitly: redis-py 8 defaults to RESP3 (HELLO 3), which older Redis servers reject.
broker = RedisBroker(
    client=redis.Redis.from_url(settings.redis_url, protocol=2),
    middleware=[AgeLimit(), TimeLimit(time_limit=30 * 60 * 1000), ShutdownNotifications(), Callbacks(), Pipelines(), Retries(max_retries=0)],
)
dramatiq.set_broker(broker)
