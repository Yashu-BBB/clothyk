import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from utils.db import supabase_admin
from utils.auth_utils import get_admin_from_request, require_admin, hash_password
from utils.nimbuspost import track_shipment, cancel_shipment, get_couriers
from utils.cache import (
    cache_get, cache_set, cache_delete, two_layer_get, two_layer_set,
    two_layer_clear_pattern, mem_delete,
)
from utils import cache as cache_utils

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")
limiter = Limiter(key_func=get_remote_address)


def admin_or_redirect(request: Request):
    admin = get_admin_from_request(request)
    if not admin:
        return None
    return admin


@router.get("/dashboard-data")
@limiter.limit("30/minute")
async def dashboard_data(request: Request, admin=Depends(require_admin)):
    cache_key = "admin:dashboard"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

        products_count = supabase_admin.table("products").select("id", count="exact").execute().count or 0

        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        today_orders = supabase_admin.table("orders").select("*")\
            .gte("created_at", today)\
            .not_.eq("status","cancelled").execute().data or []

        recent = supabase_admin.table("orders").select("*").order("created_at", desc=True).limit(10).execute().data or []

        low_stock = supabase_admin.table("products").select("id,name,stock").lte("stock", 2).gt("stock", 0).execute().data or []
        out_of_stock = supabase_admin.table("products").select("id,name").eq("stock", 0).execute().data or []

        refund_pending = supabase_admin.table("orders").select("*").eq("refund_status","pending").execute().data or []

        # NimbusPost shipment stats
        shipments_today = supabase_admin.table("orders").select("id", count="exact")\
            .gte("created_at", today).eq("shipping_status", "created").execute().count or 0

        pending_shipments = supabase_admin.table("orders").select("id", count="exact")\
            .eq("status", "confirmed").is_("nimbuspost_awb", "null").execute().count or 0

        in_transit_shipments = supabase_admin.table("orders").select("id", count="exact")\
            .eq("status", "shipped").not_.is_("nimbuspost_awb", "null").execute().count or 0

        result = {
            "total_products": products_count,
            "orders_today": len(today_orders),
            "revenue_today": sum(o["our_price"] for o in today_orders),
            "profit_today": sum(o["profit"] for o in today_orders),
            "recent_orders": recent,
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
            "refund_pending": refund_pending,
            "shipments_today": shipments_today,
            "pending_shipments": pending_shipments,
            "in_transit_shipments": in_transit_shipments,
        }
        await cache_set(cache_key, result, ttl=120)
        return result
    except Exception as e:
        logger.error(f"Dashboard data failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard data")


@router.get("/cache-stats")
@limiter.limit("10/minute")
async def cache_stats(request: Request, admin=Depends(require_admin)):
    """Shows current cache status for admin monitoring."""
    import time
    active_mem_keys = [
        k for k, (_, exp) in cache_utils._mem_cache.items()
        if time.time() < exp
    ]
    return {
        "memory_cache": {
            "total_keys": len(active_mem_keys),
            "keys": active_mem_keys
        },
        "redis": "connected" if cache_utils.redis_client else "disconnected"
    }


@router.post("/change-password")
async def change_password(
    request: Request,
    admin=Depends(require_admin)
):
    data = await request.json()
    new_pass = data.get("password", "")
    if len(new_pass) < 8:
        raise HTTPException(status_code=400, detail="Password too short")
    hashed = hash_password(new_pass)
    supabase_admin.table("admins").update({"password": hashed}).eq("username", admin["sub"]).execute()
    return {"success": True}


# ─── NimbusPost Shipment Endpoints ─────────────────────────────────────────

@router.get("/orders/{order_id}/label")
async def get_shipment_label(order_id: str, admin=Depends(require_admin)):
    cache_key = f"order:{order_id}:label"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached

        res = supabase_admin.table("orders").select("label_url").eq("id", order_id).single().execute()
        order = res.data
        if not order or not order.get("label_url"):
            raise HTTPException(status_code=404, detail="No shipping label available for this order")
        result = {"label_url": order["label_url"]}
        await cache_set(cache_key, result, ttl=3600)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Label fetch failed for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch label")


@router.post("/orders/{order_id}/ship")
async def ship_order(order_id: str, admin=Depends(require_admin)):
    # Import here (not at module top) to avoid a circular import between
    # routers/admin.py and routers/orders.py
    from routers.orders import create_shipment_for_order

    try:
        res = supabase_admin.table("orders").select("*").eq("id", order_id).single().execute()
        order = res.data
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if order.get("nimbuspost_awb"):
            raise HTTPException(status_code=400, detail="Shipment already created for this order")

        import os
        if not (os.getenv("NIMBUSPOST_API_KEY") or (os.getenv("NIMBUSPOST_EMAIL") and os.getenv("NIMBUSPOST_PASSWORD"))):
            raise HTTPException(status_code=400, detail="NimbusPost not configured")

        result = create_shipment_for_order(order)
        if not result:
            raise HTTPException(status_code=502, detail="Shipment creation failed — check shopkeeper address and NimbusPost status, then retry")

        logger.info(f"Admin {admin['sub']} manually created shipment for order {order_id}: AWB {result['awb']}")
        return {"success": True, **result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Manual shipment creation failed for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create shipment")


@router.post("/orders/{order_id}/cancel-shipment")
async def cancel_order_shipment(order_id: str, admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("orders").select("nimbuspost_awb").eq("id", order_id).single().execute()
        order = res.data
        if not order or not order.get("nimbuspost_awb"):
            raise HTTPException(status_code=404, detail="No NimbusPost shipment found for this order")

        ok = cancel_shipment(order["nimbuspost_awb"])
        if not ok:
            raise HTTPException(status_code=502, detail="NimbusPost cancellation failed — please retry")

        supabase_admin.table("orders").update({"shipping_status": "cancelled"}).eq("id", order_id).execute()
        logger.info(f"Admin {admin['sub']} cancelled shipment for order {order_id}")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Shipment cancellation failed for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel shipment")


@router.get("/orders/{order_id}/track")
async def track_order_shipment(order_id: str, admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("orders").select("nimbuspost_awb").eq("id", order_id).single().execute()
        order = res.data
        if not order or not order.get("nimbuspost_awb"):
            raise HTTPException(status_code=404, detail="No NimbusPost shipment found for this order")

        tracking = track_shipment(order["nimbuspost_awb"])
        if tracking is None:
            raise HTTPException(status_code=502, detail="NimbusPost tracking unavailable right now — please retry")

        return tracking
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Tracking fetch failed for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch tracking info")


@router.get("/nimbuspost/test-connection")
async def test_nimbuspost_connection(admin=Depends(require_admin)):
    """
    Diagnostic endpoint: confirms the Bearer auth (email+password login)
    works end-to-end by hitting the courier-list endpoint. Separate from
    pickup address registration, which uses different (static-key) auth
    — use this to isolate whether an issue is auth-wide or specific to
    the warehouse/pickup-address call.
    """
    result = get_couriers()
    logger.info(f"NimbusPost connection test by admin {admin['sub']}: {result}")
    return result


# ─── Settings (manual/auto shipment mode) ──────────────────────────────────

class SettingUpdate(BaseModel):
    value: str


@router.get("/settings/{key}")
async def get_setting(key: str, admin=Depends(require_admin)):
    cache_key = f"settings:{key}"
    try:
        cached = await two_layer_get(cache_key)
        if cached is not None:
            return cached
        res = supabase_admin.table("settings").select("*").eq("key", key).maybe_single().execute()
        result = res.data or {"key": key, "value": None}
        await two_layer_set(cache_key, result, redis_ttl=300, mem_ttl=120)
        return result
    except Exception as e:
        logger.error(f"Failed to fetch setting {key}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch setting")


@router.put("/settings/{key}")
async def update_setting(key: str, data: SettingUpdate, admin=Depends(require_admin)):
    try:
        supabase_admin.table("settings").upsert({"key": key, "value": data.value}).execute()
        logger.info(f"Setting updated: {key} = {data.value} by admin {admin['sub']}")
        await two_layer_clear_pattern("settings:")
        mem_delete("settings:delivery_fee")
        mem_delete("settings:girls_section_enabled")
        mem_delete("public_settings")
        await cache_delete("public_settings")
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to update setting {key}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update setting")


# ─── Public Settings (no auth — for customer-facing pages) ─────────────────

@router.get("/public-settings")
async def public_settings():
    """Public endpoint — returns non-sensitive settings for frontend.
    No auth required. Cached 5 min in Redis / 2 min in memory.
    """
    cache_key = "public_settings"
    try:
        cached = await two_layer_get(cache_key)
        if cached is not None:
            return cached

        res = supabase_admin.table("settings").select("key,value").in_(
            "key", ["girls_section_enabled", "delivery_fee"]
        ).execute()
        result = {row["key"]: row["value"] for row in (res.data or [])}
        # Ensure defaults if rows don't exist yet
        result.setdefault("girls_section_enabled", "false")
        result.setdefault("delivery_fee", "0")
        await two_layer_set(cache_key, result, redis_ttl=300, mem_ttl=120)
        return result
    except Exception as e:
        logger.error(f"Failed to fetch public settings: {e}", exc_info=True)
        return {"girls_section_enabled": "false", "delivery_fee": "0"}