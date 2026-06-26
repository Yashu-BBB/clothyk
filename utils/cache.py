import os
import json
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
        logger.error(f"Redis connection failed: {e}")
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
        logger.error(f"Cache get error: {e}")
        return None

async def cache_set(key: str, value: dict | list, ttl: int = 900):
    if not redis_client:
        return
    try:
        await redis_client.setex(key, ttl, json.dumps(value))
    except Exception as e:
        logger.error(f"Cache set error: {e}")

async def cache_delete(key: str):
    if not redis_client:
        return
    try:
        await redis_client.delete(key)
        logger.info(f"Cache cleared: {key}")
    except Exception as e:
        logger.error(f"Cache delete error: {e}")

async def cache_clear_pattern(pattern: str):
    if not redis_client:
        return
    try:
        keys = await redis_client.keys(pattern)
        if keys:
            await redis_client.delete(*keys)
        logger.info(f"Cache cleared pattern: {pattern} - {len(keys)} keys")
    except Exception as e:
        logger.error(f"Cache clear pattern error: {e}")
