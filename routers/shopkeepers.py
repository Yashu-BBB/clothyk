import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from utils.db import supabase_admin
from utils.auth_utils import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


class ShopkeeperCreate(BaseModel):
    shop_name: str
    shopkeeper_name: str
    contact: str


class ShopkeeperUpdate(BaseModel):
    shop_name: str | None = None
    shopkeeper_name: str | None = None
    contact: str | None = None


@router.get("/admin/all")
async def list_shopkeepers(admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("shopkeepers").select("*").order("id").execute()
        shopkeepers = res.data or []

        # Enrich with product/sold counts
        for sk in shopkeepers:
            code = f"#{sk['id']:03d}"
            prods = supabase_admin.table("products").select("id", count="exact").eq("shopkeeper_code", code).execute()
            sold = supabase_admin.table("orders").select("id", count="exact").eq("shopkeeper_code", code).not_.eq("status", "cancelled").execute()
            sk["total_products"] = prods.count or 0
            sk["total_sold"] = sold.count or 0
            sk["code"] = code

        return shopkeepers
    except Exception as e:
        logger.error(f"Failed to list shopkeepers: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch shopkeepers")


@router.post("/admin/add")
async def add_shopkeeper(data: ShopkeeperCreate, admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("shopkeepers").insert({
            "shop_name": data.shop_name,
            "shopkeeper_name": data.shopkeeper_name,
            "contact": data.contact
        }).execute()
        logger.info(f"Shopkeeper added: {data.shop_name} by admin {admin['sub']}")
        return res.data[0]
    except Exception as e:
        logger.error(f"Failed to add shopkeeper: {e}")
        raise HTTPException(status_code=500, detail="Failed to add shopkeeper")


@router.put("/admin/{sk_id}")
async def update_shopkeeper(sk_id: int, data: ShopkeeperUpdate, admin=Depends(require_admin)):
    try:
        updates = {k: v for k, v in data.dict().items() if v is not None}
        res = supabase_admin.table("shopkeepers").update(updates).eq("id", sk_id).execute()
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"Failed to update shopkeeper: {e}")
        raise HTTPException(status_code=500, detail="Failed to update shopkeeper")


@router.delete("/admin/{sk_id}")
async def delete_shopkeeper(sk_id: int, admin=Depends(require_admin)):
    try:
        supabase_admin.table("shopkeepers").delete().eq("id", sk_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to delete shopkeeper: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete shopkeeper")


@router.get("/admin/dropdown")
async def shopkeepers_dropdown(admin=Depends(require_admin)):
    """For product form dropdown."""
    try:
        res = supabase_admin.table("shopkeepers").select("id,shop_name").order("id").execute()
        return [{"id": s["id"], "label": f"#{s['id']:03d} - {s['shop_name']}"} for s in (res.data or [])]
    except Exception as e:
        logger.error(f"Shopkeeper dropdown failed: {e}")
        return []
