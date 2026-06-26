import os
import logging
import requests

logger = logging.getLogger(__name__)

TURNSTILE_SECRET = os.getenv("CLOUDFLARE_TURNSTILE_SECRET", "")


def verify_turnstile(token: str, ip: str = "") -> bool:
    if not TURNSTILE_SECRET or not token:
        logger.warning("Turnstile secret or token missing — skipping in dev mode")
        return True  # allow in dev/test if not configured
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
        logger.error(f"Turnstile verification error: {e}")
        return False
