import time
import logging
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from utils.db import supabase_admin
from utils.auth_utils import verify_password, create_token

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_ATTEMPTS = 3
BLOCK_DURATION = 900  # 15 minutes


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(req: LoginRequest, request: Request):
    client_ip = request.headers.get("CF-Connecting-IP") or request.client.host
    app_state = request.app.state

    # Check if IP is blocked
    if client_ip in app_state.blocked_ips:
        if time.time() - app_state.blocked_ips[client_ip] < BLOCK_DURATION:
            logger.warning(f"Admin login blocked IP: {client_ip}")
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try later.")
        else:
            del app_state.blocked_ips[client_ip]
            app_state.failed_attempts.pop(client_ip, None)

    try:
        res = supabase_admin.table("admins").select("*").eq("username", req.username).single().execute()
    except Exception:
        res = None

    if not res or not res.data:
        # Track failed attempt
        app_state.failed_attempts[client_ip] = app_state.failed_attempts.get(client_ip, 0) + 1
        if app_state.failed_attempts[client_ip] >= MAX_ATTEMPTS:
            app_state.blocked_ips[client_ip] = time.time()
            logger.warning(f"Admin login: IP {client_ip} blocked after {MAX_ATTEMPTS} failed attempts")
        logger.warning(f"Admin login failed: username={req.username}, IP={client_ip}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    admin = res.data
    if not verify_password(req.password, admin["password"]):
        app_state.failed_attempts[client_ip] = app_state.failed_attempts.get(client_ip, 0) + 1
        if app_state.failed_attempts[client_ip] >= MAX_ATTEMPTS:
            app_state.blocked_ips[client_ip] = time.time()
            logger.warning(f"Admin login: IP {client_ip} blocked")
        logger.warning(f"Admin login failed: username={req.username}, IP={client_ip}")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Clear failed attempts on success
    app_state.failed_attempts.pop(client_ip, None)
    logger.info(f"Admin logged in: {req.username}")
    token = create_token(req.username)

    response = JSONResponse({"success": True})
    response.set_cookie(
        "admin_token", token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=14400
    )
    return response


@router.post("/logout")
async def logout():
    response = JSONResponse({"success": True})
    response.delete_cookie("admin_token")
    return response
