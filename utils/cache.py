import os
import json
import time
import logging
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

redis_client: aioredis.Redis | None = None
REDIS_URL = os.getenv("REDIS_URL", "")

async def init_redis():
    global redis_client
    try:
        redis_client = aioredis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        await redis_client.ping()
        logger.info("Redis connected successfully")
    except Exception as e:
        logger.error(f"Redis connection failed: {e}", exc_info=True)
        redis_client = None

async def close_redis():
    global redis_client
    if redis_client:
        await redis_client.close()

async def cache_get(key: str) -> dict | list | None:
    if not redis_client:
        return None
    try:
        val = await redis_client.get(key)
        if val:
            logger.info(f"Cache hit: {key}")
            return json.loads(val)
        logger.info(f"Cache miss: {key} - fetching from Supabase")
        return None
    except Exception as e:
        logger.error(f"Cache get error: {e}", exc_info=True)
        return None

async def cache_set(key: str, value: dict | list, ttl: int = 900):
    if not redis_client:
        return
    try:
        await redis_client.setex(key, ttl, json.dumps(value))
    except Exception as e:
        logger.error(f"Cache set error: {e}", exc_info=True)

async def cache_delete(key: str):
    if not redis_client:
        return
    try:
        await redis_client.delete(key)
        logger.info(f"Cache cleared: {key}")
    except Exception as e:
        logger.error(f"Cache delete error: {e}", exc_info=True)

async def cache_clear_pattern(pattern: str):
    if not redis_client:
        return
    try:
        keys = await redis_client.keys(pattern)
        if keys:
            await redis_client.delete(*keys)
        logger.info(f"Cache cleared pattern: {pattern} - {len(keys)} keys")
    except Exception as e:
        logger.error(f"Cache clear pattern error: {e}", exc_info=True)


# ─── In-memory cache layer (Layer 1) ───────────────────────────────────────
# Sits on top of Redis (Layer 2) which sits on top of Supabase (Layer 3).
# Request → in-memory dict (0ms) → miss → Redis (1-2ms) → miss → DB (100-400ms)

_mem_cache: dict = {}


def mem_get(key: str):
    """Get from in-memory cache. Returns None if missing or expired."""
    try:
        if key in _mem_cache:
            value, expiry = _mem_cache[key]
            if time.time() < expiry:
                logger.info(f"Memory cache hit: {key}")
                return value
            del _mem_cache[key]
        return None
    except Exception as e:
        logger.error(f"Memory cache get error: {e}", exc_info=True)
        return None


def mem_set(key: str, value, ttl: int = 60):
    """Set in in-memory cache with TTL in seconds."""
    try:
        _mem_cache[key] = (value, time.time() + ttl)
    except Exception as e:
        logger.error(f"Memory cache set error: {e}", exc_info=True)


def mem_delete(key: str):
    """Delete single key from in-memory cache."""
    try:
        _mem_cache.pop(key, None)
    except Exception as e:
        logger.error(f"Memory cache delete error: {e}", exc_info=True)


def mem_clear_pattern(pattern: str):
    """Clear all in-memory keys matching pattern prefix."""
    try:
        prefix = pattern.rstrip('*')
        keys_to_delete = [k for k in _mem_cache if k.startswith(prefix)]
        for k in keys_to_delete:
            del _mem_cache[k]
        logger.info(f"Memory cache cleared pattern: {pattern} - {len(keys_to_delete)} keys")
    except Exception as e:
        logger.error(f"Memory cache clear pattern error: {e}", exc_info=True)


def mem_clear_all():
    """Clear entire in-memory cache."""
    try:
        _mem_cache.clear()
    except Exception as e:
        logger.error(f"Memory cache clear all error: {e}", exc_info=True)


async def two_layer_get(key: str):
    """Check in-memory cache first, then Redis. Warms memory on a Redis hit."""
    try:
        val = mem_get(key)
        if val is not None:
            return val
    except Exception as e:
        logger.error(f"two_layer_get memory lookup error: {e}", exc_info=True)

    val = await cache_get(key)
    if val is not None:
        mem_set(key, val, ttl=60)
    return val


async def two_layer_set(key: str, value, redis_ttl: int = 900, mem_ttl: int = 60):
    """Save to both Redis and in-memory cache."""
    await cache_set(key, value, ttl=redis_ttl)
    mem_set(key, value, ttl=mem_ttl)


async def two_layer_clear_pattern(pattern: str):
    """Clear a key pattern from both Redis and in-memory cache."""
    await cache_clear_pattern(pattern)
    mem_clear_pattern(pattern)