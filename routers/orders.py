import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from utils.db import supabase_admin
from utils.auth_utils import require_admin
from utils.captcha import verify_turnstile
from utils.whatsapp_utils import send_text, send_upi_qr, msg_order_received, msg_shipped, msg_refund_processed

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

WA_NUMBER = __import__("os").getenv("WHATSAPP_NUMBER", "")


class OrderRequest(BaseModel):
    customer_name: str
    customer_phone: str
    customer_address: str
    customer_city: str
    product_id: str
    size: str
    color: str
    captcha_token: str


class StatusUpdate(BaseModel):
    status: str
    tracking_id: str | None = None
    courier_name: str | None = None
    payment_status: str | None = None
    refund_status: str | None = None


@router.post("/create")
@limiter.limit("5/minute")
async def create_order(order: OrderRequest, request: Request):
    client_ip = request.headers.get("CF-Connecting-IP") or request.client.host

    # Verify captcha
    if not verify_turnstile(order.captcha_token, client_ip):
        raise HTTPException(status_code=400, detail="Captcha verification failed")

    # Fetch product (including hidden price)
    try:
        prod_res = supabase_admin.table("products").select("*").eq("id", order.product_id).single().execute()
    except Exception as e:
        logger.error(f"Order save failed - product fetch: {e}")
        raise HTTPException(status_code=404, detail="Product not found")

    prod = prod_res.data
    if not prod or prod["stock"] < 1:
        raise HTTPException(status_code=400, detail="Product out of stock")

    # Create order
    try:
        order_data = {
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone,
            "customer_address": order.customer_address,
            "customer_city": order.customer_city,
            "product_id": order.product_id,
            "product_name": prod["name"],
            "size": order.size,
            "color": order.color,
            "our_price": prod["our_price"],
            "shopkeeper_price": prod["shopkeeper_price"],
            "shopkeeper_id": prod["shopkeeper_id"],
            "shopkeeper_code": prod["shopkeeper_code"],
            "agent_state": {}
        }
        res = supabase_admin.table("orders").insert(order_data).execute()
        new_order = res.data[0]
        logger.info(f"Order created: {new_order['id']}, customer: {order.customer_phone}, product: {prod['name']}")
    except Exception as e:
        logger.error(f"Order save failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create order")

    # Decrement stock (product is unique, set stock to 0)
    try:
        supabase_admin.table("products").update({"stock": prod["stock"] - 1}).eq("id", order.product_id).execute()
    except Exception as e:
        logger.warning(f"Stock update failed for {order.product_id}: {e}")

    # Send WhatsApp order confirmation + payment choice
    wa_msg = msg_order_received(
        order.customer_name,
        prod["name"],
        order.size,
        order.color,
        prod["our_price"]
    )
    send_text(WA_NUMBER, wa_msg)

    return {
        "success": True,
        "order_id": new_order["id"],
        "whatsapp_message": (
            f"🛍️ New Order!\n\n"
            f"Product: {prod['name']}\n"
            f"Code: {prod['shopkeeper_code']}\n"
            f"Size: {order.size} | Color: {order.color}\n"
            f"Price: ₹{prod['our_price']:.0f}\n\n"
            f"👤 Customer Details:\n"
            f"Name: {order.customer_name}\n"
            f"Phone: {order.customer_phone}\n"
            f"Address: {order.customer_address}\n"
            f"City: {order.customer_city}"
        )
    }


# ─── ADMIN ENDPOINTS ──────────────────────────────────────────────────────

@router.get("/admin/all")
async def admin_list_orders(
    status: str | None = None,
    admin=Depends(require_admin)
):
    try:
        q = supabase_admin.table("orders").select("*").order("created_at", desc=True)
        if status:
            q = q.eq("status", status)
        res = q.execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Admin: list orders failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch orders")


@router.put("/admin/{order_id}")
async def update_order(order_id: str, update: StatusUpdate, admin=Depends(require_admin)):
    try:
        current = supabase_admin.table("orders").select("*").eq("id", order_id).single().execute()
        if not current.data:
            raise HTTPException(status_code=404, detail="Order not found")
        order = current.data

        updates = {}
        if update.status:
            updates["status"] = update.status
        if update.tracking_id:
            updates["tracking_id"] = update.tracking_id
        if update.courier_name:
            updates["courier_name"] = update.courier_name
        if update.payment_status:
            updates["payment_status"] = update.payment_status
        if update.refund_status:
            updates["refund_status"] = update.refund_status

        supabase_admin.table("orders").update(updates).eq("id", order_id).execute()
        logger.info(f"Order status updated: {order_id}, {order['status']} → {update.status}")

        # WhatsApp notifications on status change
        phone = order["customer_phone"]

        if update.status == "shipped" and update.tracking_id:
            courier = update.courier_name or "Courier"
            tracking_url = f"https://www.delhivery.com/track/package/{update.tracking_id}"
            send_text(phone, msg_shipped(order["product_name"], courier, update.tracking_id, tracking_url))

        if update.status == "confirmed" and order.get("payment_type") == "upi" and update.payment_status == "verified":
            from utils.whatsapp_utils import msg_payment_confirmed
            send_text(phone, msg_payment_confirmed(
                order["product_name"], order["size"], order["color"], order["our_price"]
            ))

        if update.refund_status == "processed":
            send_text(phone, msg_refund_processed(order["our_price"]))

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Order update failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to update order")


@router.get("/admin/recent")
async def recent_orders(admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("orders").select("*").order("created_at", desc=True).limit(10).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Recent orders fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch recent orders")
