import re
import logging
import requests
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from utils.db import supabase_admin
from utils.auth_utils import require_admin
from utils.captcha import verify_turnstile
from utils.whatsapp_utils import send_text, send_image_url, send_file_base64, send_upi_qr, msg_order_received, msg_shipped, msg_refund_processed
from utils.nimbuspost import create_shipment
from utils.label_generator import build_shopkeeper_package_pdf
from utils.cache import cache_get, cache_set, cache_delete, two_layer_get, two_layer_set

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

WA_NUMBER = __import__("os").getenv("WHATSAPP_NUMBER", "")


class OrderRequest(BaseModel):
    customer_name: str = Field(..., max_length=200)
    customer_phone: str = Field(..., max_length=10, min_length=10)
    customer_address: str = Field(..., max_length=500)
    customer_city: str = Field(..., max_length=100)
    customer_pincode: str = Field(..., max_length=6, min_length=6)
    product_id: str = Field(..., max_length=36)
    size: str = Field(..., max_length=50)
    color: str = Field(..., max_length=100)
    captcha_token: str = Field(..., max_length=2000)


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
    cache_key = "settings:delivery_fee"
    cached = await two_layer_get(cache_key)
    if cached is not None:
        return cached
    result = {"delivery_fee": get_delivery_fee()}
    await two_layer_set(cache_key, result, redis_ttl=300, mem_ttl=120)
    return result


def _send_shopkeeper_package_pdf(order: dict, shopkeeper: dict, nimbuspost_label_bytes: bytes | None):
    """
    Builds the 2-page shopkeeper PDF (product photo + either NimbusPost's
    untouched label or our own fallback slip) and sends it via WhatsApp.
    Never raises — a notification failure must never block order/shipment
    processing.
    """
    try:
        contact = shopkeeper.get("contact") if shopkeeper else None
        if not contact:
            logger.warning(f"Shopkeeper has no contact number — skipping package PDF for order {order.get('id')}")
            return
        pdf_bytes = build_shopkeeper_package_pdf(order, shopkeeper, nimbuspost_label_bytes)
        send_file_base64(
            contact, pdf_bytes,
            filename=f"order_{str(order.get('id'))[:8]}.pdf",
            caption="📦 New order — photo on page 1, shipping details on page 2. Print double-sided and attach to the package."
        )
    except Exception as e:
        logger.warning(f"Shopkeeper package PDF failed for order {order.get('id')}: {e}")


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
            # No NimbusPost label possible without an address — send the
            # fallback package PDF instead, so the shopkeeper still gets
            # the photo + order details to work from.
            if shopkeeper:
                _send_shopkeeper_package_pdf(order, shopkeeper, None)
            return None

        result = create_shipment(order, shopkeeper)
        if not result:
            supabase_admin.table("orders").update({"shipping_status": "failed"}).eq("id", order["id"]).execute()
            _send_shopkeeper_package_pdf(order, shopkeeper, None)
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

        # Fetch NimbusPost's own official label and merge it (untouched —
        # never edited, so the barcode/AWB stays valid) with our product
        # photo page, then send the combined PDF to the shopkeeper.
        label_bytes = None
        if result.get("label_url"):
            try:
                label_resp = requests.get(result["label_url"], timeout=15)
                label_resp.raise_for_status()
                label_bytes = label_resp.content
            except Exception as e:
                logger.warning(f"Could not fetch NimbusPost label PDF for order {order['id']}: {e}")
        _send_shopkeeper_package_pdf(order, shopkeeper, label_bytes)

        return result
    except Exception as e:
        logger.error(f"create_shipment_for_order failed for order {order.get('id')}: {e}", exc_info=True)
        return None


@router.post("/create")
@limiter.limit("5/minute")
async def create_order(order: OrderRequest, request: Request):
    client_ip = request.headers.get("CF-Connecting-IP") or request.client.host

    # Verify captcha
    if not verify_turnstile(order.captcha_token, client_ip):
        raise HTTPException(status_code=400, detail="Captcha verification failed")

    # Phone number validation (frontend validation can be bypassed)
    phone = order.customer_phone.strip().replace(" ", "").replace("-", "")
    if not re.match(r'^\d{10}$', phone):
        raise HTTPException(
            status_code=400,
            detail="Invalid phone number. Must be 10 digits."
        )
    order.customer_phone = phone  # use cleaned version

    # Pincode validation (frontend validation can be bypassed)
    if not re.match(r'^[1-9][0-9]{5}$', order.customer_pincode):
        raise HTTPException(
            status_code=400,
            detail="Invalid pincode. Must be a valid 6-digit Indian pincode."
        )

    # Fetch product (including hidden price)
    try:
        prod_res = supabase_admin.table("products").select("*").eq("id", order.product_id).single().execute()
    except Exception as e:
        logger.error(f"Order save failed - product fetch: {e}", exc_info=True)
        raise HTTPException(status_code=404, detail="Product not found")

    prod = prod_res.data
    if not prod or prod["stock"] < 1:
        raise HTTPException(status_code=400, detail="Product out of stock")

    # Delivery fee is frozen at order-creation time (from the admin setting)
    # so later changes to the setting never alter an existing order's total.
    delivery_fee = get_delivery_fee()

    # Main product photo, denormalized onto the order so it stays correct
    # even if the product is later edited or removed. Prefer the new
    # multi-image array's first photo, fall back to the legacy single
    # "image" field for older products.
    main_image_url = None
    if prod.get("images"):
        main_image_url = prod["images"][0]
    elif prod.get("image"):
        main_image_url = prod["image"]

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
            "product_image": main_image_url,
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

        await cache_delete("orders:recent")
        await cache_delete("admin:dashboard")
        await cache_delete("analytics:overview")
    except Exception as e:
        logger.error(f"Order save failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create order")

    # Decrement stock atomically — only succeeds if stock hasn't changed
    # since we read it above. If it has (a concurrent order beat us to it),
    # roll back the order we just created instead of allowing stock to go negative.
    try:
        stock_result = supabase_admin.table("products")\
            .update({"stock": prod["stock"] - 1})\
            .eq("id", order.product_id)\
            .eq("stock", prod["stock"])\
            .execute()

        if not stock_result.data:
            supabase_admin.table("orders").delete().eq("id", new_order["id"]).execute()
            logger.warning(f"Stock race condition detected for {order.product_id} — order {new_order['id']} rolled back")
            raise HTTPException(
                status_code=409,
                detail="Product just went out of stock. Please try again."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Stock update failed for {order.product_id}: {e}", exc_info=True)

    total_amount = prod["our_price"] + delivery_fee
    price_lines = (
        f"Price: ₹{prod['our_price']:.0f}\n"
        f"Delivery: ₹{delivery_fee:.0f}\n"
        f"Total: ₹{total_amount:.0f}\n\n"
    ) if delivery_fee else f"Price: ₹{prod['our_price']:.0f}\n\n"

    admin_notification = (
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

    return {
        "success": True,
        "order_id": new_order["id"],
        "admin_phone": WA_NUMBER,
        "whatsapp_message": admin_notification,
        "product_image": main_image_url
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
        logger.error(f"Admin: list orders failed: {e}", exc_info=True)
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

        await cache_delete("admin:dashboard")
        await cache_delete("analytics:overview")
        await cache_delete("orders:recent")

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

            # Send the shopkeeper their package PDF (product photo + either
            # NimbusPost's label or our fallback slip). If auto-ship is on,
            # create_shipment_for_order() below decides success/failure and
            # sends it exactly once from there — sending it here too would
            # duplicate it. If auto-ship is off, no shipment will ever be
            # attempted automatically, so send the fallback version now.
            if not _is_auto_ship_enabled():
                try:
                    shopkeeper_id = order.get("shopkeeper_id")
                    if shopkeeper_id:
                        sk_res = supabase_admin.table("shopkeepers").select("*").eq("id", shopkeeper_id).single().execute()
                        shopkeeper = sk_res.data
                        if shopkeeper:
                            _send_shopkeeper_package_pdf(order, shopkeeper, None)
                        else:
                            logger.warning(f"Shopkeeper {shopkeeper_id} not found — skipping package PDF")
                    else:
                        logger.warning(f"Order {order_id} has no shopkeeper_id — skipping package PDF")
                except Exception as e:
                    logger.warning(f"Shopkeeper package PDF failed for order {order_id}: {e}")

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
        logger.error(f"Order update failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update order")


@router.get("/admin/recent")
async def recent_orders(admin=Depends(require_admin)):
    cache_key = "orders:recent"
    try:
        cached = await cache_get(cache_key)
        if cached is not None:
            return cached
        res = supabase_admin.table("orders").select("*").order("created_at", desc=True).limit(10).execute()
        data = res.data or []
        await cache_set(cache_key, data, ttl=60)
        return data
    except Exception as e:
        logger.error(f"Recent orders fetch failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch recent orders")