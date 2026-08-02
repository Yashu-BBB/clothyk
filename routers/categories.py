import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from utils.db import supabase_admin
from utils.auth_utils import require_admin
from utils.cache import two_layer_get, two_layer_set, two_layer_clear_pattern

logger = logging.getLogger(__name__)
router = APIRouter()


class CategoryCreate(BaseModel):
    name: str
    icon: str = "🏷️"
    gender: str
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: str | None = None
    icon: str | None = None
    gender: str | None = None
    sort_order: int | None = None


# ─── PUBLIC ───────────────────────────────────────────────────────────────

@router.get("/")
async def list_categories(gender: str | None = None):
    cache_key = f"categories:all:{gender}"
    try:
        cached = await two_layer_get(cache_key)
        if cached is not None:
            return cached
        q = supabase_admin.table("categories").select("*").order("sort_order")
        if gender:
            q = q.eq("gender", gender)
        res = q.execute()
        data = res.data or []
        await two_layer_set(cache_key, data, redis_ttl=3600, mem_ttl=1800)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch categories: {e}", exc_info=True)
        return []


@router.get("/boys")
async def boys_categories():
    cache_key = "categories:boys"
    try:
        cached = await two_layer_get(cache_key)
        if cached is not None:
            return cached
        res = supabase_admin.table("categories").select("*").eq("gender", "Boys").order("sort_order").execute()
        data = res.data or []
        await two_layer_set(cache_key, data, redis_ttl=3600, mem_ttl=1800)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch boys categories: {e}", exc_info=True)
        return []


@router.get("/girls")
async def girls_categories():
    cache_key = "categories:girls"
    try:
        cached = await two_layer_get(cache_key)
        if cached is not None:
            return cached
        res = supabase_admin.table("categories").select("*").eq("gender", "Girls").order("sort_order").execute()
        data = res.data or []
        await two_layer_set(cache_key, data, redis_ttl=3600, mem_ttl=1800)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch girls categories: {e}", exc_info=True)
        return []


async def warm_categories_cache():
    """Pre-warm categories cache on app startup."""
    for gender in ["Boys", "Girls", None]:
        q = supabase_admin.table("categories").select("*").order("sort_order")
        if gender:
            q = q.eq("gender", gender)
        res = q.execute()
        data = res.data or []
        key = f"categories:all:{gender}"
        await two_layer_set(key, data, redis_ttl=3600, mem_ttl=1800)
    logger.info("Categories cache warmed ✅")


# ─── ADMIN ────────────────────────────────────────────────────────────────

@router.get("/admin/all")
async def admin_list_categories(admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("categories").select("*").order("gender").order("sort_order").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Admin: failed to list categories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch categories")


@router.post("/admin/add")
async def add_category(data: CategoryCreate, admin=Depends(require_admin)):
    if data.gender not in ("Boys", "Girls"):
        raise HTTPException(status_code=400, detail="Gender must be Boys or Girls")
    try:
        res = supabase_admin.table("categories").insert({
            "name": data.name,
            "icon": data.icon,
            "gender": data.gender,
            "sort_order": data.sort_order
        }).execute()
        await two_layer_clear_pattern("categories:")
        logger.info(f"Category added: {data.name} by admin {admin['sub']}")
        return res.data[0]
    except Exception as e:
        logger.error(f"Failed to add category: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add category")


@router.put("/admin/{cat_id}")
async def update_category(cat_id: int, data: CategoryUpdate, admin=Depends(require_admin)):
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        res = supabase_admin.table("categories").update(updates).eq("id", cat_id).execute()
        await two_layer_clear_pattern("categories:")
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"Failed to update category: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update category")


@router.delete("/admin/{cat_id}")
async def delete_category(cat_id: int, admin=Depends(require_admin)):
    try:
        supabase_admin.table("categories").delete().eq("id", cat_id).execute()
        await two_layer_clear_pattern("categories:")
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to delete category: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete category")