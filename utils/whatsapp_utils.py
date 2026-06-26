import os
import io
import logging
import qrcode
import requests
import base64

logger = logging.getLogger(__name__)

INSTANCE = os.getenv("ULTRAMSG_INSTANCE", "")
TOKEN = os.getenv("ULTRAMSG_TOKEN", "")
WA_NUMBER = os.getenv("WHATSAPP_NUMBER", "")
UPI_ID = os.getenv("UPI_ID", "")
UPI_NAME = os.getenv("UPI_NAME", "Clothyk")

BASE_URL = f"https://api.ultramsg.com/{INSTANCE}"


def _send(endpoint: str, payload: dict) -> bool:
    try:
        payload["token"] = TOKEN
        r = requests.post(f"{BASE_URL}/{endpoint}", data=payload, timeout=10)
        r.raise_for_status()
        logger.info(f"WhatsApp message sent: {endpoint} to {payload.get('to')}")
        return True
    except Exception as e:
        logger.error(f"WhatsApp API failed: {e}")
        return False


def send_text(to: str, message: str) -> bool:
    if not to.startswith("+"):
        to = f"+{to}"
    return _send("messages/chat", {"to": to, "body": message})


def send_image_url(to: str, image_url: str, caption: str = "") -> bool:
    if not to.startswith("+"):
        to = f"+{to}"
    return _send("messages/image", {"to": to, "image": image_url, "caption": caption})


def generate_upi_qr(order_id: str, amount: float) -> bytes:
    """Generate UPI QR code and return image bytes."""
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
    """Send UPI QR code via UltraMsg as base64 image."""
    try:
        qr_bytes = generate_upi_qr(order_id, amount)
        b64 = base64.b64encode(qr_bytes).decode()
        if not to.startswith("+"):
            to = f"+{to}"
        payload = {
            "token": TOKEN,
            "to": to,
            "image": f"data:image/png;base64,{b64}",
            "caption": f"Scan to pay ₹{amount:.0f} for Order #{order_id}"
        }
        r = requests.post(f"{BASE_URL}/messages/image", data=payload, timeout=15)
        r.raise_for_status()
        logger.info(f"UPI QR sent to {to} for order {order_id}")
        return True
    except Exception as e:
        logger.error(f"QR code send failed: {e}")
        return False


# ─── Message Templates ─────────────────────────────────────────────────────

def msg_order_received(name: str, product: str, size: str, color: str, amount: float) -> str:
    return (
        f"Hi {name}! 👋\n"
        f"We received your order for\n"
        f"*{product}* (Size {size}, {color})\n"
        f"Total: ₹{amount:.0f}\n\n"
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

def msg_payment_confirmed(product: str, size: str, color: str, amount: float) -> str:
    return (
        f"✅ Payment Verified! Order Confirmed 🎉\n\n"
        f"Order Details:\n"
        f"Product: {product}\n"
        f"Size: {size} | Color: {color}\n"
        f"Amount Paid: ₹{amount:.0f}\n\n"
        f"We will notify you once shipped! 📦\n\n"
        f"Reply:\n"
        f"TRACK → Track your order 📦\n"
        f"CANCEL → Cancel your order ❌\n"
        f"HELP → Show all options"
    )

def msg_cod_confirmed(product: str, size: str, color: str, amount: float) -> str:
    return (
        f"✅ Order Confirmed! (Cash on Delivery)\n\n"
        f"Order Details:\n"
        f"Product: {product}\n"
        f"Size: {size} | Color: {color}\n"
        f"Amount: ₹{amount:.0f} (pay on delivery)\n\n"
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
