import os
import logging
import requests

logger = logging.getLogger(__name__)

TURNSTILE_SECRET = os.getenv("CLOUDFLARE_TURNSTILE_SECRET", "")

if not TURNSTILE_SECRET:
    logger.critical(
        "⚠️ SECURITY WARNING: CLOUDFLARE_TURNSTILE_SECRET is not set! "
        "Captcha verification is DISABLED and all orders will bypass it. "
        "Set CLOUDFLARE_TURNSTILE_SECRET in your environment variables immediately "
        "if this is a production environment."
    )


def verify_turnstile(token: str, ip: str = "") -> bool:
    if not TURNSTILE_SECRET:
        # Secret not configured at all — dev/test bypass. Already logged
        # loudly at import time above so misconfiguration in production
        # can't go unnoticed.
        return True
    if not token:
        logger.warning(f"Captcha token missing from request, IP: {ip}")
        return False
    try:
        r = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip},
            timeout=5
        )
        data = r.json()
        if not data.get("success"):
            logger.warning(f"Failed captcha attempt from IP: {ip}")
            return False
        return True
    except Exception as e:
        logger.error(f"Turnstile verification error: {e}", exc_info=True)
        return False