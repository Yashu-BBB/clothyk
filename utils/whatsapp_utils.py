import os
import io
import time
import logging
import qrcode
import requests
import base64
from utils.db import supabase_admin
logger = logging.getLogger(__name__)

WA_NUMBER = os.getenv("WHATSAPP_NUMBER", "")
UPI_ID = os.getenv("UPI_ID", "")
UPI_NAME = os.getenv("UPI_NAME", "clovical")

# ─── WPPConnect server config (replaces UltraMsg) ──────────────────────────
WPP_SERVER_URL = os.getenv("WPP_SERVER_URL", "").rstrip("/")
WPP_API_KEY = os.getenv("WPP_API_KEY", "")
WPP_SESSION = os.getenv("WPP_SESSION", "clovical")


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "x-api-key": WPP_API_KEY,
    }


def _to_wpp_number(to: str) -> str:
    """
    WPPConnect accepts either a bare number (e.g. 917975735906, which it
    appends @c.us to) or a full JID we already have (e.g. "...@c.us" or
    "...@lid" — the latter shows up now that WhatsApp obfuscates some
    numbers as Linked IDs). Pass full JIDs through unchanged.

    Callers sometimes pass the raw customer_phone straight from Supabase
    (e.g. routers/orders.py sends admin-triggered notifications this way),
    which is stored as a bare 10-digit Indian number with no country code.
    Add the +91 country code in that case so WPPConnect targets the right
    WhatsApp ID instead of an invalid/wrong one.
    """
    if "@" in to:
        return to
    digits = to.lstrip("+")
    if len(digits) == 10:  # bare Indian mobile number, no country code yet
        digits = "91" + digits
    return digits


# Retry only transient failures — a dropped connection, a timeout, or a 5xx
# from chatpilot (e.g. mid-reconnect) all mean "we don't know if this went
# out", so retrying is the right call, worst case being a duplicate message.
# A 4xx (bad payload, wrong api key) means retrying identical input will
# just fail identically, so those are not retried.
_RETRY_ATTEMPTS = int(os.getenv("WA_SEND_RETRY_ATTEMPTS", "3"))
_RETRY_BACKOFF_SECONDS = [2, 5]  # gaps before attempt 2 and attempt 3


def _post(endpoint: str, payload: dict, timeout: int = 45) -> bool:
    last_err = None
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            r = requests.post(
                f"{WPP_SERVER_URL}/{endpoint}",
                json=payload,
                headers=_headers(),
                timeout=timeout,
            )
            r.raise_for_status()
            if attempt > 1:
                logger.info(f"WhatsApp message sent: {endpoint} to {payload.get('to')} (succeeded on retry {attempt})")
            else:
                logger.info(f"WhatsApp message sent: {endpoint} to {payload.get('to')}")
            return True
        except requests.exceptions.HTTPError as e:
            last_err = e
            status = e.response.status_code if e.response is not None else None
            if status is not None and 400 <= status < 500:
                logger.error(f"WPPConnect API rejected request ({endpoint}, {status}) — not retrying: {e}")
                return False
            logger.warning(f"WPPConnect API {endpoint} attempt {attempt}/{_RETRY_ATTEMPTS} failed ({status}): {e}")
        except Exception as e:
            last_err = e
            logger.warning(f"WPPConnect API {endpoint} attempt {attempt}/{_RETRY_ATTEMPTS} failed: {e}")

        if attempt < _RETRY_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)])

    logger.error(f"WPPConnect API failed after {_RETRY_ATTEMPTS} attempts ({endpoint}) to {payload.get('to')}: {last_err}")
    return False


def send_text(to: str, message: str) -> bool:
    payload = {
        "session": WPP_SESSION,
        "to": _to_wpp_number(to),
        "message": message,
    }
    return _post("send-text", payload)


def send_image_url(to: str, image_url: str, caption: str = "") -> bool:
    payload = {
        "session": WPP_SESSION,
        "to": _to_wpp_number(to),
        "image": image_url,
        "caption": caption,
    }
    return _post("send-image", payload)


def send_file(to: str, file_url: str, filename: str = "file", caption: str = "") -> bool:
    """Send a file by URL or path."""
    payload = {
        "session": WPP_SESSION,
        "to": _to_wpp_number(to),
        "file": file_url,
        "filename": filename,
        "caption": caption,
    }
    return _post("send-file", payload)


def send_file_base64(to: str, file_bytes: bytes, filename: str = "file.pdf", caption: str = "") -> bool:
    """Send a file (e.g. a generated PDF) from raw bytes."""
    b64 = base64.b64encode(file_bytes).decode()
    payload = {
        "session": WPP_SESSION,
        "to": _to_wpp_number(to),
        "file": f"data:application/pdf;base64,{b64}",
        "filename": filename,
        "caption": caption,
    }
    return _post("send-file", payload)


def generate_upi_qr(order_id: str, amount: float) -> bytes:
    """Generate UPI QR code and return image bytes. (unchanged)"""
    upi_url = f"upi://pay?pa={UPI_ID}&pn={UPI_NAME}&am={amount}&cu=INR&tn=Order{order_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    logger.info(f"QR code generated: order {order_id}, amount ₹{amount}")
    return buf.getvalue()


def send_upi_qr(to: str, order_id: str, amount: float) -> bool:
    """Generate UPI QR → base64 → send via WPPConnect server."""
    try:
        qr_bytes = generate_upi_qr(order_id, amount)
        b64 = base64.b64encode(qr_bytes).decode()
        payload = {
            "session": WPP_SESSION,
            "to": _to_wpp_number(to),
            "image": f"data:image/png;base64,{b64}",
            "caption": f"Scan to pay ₹{amount:.0f} for Order #{order_id}",
        }
        ok = _post("send-image", payload)
        if ok:
            logger.info(f"UPI QR sent to {to} for order {order_id}")
        return ok
    except Exception as e:
        logger.error(f"QR code send failed: {e}")
        return False


# ─── Message Templates (unchanged) ─────────────────────────────────────────

def msg_order_received(name: str, product: str, size: str, color: str, amount: float, delivery_fee: float = 0) -> str:
    breakdown = (
        f"Item: ₹{amount - delivery_fee:.0f}\n"
        f"Delivery: ₹{delivery_fee:.0f}\n"
        f"Total: ₹{amount:.0f}\n\n"
    ) if delivery_fee else f"Total: ₹{amount:.0f}\n\n"
    return (
        f"Hi {name}! 👋\n"
        f"We received your order for\n"
        f"*{product}* (Size {size}, {color})\n"
        f"{breakdown}"
        f"How would you like to pay?\n"
        f"Reply:\n"
        f"1️⃣ UPI (GPay/PhonePe/Paytm)\n"
        f"2️⃣ Cash on Delivery"
    )

def msg_upi_qr_followup() -> str:
    return (
        "After payment please send us 📸\n"
        "your payment screenshot for verification\n"
        "We will confirm your order within 30 minutes ⏳"
    )

def msg_screenshot_received() -> str:
    return (
        "✅ Screenshot received! Thank you 🙏\n"
        "We are verifying your payment ⏳\n"
        "You will be notified once confirmed"
    )

def msg_payment_confirmed(product: str, size: str, color: str, amount: float, delivery_fee: float = 0) -> str:
    breakdown = (
        f"Item: ₹{amount - delivery_fee:.0f}\n"
        f"Delivery: ₹{delivery_fee:.0f}\n"
        f"Total Paid: ₹{amount:.0f}\n\n"
    ) if delivery_fee else f"Amount Paid: ₹{amount:.0f}\n\n"
    return (
        f"✅ Payment Verified! Order Confirmed 🎉\n\n"
        f"Order Details:\n"
        f"Product: {product}\n"
        f"Size: {size} | Color: {color}\n"
        f"{breakdown}"
        f"We will notify you once shipped! 📦\n\n"
        f"Reply:\n"
        f"TRACK → Track your order 📦\n"
        f"CANCEL → Cancel your order ❌\n"
        f"HELP → Show all options"
    )

def msg_cod_pending(product: str, size: str, color: str, amount: float, delivery_fee: float = 0) -> str:
    """
    Sent immediately when a customer picks COD (replies "2"). This is NOT a
    confirmation — the order stays in "pending" status until an admin
    manually confirms it from the admin panel, at which point
    msg_cod_confirmed() below is sent instead.
    """
    return (
        f"🛍️ COD Order Received!\n\n"
        f"Product: {product}\n"
        f"Size: {size} | Color: {color}\n"
        f"Amount: ₹{amount:.0f} (pay on delivery)\n\n"
        f"⏳ Your order is being confirmed by our team.\n"
        f"You will receive a confirmation message shortly.\n\n"
        f"Reply HELP for assistance."
    )

def msg_cod_confirmed(product: str, size: str, color: str, amount: float, delivery_fee: float = 0) -> str:
    breakdown = (
        f"Item: ₹{amount - delivery_fee:.0f}\n"
        f"Delivery: ₹{delivery_fee:.0f}\n"
        f"Total: ₹{amount:.0f} (pay on delivery)\n\n"
    ) if delivery_fee else f"Amount: ₹{amount:.0f} (pay on delivery)\n\n"
    return (
        f"✅ Order Confirmed! (Cash on Delivery)\n\n"
        f"Order Details:\n"
        f"Product: {product}\n"
        f"Size: {size} | Color: {color}\n"
        f"{breakdown}"
        f"Expected delivery: 5-7 working days 🚚\n\n"
        f"Reply:\n"
        f"TRACK → Track your order 📦\n"
        f"CANCEL → Cancel your order ❌\n"
        f"HELP → Show all options"
    )

def msg_shipped(product: str, courier: str, tracking_id: str, tracking_url: str) -> str:
    return (
        f"📦 Your Order is Shipped!\n\n"
        f"Product: {product}\n"
        f"Courier: {courier}\n"
        f"Tracking ID: {tracking_id}\n"
        f"Track here: {tracking_url}\n\n"
        f"Expected delivery: 5-7 working days\n"
        f"Reply TRACK anytime to check status 📦"
    )

def msg_track_confirmed(product: str) -> str:
    return (
        f"📦 Order Status: Confirmed\n"
        f"Your order for *{product}* is being prepared\n"
        f"We will notify you once shipped!"
    )

def msg_track_shipped(courier: str, tracking_id: str, tracking_url: str) -> str:
    return (
        f"🚚 Order Status: Shipped!\n"
        f"Your order is on the way\n"
        f"Expected delivery: 5-7 working days\n"
        f"Courier: {courier}\n"
        f"Tracking ID: {tracking_id}\n"
        f"Track here: {tracking_url}"
    )

def msg_track_delivered() -> str:
    return (
        "✅ Order Delivered!\n"
        "Thank you for shopping with us 🎉\n"
        "Reply REVIEW to share your experience"
    )

def msg_cancel_confirm(product: str, amount: float) -> str:
    return (
        f"Are you sure you want to cancel?\n\n"
        f"Product: {product}\n"
        f"Amount: ₹{amount:.0f}\n\n"
        f"Reply:\n"
        f"YES → Cancel my order\n"
        f"NO → Keep my order"
    )

def msg_cancelled_upi(amount: float) -> str:
    return (
        f"✅ Order Cancelled Successfully\n"
        f"Refund of ₹{amount:.0f} will be processed\n"
        f"within 3-5 working days to your UPI account 💸"
    )

def msg_cancelled_cod() -> str:
    return "✅ Order Cancelled Successfully\nWe hope to see you again! 😊"

def msg_keep_order() -> str:
    return "Great! Your order is still active 😊\nReply TRACK to track your order"

def msg_cannot_cancel(tracking_id: str) -> str:
    return (
        f"❌ Cannot Cancel Order\n"
        f"Your order is already shipped 🚚\n"
        f"Tracking ID: {tracking_id}\n"
        f"For issues reply HELP"
    )

def msg_review_request() -> str:
    return (
        "We'd love your feedback! ⭐\n"
        "Rate your experience:\n\n"
        "Reply:\n"
        "1 → ⭐ Poor\n"
        "2 → ⭐⭐ Average\n"
        "3 → ⭐⭐⭐ Good\n"
        "4 → ⭐⭐⭐⭐ Very Good\n"
        "5 → ⭐⭐⭐⭐⭐ Excellent"
    )

def msg_review_5star() -> str:
    return (
        "Thank you for your feedback! 🙏\n"
        "We're so glad you loved it! 🌟\n"
        "Share us on Instagram and tag us\n"
        "for a special discount on your next order! 🎁"
    )

def msg_review_low() -> str:
    return (
        "Thank you for your feedback! 🙏\n"
        "We're sorry for the experience 😔\n"
        "We will work hard to improve\n"
        "Thank you for letting us know 🙏"
    )

def msg_refund_processed(amount: float) -> str:
    return (
        f"✅ Refund of ₹{amount:.0f} Processed!\n"
        f"Will reflect in your account\n"
        f"within 3-5 working days 💸\n"
        f"Thank you for your patience"
    )

def msg_help() -> str:
    return (
        "Hi! Here's what I can help with 😊\n\n"
        "Reply:\n"
        "TRACK → Track your order 📦\n"
        "CANCEL → Cancel your order ❌\n"
        "REVIEW → Rate your experience ⭐\n"
        "HELP → Show this menu 📋\n\n"
        "For other queries we will\n"
        "get back to you shortly! 🙏"
    )


# ─── Admin inbox: conversation logging + bot pause ─────────────────────────

def log_message(phone: str, direction: str, body: str, message_type: str = "text", customer_name: str = None):
    """
    Record a message in the conversations/messages tables so it shows up
    in the admin "Conversations" inbox. `direction` is 'in' (customer to
    shop) or 'out' (shop to customer). `phone` should be the bare real
    phone digits (e.g. "7975735906"), not a JID.
    """
    try:
        upsert_data = {"phone": phone, "last_message_at": "now()"}
        if customer_name:
            upsert_data["customer_name"] = customer_name
        supabase_admin.table("conversations").upsert(upsert_data, on_conflict="phone").execute()

        supabase_admin.table("messages").insert({
            "phone": phone,
            "direction": direction,
            "body": body,
            "message_type": message_type,
        }).execute()
    except Exception as e:
        logger.error(f"Failed to log message for {phone}: {e}")


def is_bot_paused(phone: str) -> bool:
    """Check whether an admin has taken over this conversation manually."""
    try:
        res = supabase_admin.table("conversations").select("bot_paused").eq("phone", phone).single().execute()
        return bool(res.data and res.data.get("bot_paused"))
    except Exception:
        return False  # no conversation row yet, or lookup failed — bot stays active by default