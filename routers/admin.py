import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from utils.db import supabase_admin
from utils.auth_utils import get_admin_from_request, require_admin, hash_password

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="templates")


def admin_or_redirect(request: Request):
    admin = get_admin_from_request(request)
    if not admin:
        return None
    return admin


@router.get("/dashboard-data")
async def dashboard_data(admin=Depends(require_admin)):
    try:
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

        return {
            "total_products": products_count,
            "orders_today": len(today_orders),
            "revenue_today": sum(o["our_price"] for o in today_orders),
            "profit_today": sum(o["profit"] for o in today_orders),
            "recent_orders": recent,
            "low_stock": low_stock,
            "out_of_stock": out_of_stock,
            "refund_pending": refund_pending
        }
    except Exception as e:
        logger.error(f"Dashboard data failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard data")


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
