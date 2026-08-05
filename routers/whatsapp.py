import logging
import os
from fastapi import APIRouter, Request, HTTPException
from utils.db import supabase_admin
from utils.whatsapp_utils import (
    send_text, send_upi_qr,
    msg_order_received, msg_upi_qr_followup, msg_screenshot_received,
    msg_cod_confirmed, msg_track_confirmed, msg_track_shipped, msg_track_delivered,
    msg_cancel_confirm, msg_cancelled_upi, msg_cancelled_cod, msg_keep_order,
    msg_cannot_cancel, msg_review_request, msg_review_5star, msg_review_low,
    msg_help
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Shared secret the WPPConnect server sends back with each webhook call,
# so we can verify it's really our own server forwarding the message.
WPP_API_KEY = os.getenv("WPP_API_KEY", "")


def order_total(order: dict) -> float:
    """
    Amount the customer actually owes: item price + delivery fee.
    Falls back to computing it manually if the generated total_amount
    column isn't present yet (e.g. migration not run).
    """
    delivery_fee = order.get("delivery_fee") or 0
    return order.get("total_amount") or (order["our_price"] + delivery_fee)


def normalize_phone(phone: str) -> str:
    phone = phone.strip().replace(" ", "").replace("-", "").replace("+", "")
    if phone.startswith("91") and len(phone) == 12:
        return phone[2:]
    return phone[-10:]


async def get_latest_order(phone: str):
    """Get the most recent non-cancelled order for a phone number."""
    try:
        res = supabase_admin.table("orders").select("*")\
            .eq("customer_phone", phone)\
            .not_.eq("status", "cancelled")\
            .order("created_at", desc=True)\
            .limit(1).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"Failed to fetch order for {phone}: {e}", exc_info=True)
        return None


async def get_agent_state(order_id: str) -> dict:
    try:
        res = supabase_admin.table("orders").select("agent_state").eq("id", order_id).single().execute()
        return res.data.get("agent_state", {}) if res.data else {}
    except:
        return {}


async def set_agent_state(order_id: str, state: dict):
    try:
        supabase_admin.table("orders").update({"agent_state": state}).eq("id", order_id).execute()
    except Exception as e:
        logger.error(f"Failed to set agent state: {e}", exc_info=True)


@router.post("/webhook")
async def whatsapp_webhook(request: Request):
    """
    WPPConnect server forwards incoming WhatsApp events here.

    Expected payload shape:
    {
      "event": "onmessage",
      "session": "clovical",
      "data": {
        "id": "message_id",
        "from": "917975735906@c.us",
        "to": "916362341792@c.us",
        "body": "1",                      # text, or base64 for media
        "type": "chat" | "image" | ...,
        "isMedia": false,
        "mimetype": null,
        "caption": null,
        "timestamp": 1234567890
      }
    }
    """
    # Shared-secret check (WPPConnect server sends this as a header). When
    # WPP_API_KEY is configured, the header is required — an attacker
    # omitting it entirely must not be able to skip verification.
    if WPP_API_KEY:
        incoming_key = request.headers.get("x-api-key", "")
        if incoming_key != WPP_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        body = await request.json()
        logger.info(f"Webhook received: {str(body)[:200]}")
    except Exception as e:
        logger.error(f"Webhook parse error: {e}", exc_info=True)
        return {"status": "ok"}

    event = body.get("event", "")
    msg_data = body.get("data", {}) or {}

    # Only handle incoming message events; ignore status/ack/etc. events
    if event and event != "onmessage":
        return {"status": "ok"}

    msg_type = msg_data.get("type", "")
    raw_from = msg_data.get("from", "")
    is_image = bool(msg_data.get("isMedia")) or msg_type == "image"
    body_text = ("" if is_image else (msg_data.get("body", "") or "")).strip().upper()

    if not raw_from:
        return {"status": "ok"}

    # Ignore group messages
    if "@g.us" in raw_from:
        return {"status": "ok"}

    # Ignore protocol/system events that aren't actual customer messages
    # (e2e_notification, ciphertext, revoked, etc. arrive as WhatsApp's
    # encryption handshake noise around every real message)
    ACTIONABLE_TYPES = {"chat", "image", "document", "video", "ptt", "audio"}
    if msg_type not in ACTIONABLE_TYPES and not is_image:
        return {"status": "ok"}

    # WhatsApp is rolling out @lid (Linked ID) identifiers that replace the
    # real phone number for privacy — raw_from may be "<opaque-id>@lid"
    # instead of "<phone>@c.us". The WPPConnect server attempts to resolve
    # this via getPnLidEntry and, if successful, includes the real number
    # in msg_data["pn"]. Reply target is always the exact JID WhatsApp gave
    # us; order lookups use the resolved phone number when available.
    is_lid = raw_from.endswith("@lid")
    resolved_pn = (msg_data.get("pn") or "").strip()
    phone_raw = raw_from.split("@")[0]
    full_phone = raw_from  # reply target: exact JID WhatsApp gave us

    if is_lid and resolved_pn:
        phone = normalize_phone(resolved_pn)
        logger.info(f"Resolved @lid {raw_from} -> phone {phone} via WPPConnect")
    elif is_lid:
        phone = phone_raw
        logger.warning(
            f"Incoming message uses @lid with no resolvable phone number: {raw_from}. "
            f"Order lookup by phone will likely miss."
        )
    else:
        phone = normalize_phone(phone_raw)

    logger.info(f"WhatsApp incoming from {phone}: type={msg_type}, body={body_text[:50]}")

    order = await get_latest_order(phone)
    agent_state = await get_agent_state(order["id"]) if order else {}

    # ─── Command routing ────────────────────────────────────────────────
    # (Everything below this line is unchanged from the original agent logic.)

    # TRACK command
    if body_text == "TRACK":
        if not order:
            send_text(full_phone, "No active order found.\nReply HELP for assistance.")
            return {"status": "ok"}
        s = order["status"]
        if s == "confirmed":
            send_text(full_phone, msg_track_confirmed(order["product_name"]))
        elif s == "shipped":
            tracking_url = f"https://www.delhivery.com/track/package/{order.get('tracking_id','')}"
            send_text(full_phone, msg_track_shipped(order.get("courier_name",""), order.get("tracking_id",""), tracking_url))
        elif s == "delivered":
            send_text(full_phone, msg_track_delivered())
        else:
            send_text(full_phone, f"Order Status: {s.title()}\nReply HELP for options.")
        return {"status": "ok"}

    # REVIEW command
    if body_text == "REVIEW":
        if not order:
            send_text(full_phone, "No order found. Reply HELP for assistance.")
            return {"status": "ok"}
        await set_agent_state(order["id"], {"awaiting": "review"})
        send_text(full_phone, msg_review_request())
        return {"status": "ok"}

    # CANCEL command
    if body_text == "CANCEL":
        if not order:
            send_text(full_phone, "No active order found. Reply HELP for assistance.")
            return {"status": "ok"}
        s = order["status"]
        if s in ("shipped", "delivered"):
            send_text(full_phone, msg_cannot_cancel(order.get("tracking_id", "N/A")))
        else:
            await set_agent_state(order["id"], {"awaiting": "cancel_confirm"})
            send_text(full_phone, msg_cancel_confirm(order["product_name"], order_total(order)))
        return {"status": "ok"}

    # HELP is an explicit override command that always works.
    # HI/HELLO/START are NOT intercepted here — they fall through so that a
    # customer with a fresh pending order gets the payment prompt instead of
    # being stuck on the generic menu (see fallback at the bottom).
    if body_text == "HELP":
        send_text(full_phone, msg_help())
        return {"status": "ok"}

    # ─── State-based responses ──────────────────────────────────────────

    awaiting = agent_state.get("awaiting")

    # Awaiting payment choice (1 = UPI, 2 = COD)
    if awaiting == "payment_choice":
        if body_text in ("1", "UPI"):
            send_upi_qr(full_phone, order["id"], order_total(order))
            import asyncio; await asyncio.sleep(1)
            send_text(full_phone, msg_upi_qr_followup())
            await set_agent_state(order["id"], {"awaiting": "screenshot"})
        elif body_text in ("2", "COD"):
            send_text(full_phone, msg_cod_confirmed(
                order["product_name"], order["size"], order["color"],
                order_total(order), order.get("delivery_fee") or 0
            ))
            supabase_admin.table("orders").update({"payment_type": "cod", "status": "confirmed"}).eq("id", order["id"]).execute()
            await set_agent_state(order["id"], {})
        else:
            send_text(full_phone, "Please reply:\n1️⃣ for UPI\n2️⃣ for Cash on Delivery")
        return {"status": "ok"}

    # Awaiting screenshot
    if awaiting == "screenshot":
        if is_image:
            send_text(full_phone, msg_screenshot_received())
            supabase_admin.table("orders").update({"payment_status": "received"}).eq("id", order["id"]).execute()
            await set_agent_state(order["id"], {"awaiting": "payment_verification"})
            logger.info(f"Payment screenshot received: order {order['id']}")
        else:
            send_text(full_phone, "Please send your payment screenshot 📸")
        return {"status": "ok"}

    # Awaiting cancel confirmation
    if awaiting == "cancel_confirm":
        if body_text == "YES":
            if order["payment_type"] == "upi" and order["payment_status"] in ("received", "verified"):
                send_text(full_phone, msg_cancelled_upi(order_total(order)))
                supabase_admin.table("orders").update({"status": "cancelled", "refund_status": "pending"}).eq("id", order["id"]).execute()
                logger.warning(f"Order cancellation: {order['id']}, refund pending")
            else:
                send_text(full_phone, msg_cancelled_cod())
                supabase_admin.table("orders").update({"status": "cancelled"}).eq("id", order["id"]).execute()
            await set_agent_state(order["id"], {})
        elif body_text == "NO":
            send_text(full_phone, msg_keep_order())
            await set_agent_state(order["id"], {})
        else:
            send_text(full_phone, "Reply YES to confirm cancellation or NO to keep your order.")
        return {"status": "ok"}

    # Awaiting review rating
    if awaiting == "review":
        if body_text in ("1","2","3","4","5"):
            rating = int(body_text)
            supabase_admin.table("reviews").insert({"order_id": order["id"], "customer_phone": phone, "rating": rating}).execute()
            supabase_admin.table("orders").update({"review_rating": rating}).eq("id", order["id"]).execute()
            if rating == 5:
                send_text(full_phone, msg_review_5star())
            else:
                send_text(full_phone, msg_review_low())
            await set_agent_state(order["id"], {})
        else:
            send_text(full_phone, "Please reply with a number 1-5 ⭐")
        return {"status": "ok"}

    # New order message → trigger payment flow
    if order and order["status"] == "pending" and not awaiting:
        await set_agent_state(order["id"], {"awaiting": "payment_choice"})
        send_text(full_phone, msg_order_received(
            order["customer_name"], order["product_name"],
            order["size"], order["color"], order_total(order), order.get("delivery_fee") or 0
        ))
        return {"status": "ok"}

    # Fallback
    send_text(full_phone, msg_help())
    return {"status": "ok"}