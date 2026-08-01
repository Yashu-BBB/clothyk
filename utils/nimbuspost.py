"""
NimbusPost courier integration for Clothyk.

Handles pickup-address registration, shipment creation, tracking, and
cancellation via the NimbusPost API (https://api.nimbuspost.com/v1).

NimbusPost uses email+password login to obtain a JWT token, which is then
sent as the `token` header on every subsequent call. The token is cached
in-process and refreshed automatically ~1 hour before its 24h expiry.

Every function degrades gracefully (returns None / False) when
NIMBUSPOST_API_KEY / credentials are not yet configured, or when the API
call fails for any reason — NimbusPost is never allowed to block the core
order flow. All failures are logged and reported to Sentry if configured.
"""

import os
import time
import logging
import requests
import sentry_sdk

logger = logging.getLogger(__name__)

NIMBUSPOST_BASE_URL = "https://api.nimbuspost.com/v1"

NIMBUSPOST_API_KEY = os.getenv("NIMBUSPOST_API_KEY", "")
NIMBUSPOST_EMAIL = os.getenv("NIMBUSPOST_EMAIL", "")
NIMBUSPOST_PASSWORD = os.getenv("NIMBUSPOST_PASSWORD", "")

_REQUEST_TIMEOUT = 15

# ─── Token cache (in-process, refreshed ~1h before 24h expiry) ────────────
_token_cache: dict = {"token": None, "fetched_at": 0}
_TOKEN_TTL_SECONDS = 23 * 3600  # refresh a bit before the real 24h expiry


def _is_configured() -> bool:
    """NimbusPost needs either a static API key OR email+password login."""
    return bool(NIMBUSPOST_API_KEY or (NIMBUSPOST_EMAIL and NIMBUSPOST_PASSWORD))


def get_auth_token() -> str | None:
    """
    Returns a valid NimbusPost auth token, fetching/refreshing as needed.

    If NIMBUSPOST_API_KEY is set directly, it's used as-is (some NimbusPost
    accounts issue a static token). Otherwise falls back to email+password
    login against /users/login to obtain a JWT, cached until near-expiry.
    """
    if NIMBUSPOST_API_KEY:
        return NIMBUSPOST_API_KEY

    if not (NIMBUSPOST_EMAIL and NIMBUSPOST_PASSWORD):
        logger.warning("NimbusPost not configured (no API key or email/password)")
        return None

    now = time.time()
    if _token_cache["token"] and (now - _token_cache["fetched_at"]) < _TOKEN_TTL_SECONDS:
        return _token_cache["token"]

    try:
        resp = requests.post(
            f"{NIMBUSPOST_BASE_URL}/users/login",
            json={"email": NIMBUSPOST_EMAIL, "password": NIMBUSPOST_PASSWORD},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("data")
        if not token:
            logger.error(f"NimbusPost login returned no token: {data}", exc_info=True)
            return None
        _token_cache["token"] = token
        _token_cache["fetched_at"] = now
        logger.info("NimbusPost auth token refreshed")
        return token
    except Exception as e:
        logger.error(f"NimbusPost login failed: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return None


def _headers() -> dict | None:
    token = get_auth_token()
    if not token:
        return None
    return {"Content-Type": "application/json", "token": token}


def register_pickup_address(shopkeeper: dict) -> str | None:
    """
    Registers a shopkeeper's address as a NimbusPost pickup location.
    Returns the pickup_id on success, or None on failure / not configured.
    """
    if not _is_configured():
        logger.warning("NimbusPost not configured — skipping pickup address registration")
        return None

    headers = _headers()
    if not headers:
        return None

    payload = {
        "warehouse_name": f"Clothyk - {shopkeeper.get('shop_name', '')}",
        "name": shopkeeper.get("shopkeeper_name", ""),
        "email": "clothyk@gmail.com",
        "phone": shopkeeper.get("contact", ""),
        "address": shopkeeper.get("address", ""),
        "pincode": shopkeeper.get("pincode", ""),
        "city": shopkeeper.get("city", ""),
        "state": shopkeeper.get("state", ""),
    }

    try:
        resp = requests.post(
            f"{NIMBUSPOST_BASE_URL}/client/pickupaddress",
            json=payload,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        pickup_id = data.get("data", {}).get("pickup_id") if isinstance(data.get("data"), dict) else data.get("pickup_id")
        if not pickup_id:
            logger.error(f"NimbusPost pickup address registration returned no pickup_id: {data}", exc_info=True)
            return None
        logger.info(f"NimbusPost pickup address registered: {shopkeeper.get('shop_name')} -> {pickup_id}")
        return pickup_id
    except Exception as e:
        logger.error(f"NimbusPost pickup address registration failed for {shopkeeper.get('shop_name')}: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return None


def create_shipment(order: dict, shopkeeper: dict) -> dict | None:
    """
    Creates a shipment on NimbusPost for a confirmed order.

    `order` should include: id, customer_name, customer_phone,
    customer_address, customer_city, customer_pincode, product_name,
    payment_type, our_price, shopkeeper_code.

    `shopkeeper` should include: shop_name, shopkeeper_name, address,
    pincode, city, state, contact.

    Returns {"awb": ..., "courier_name": ..., "label_url": ..., "shipment_id": ...}
    on success, or None on failure / not configured.
    """
    if not _is_configured():
        logger.warning("NimbusPost not configured — cannot create shipment")
        return None

    headers = _headers()
    if not headers:
        return None

    payment_type = "cod" if order.get("payment_type") == "cod" else "prepaid"
    order_amount = order.get("our_price", 0) if payment_type == "cod" else 0

    payload = {
        "order_number": str(order.get("id", "")),
        "payment_type": payment_type,
        "order_amount": order.get("our_price", 0),
        "package_weight": 500,
        "package_length": 30,
        "package_breadth": 25,
        "package_height": 5,
        "consignee": {
            "name": order.get("customer_name", ""),
            "address": order.get("customer_address", ""),
            "city": order.get("customer_city", ""),
            "state": "",
            "pincode": order.get("customer_pincode", ""),
            "phone": order.get("customer_phone", ""),
        },
        "pickup": {
            "warehouse_name": f"Clothyk - {shopkeeper.get('shop_name', '')}",
            "name": shopkeeper.get("shopkeeper_name", ""),
            "address": shopkeeper.get("address", ""),
            "pincode": shopkeeper.get("pincode", ""),
            "city": shopkeeper.get("city", ""),
            "state": shopkeeper.get("state", ""),
            "phone": shopkeeper.get("contact", ""),
        },
        "products": [{
            "name": order.get("product_name", ""),
            "qty": 1,
            "price": order.get("our_price", 0),
            "sku": order.get("shopkeeper_code", ""),
        }],
    }

    try:
        resp = requests.post(
            f"{NIMBUSPOST_BASE_URL}/shipment",
            json=payload,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        body = data.get("data", data)
        awb = body.get("awb_number") or body.get("awb")
        if not awb:
            logger.error(f"NimbusPost shipment creation returned no AWB for order {order.get('id')}: {data}", exc_info=True)
            return None

        result = {
            "awb": awb,
            "courier_name": body.get("courier_name", ""),
            "label_url": body.get("label_url", ""),
            "shipment_id": str(body.get("shipment_id", "") or body.get("id", "")),
        }
        logger.info(f"NimbusPost shipment created: order {order.get('id')} -> AWB {awb}")
        return result
    except Exception as e:
        logger.error(f"NimbusPost shipment creation failed for order {order.get('id')}: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return None


def track_shipment(awb: str) -> dict | None:
    """Returns {"status": ..., "location": ..., "timestamp": ...} or None."""
    if not _is_configured():
        return None

    headers = _headers()
    if not headers:
        return None

    try:
        resp = requests.get(
            f"{NIMBUSPOST_BASE_URL}/shipment/track/{awb}",
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        body = data.get("data", data)
        if isinstance(body, list):
            body = body[0] if body else {}
        return {
            "status": body.get("current_status", body.get("status", "")),
            "location": body.get("location", ""),
            "timestamp": body.get("timestamp", ""),
        }
    except Exception as e:
        logger.error(f"NimbusPost tracking failed for AWB {awb}: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return None


def cancel_shipment(awb: str) -> bool:
    """Cancels a shipment on NimbusPost. Returns True on success."""
    if not _is_configured():
        return False

    headers = _headers()
    if not headers:
        return False

    try:
        resp = requests.post(
            f"{NIMBUSPOST_BASE_URL}/shipment/cancel",
            json={"awbs": [awb]},
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info(f"NimbusPost shipment cancelled: AWB {awb}")
        return True
    except Exception as e:
        logger.error(f"NimbusPost shipment cancellation failed for AWB {awb}: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return False