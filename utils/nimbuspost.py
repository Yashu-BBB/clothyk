"""
NimbusPost courier integration for clovical.

Handles pickup-address registration, shipment creation, tracking, and
cancellation via the NimbusPost API (https://api.nimbuspost.com/v1).

IMPORTANT — NimbusPost actually has TWO separate auth systems for
different endpoint groups (confirmed from their own public docs):

  1. Static key system ("Old API" in the NimbusPost dashboard, under
     Settings → API → "Reset API Key"). Sent as header:
         NP-API-KEY: {NIMBUSPOST_API_KEY}
     This covers Orders and Warehouse (pickup address) endpoints.

  2. Email+password login system ("New API" in the dashboard, under
     Settings → API → "Generate Api User Credentials"). You log in once
     via /users/login to get a JWT, then send it as:
         Authorization: Bearer {token}
     This covers Couriers, Shipment create/cancel, Tracking, NDR, Manifest.

So this module uses BOTH: register_pickup_address() uses the static
NP-API-KEY header, everything else uses the Bearer token. Both
NIMBUSPOST_API_KEY and NIMBUSPOST_EMAIL/PASSWORD should be set for full
functionality.

Every function degrades gracefully (returns None / False) when the
relevant credentials aren't configured, or when the API call fails for
any reason — NimbusPost is never allowed to block the core order flow.
All failures are logged and reported to Sentry if configured.
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

# The verified support email+phone NimbusPost requires on the pickup/warehouse
# for both the warehouse-registration call AND every shipment-creation call.
# NimbusPost rejects shipment creation with "Support email and phone number
# on the shipping label must be provided and OTP-verified to proceed." if
# these aren't set to a value that's been OTP-verified as a "support
# contact" on your NimbusPost account (NimbusPost dashboard → Settings →
# Company Profile / KYC — look for "Support Email"/"Support Phone" and
# verify each via the OTP they send) — this is an account-side step on
# NimbusPost's platform, not something fixable from this code alone.
#
# Previously only NIMBUSPOST_SUPPORT_EMAIL existed here, and the phone sent
# in the shipment payload was the *shopkeeper's own* contact number — but
# NimbusPost's "Support ... must be OTP-verified" check is against your
# platform's verified support contact, not an individual shopkeeper's
# personal number, so that phone was never going to pass verification.
NIMBUSPOST_SUPPORT_EMAIL = os.getenv("NIMBUSPOST_SUPPORT_EMAIL", "eclothyk@gmail.com")
NIMBUSPOST_SUPPORT_PHONE = os.getenv("NIMBUSPOST_SUPPORT_PHONE", "")

_REQUEST_TIMEOUT = 15

# ─── Token cache (in-process, refreshed ~1h before 24h expiry) ────────────
_token_cache: dict = {"token": None, "fetched_at": 0}
_TOKEN_TTL_SECONDS = 23 * 3600  # refresh a bit before the real 24h expiry


def _bearer_configured() -> bool:
    """Shipment/tracking/courier endpoints need email+password login."""
    return bool(NIMBUSPOST_EMAIL and NIMBUSPOST_PASSWORD)


def _static_configured() -> bool:
    """Pickup address / warehouse endpoints need the static NP-API-KEY."""
    return bool(NIMBUSPOST_API_KEY)


def _is_configured() -> bool:
    """True if either auth method is set up (used for generic status checks)."""
    return _bearer_configured() or _static_configured()


def is_configured() -> bool:
    """
    Public helper: True if NimbusPost has at least one auth method set up.
    Use this (instead of re-checking os.getenv(...) elsewhere) anywhere
    that needs to know whether to attempt a NimbusPost call at all —
    e.g. the admin "Create Shipment" endpoint and the Orders page's
    server-rendered button state. Logs a warning when not configured so
    it's obvious in the logs why shipment actions are being skipped.
    """
    if not _is_configured():
        logger.warning(
            "NimbusPost not configured — set NIMBUSPOST_API_KEY and/or "
            "NIMBUSPOST_EMAIL + NIMBUSPOST_PASSWORD to enable shipping"
        )
        return False
    return True


def get_auth_token() -> str | None:
    """
    Returns a valid NimbusPost Bearer token for shipment/tracking/courier
    endpoints, fetching/refreshing via email+password login as needed.
    (This is separate from NIMBUSPOST_API_KEY, which is a different static
    key used only for pickup address / warehouse endpoints — see
    _static_headers().)
    """
    if not _bearer_configured():
        logger.warning("NimbusPost Bearer auth not configured (no email/password)")
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
        # NimbusPost's exact field name for the token isn't confirmed from the
        # public docs, so check the common possibilities defensively.
        token = data.get("data") or data.get("token") or (
            data.get("data", {}).get("token") if isinstance(data.get("data"), dict) else None
        )
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


def _bearer_headers() -> dict | None:
    token = get_auth_token()
    if not token:
        return None
    return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}


def _static_headers() -> dict | None:
    if not _static_configured():
        logger.warning("NimbusPost static API key (NIMBUSPOST_API_KEY) not configured")
        return None
    return {"Content-Type": "application/json", "NP-API-KEY": NIMBUSPOST_API_KEY}


def get_couriers() -> dict:
    """
    Diagnostic helper: calls NimbusPost's courier-list endpoint using the
    Bearer token to confirm auth is working end-to-end, independent of the
    pickup-address/warehouse call. Tries the two most likely path variants
    since the exact path isn't confirmed from public docs, and returns
    full diagnostic info either way (not just success/failure) so it's
    useful for debugging regardless of which path is correct.
    """
    headers = _bearer_headers()
    if not headers:
        return {"ok": False, "error": "Bearer auth not configured or login failed"}

    for path in ("/courier", "/couriers"):
        try:
            resp = requests.get(f"{NIMBUSPOST_BASE_URL}{path}", headers=headers, timeout=_REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return {"ok": True, "path_used": path, "status_code": 200, "body": resp.json()}
            last_result = {"ok": False, "path_tried": path, "status_code": resp.status_code, "body": resp.text[:500]}
        except Exception as e:
            last_result = {"ok": False, "path_tried": path, "error": str(e)}
    return last_result


def register_pickup_address(shopkeeper: dict) -> str | None:
    """
    Registers a shopkeeper's address as a NimbusPost pickup location
    (NimbusPost calls this a "warehouse"). Returns the pickup_id on
    success, or None on failure / not configured.

    Uses the static NP-API-KEY auth system (NIMBUSPOST_API_KEY), NOT the
    Bearer token — this endpoint lives under NimbusPost's "Old API"
    (Settings → API → Reset API Key in their dashboard), separate from
    the shipment/tracking endpoints below.

    Note: the exact endpoint path below is our best-supported guess from
    the original integration spec; NimbusPost's rendered API docs (behind
    JS) couldn't be fully verified. If this 404s (vs 403), the path
    itself may need adjusting — check NimbusPost's Old API doc directly.
    """
    if not _static_configured():
        logger.warning("NimbusPost static API key not configured — skipping pickup address registration")
        return None

    headers = _static_headers()
    if not headers:
        return None

    payload = {
        "warehouse_name": f"clovical - {shopkeeper.get('shop_name', '')}",
        "name": shopkeeper.get("shopkeeper_name", ""),
        "email": NIMBUSPOST_SUPPORT_EMAIL,
        # NimbusPost's OTP-verification check is against the platform's own
        # verified support phone (NIMBUSPOST_SUPPORT_PHONE), not the
        # shopkeeper's personal number. Fall back to the shopkeeper's
        # contact only if no support phone is configured, so this never
        # sends a blank phone — but expect that fallback to still fail
        # NimbusPost's verification check the same way the email did.
        "phone": NIMBUSPOST_SUPPORT_PHONE or shopkeeper.get("contact", ""),
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
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            logger.error(
                f"NimbusPost pickup address registration got 403 Forbidden for {shopkeeper.get('shop_name')} "
                f"— this usually means the NimbusPost account isn't yet activated for API warehouse "
                f"registration, or needs a wallet top-up. Contact tech@nimbuspost.com if this persists.",
                exc_info=True,
            )
        else:
            logger.error(f"NimbusPost pickup address registration failed for {shopkeeper.get('shop_name')}: {e}", exc_info=True)
        sentry_sdk.capture_exception(e)
        return None
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
    if not _bearer_configured():
        logger.warning("NimbusPost Bearer auth not configured — cannot create shipment")
        return None

    headers = _bearer_headers()
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
            "warehouse_name": f"clovical - {shopkeeper.get('shop_name', '')}",
            "name": shopkeeper.get("shopkeeper_name", ""),
            "email": NIMBUSPOST_SUPPORT_EMAIL,
            "address": shopkeeper.get("address", ""),
            "pincode": shopkeeper.get("pincode", ""),
            "city": shopkeeper.get("city", ""),
            "state": shopkeeper.get("state", ""),
            # Same reasoning as register_pickup_address(): NimbusPost wants
            # the verified support phone here, not the shopkeeper's own.
            "phone": NIMBUSPOST_SUPPORT_PHONE or shopkeeper.get("contact", ""),
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
            f"{NIMBUSPOST_BASE_URL}/shipments",
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
    if not _bearer_configured():
        return None

    headers = _bearer_headers()
    if not headers:
        return None

    try:
        resp = requests.get(
            f"{NIMBUSPOST_BASE_URL}/shipments/track/{awb}",
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
    if not _bearer_configured():
        return False

    headers = _bearer_headers()
    if not headers:
        return False

    try:
        resp = requests.post(
            f"{NIMBUSPOST_BASE_URL}/shipments/cancel",
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