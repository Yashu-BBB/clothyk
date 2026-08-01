import logging
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from utils.db import supabase_admin
from utils.auth_utils import require_admin
from utils.captcha import verify_turnstile
from utils.whatsapp_utils import send_text, send_upi_qr, msg_order_received, msg_shipped, msg_refund_processed
from utils.nimbuspost import create_shipment

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

WA_NUMBER = __import__("os").getenv("WHATSAPP_NUMBER", "")


class OrderRequest(BaseModel):
    customer_name: str
    customer_phone: str
    customer_address: str
    customer_city: str
    customer_pincode: str
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


# ─── NimbusPost auto-ship helpers ──────────────────────────────────────────

def _is_auto_ship_enabled() -> bool:
    """Reads the nimbuspost_auto_mode toggle from the settings table."""
    try:
        res = supabase_admin.table("settings").select("value").eq("key", "nimbuspost_auto_mode").maybe_single().execute()
        return bool(res.data and res.data.get("value") == "true")
    except Exception as e:
        logger.warning(f"Could not read nimbuspost_auto_mode setting, defaulting to manual: {e}")
        return False


def get_delivery_fee() -> float:
    """
    Reads the admin-configured delivery fee from the settings table.
    Defaults to 0 if not set or unreadable, so checkout never breaks.
    """
    try:
        res = supabase_admin.table("settings").select("value").eq("key", "delivery_fee").maybe_single().execute()
        return float(res.data.get("value", 0)) if res.data and res.data.get("value") else 0.0
    except Exception as e:
        logger.warning(f"Could not read delivery_fee setting, defaulting to 0: {e}")
        return 0.0


@router.get("/delivery-fee")
async def public_delivery_fee():
    """Public (no-auth) endpoint the checkout page reads to show the delivery fee."""
    return {"delivery_fee": get_delivery_fee()}


def create_shipment_for_order(order: dict) -> dict | None:
    """
    Fetches the order's shopkeeper and calls NimbusPost to create a
    shipment, then persists the returned AWB/courier/label on the order
    and notifies the customer via WhatsApp.

    Shared between the auto-ship flow here and the manual "Create Shipment"
    admin endpoint in routers/admin.py. Returns the NimbusPost result dict
    on success, or None on failure (never raises — shipment failures must
    never block the rest of the order flow).
    """
    try:
        if order.get("nimbuspost_awb"):
            logger.info(f"Order {order['id']} already has a NimbusPost shipment — skipping")
            return None

        shopkeeper_id = order.get("shopkeeper_id")
        if not shopkeeper_id:
            logger.warning(f"Order {order['id']} has no shopkeeper_id — cannot create shipment")
            return None

        sk_res = supabase_admin.table("shopkeepers").select("*").eq("id", shopkeeper_id).single().execute()
        shopkeeper = sk_res.data
        if not shopkeeper or not shopkeeper.get("address"):
            logger.warning(f"Shopkeeper {shopkeeper_id} has no registered address — cannot create shipment for order {order['id']}")
            return None

        result = create_shipment(order, shopkeeper)
        if not result:
            supabase_admin.table("orders").update({"shipping_status": "failed"}).eq("id", order["id"]).execute()
            return None

        supabase_admin.table("orders").update({
            "nimbuspost_awb": result["awb"],
            "tracking_id": result["awb"],
            "courier_name": result["courier_name"],
            "label_url": result["label_url"],
            "nimbuspost_shipment_id": result["shipment_id"],
            "shipping_status": "created",
            "status": "shipped",
        }).eq("id", order["id"]).execute()

        logger.info(f"NimbusPost shipment created for order {order['id']}: AWB {result['awb']}")

        tracking_url = f"https://www.nimbuspost.com/track/{result['awb']}"
        send_text(order["customer_phone"], msg_shipped(
            order["product_name"], result["courier_name"] or "Courier", result["awb"], tracking_url
        ))

        return result
    except Exception as e:
        logger.error(f"create_shipment_for_order failed for order {order.get('id')}: {e}")
        return None


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

    # Delivery fee is frozen at order-creation time (from the admin setting)
    # so later changes to the setting never alter an existing order's total.
    delivery_fee = get_delivery_fee()

    # Create order
    try:
        order_data = {
            "customer_name": order.customer_name,
            "customer_phone": order.customer_phone,
            "customer_address": order.customer_address,
            "customer_city": order.customer_city,
            "customer_pincode": order.customer_pincode,
            "product_id": order.product_id,
            "product_name": prod["name"],
            "size": order.size,
            "color": order.color,
            "our_price": prod["our_price"],
            "shopkeeper_price": prod["shopkeeper_price"],
            "shopkeeper_id": prod["shopkeeper_id"],
            "shopkeeper_code": prod["shopkeeper_code"],
            "payment_type": "upi",
            "delivery_fee": delivery_fee,
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

    total_amount = prod["our_price"] + delivery_fee
    price_lines = (
        f"Price: ₹{prod['our_price']:.0f}\n"
        f"Delivery: ₹{delivery_fee:.0f}\n"
        f"Total: ₹{total_amount:.0f}\n\n"
    ) if delivery_fee else f"Price: ₹{prod['our_price']:.0f}\n\n"

    return {
        "success": True,
        "order_id": new_order["id"],
        "admin_phone": WA_NUMBER,
        "whatsapp_message": (
            f"🛍️ New Order!\n\n"
            f"Product: {prod['name']}\n"
            f"Code: {prod['shopkeeper_code']}\n"
            f"Size: {order.size} | Color: {order.color}\n"
            f"{price_lines}"
            f"👤 Customer Details:\n"
            f"Name: {order.customer_name}\n"
            f"Phone: {order.customer_phone}\n"
            f"Address: {order.customer_address}\n"
            f"City: {order.customer_city}\n"
        f"Pincode: {order.customer_pincode}"
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

        delivery_fee = order.get("delivery_fee") or 0
        total_amount = order.get("total_amount") or (order["our_price"] + delivery_fee)

        if update.status == "confirmed" and order.get("payment_type") == "upi" and update.payment_status == "verified":
            from utils.whatsapp_utils import msg_payment_confirmed
            send_text(phone, msg_payment_confirmed(
                order["product_name"], order["size"], order["color"], total_amount, delivery_fee
            ))

        if update.refund_status == "processed":
            send_text(phone, msg_refund_processed(total_amount))

        # NimbusPost auto-ship: only fires when payment is verified on a
        # confirmed order and the auto-mode setting is turned on. In manual
        # mode, admins trigger shipment creation from the Orders page instead.
        if (update.status == "confirmed"
                and update.payment_status == "verified"
                and _is_auto_ship_enabled()):
            merged_order = {**order, **updates}
            create_shipment_for_order(merged_order)

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