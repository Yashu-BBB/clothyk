import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from utils.db import supabase_admin
from utils.auth_utils import require_admin

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
    try:
        q = supabase_admin.table("categories").select("*").order("sort_order")
        if gender:
            q = q.eq("gender", gender)
        res = q.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Failed to fetch categories: {e}")
        return []


@router.get("/boys")
async def boys_categories():
    try:
        res = supabase_admin.table("categories").select("*").eq("gender", "Boys").order("sort_order").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Failed to fetch boys categories: {e}")
        return []


@router.get("/girls")
async def girls_categories():
    try:
        res = supabase_admin.table("categories").select("*").eq("gender", "Girls").order("sort_order").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Failed to fetch girls categories: {e}")
        return []


# ─── ADMIN ────────────────────────────────────────────────────────────────

@router.get("/admin/all")
async def admin_list_categories(admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("categories").select("*").order("gender").order("sort_order").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Admin: failed to list categories: {e}")
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
        logger.info(f"Category added: {data.name} by admin {admin['sub']}")
        return res.data[0]
    except Exception as e:
        logger.error(f"Failed to add category: {e}")
        raise HTTPException(status_code=500, detail="Failed to add category")


@router.put("/admin/{cat_id}")
async def update_category(cat_id: int, data: CategoryUpdate, admin=Depends(require_admin)):
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        res = supabase_admin.table("categories").update(updates).eq("id", cat_id).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"Failed to update category: {e}")
        raise HTTPException(status_code=500, detail="Failed to update category")


@router.delete("/admin/{cat_id}")
async def delete_category(cat_id: int, admin=Depends(require_admin)):
    try:
        supabase_admin.table("categories").delete().eq("id", cat_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to delete category: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete category")
