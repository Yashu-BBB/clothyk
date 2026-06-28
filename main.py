from dotenv import load_dotenv
load_dotenv()  
import logging
import time
import hashlib
import sentry_sdk
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import redis.asyncio as aioredis
import os

from routers import auth, products, orders, shopkeepers, admin, whatsapp, analytics, public, categories
from utils.db import supabase_admin
from utils.cache import init_redis, close_redis

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── Sentry ────────────────────────────────────────────────────────────────
SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.1)

# ─── Rate Limiter ──────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ─── Blocked IPs (in-memory, reset on restart) ────────────────────────────
blocked_ips: dict[str, float] = {}
failed_attempts: dict[str, int] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_redis()
    logger.info("App started successfully")
    yield
    await close_redis()

app = FastAPI(title="Clothyk", lifespan=lifespan, docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Middleware: Visitor Tracking + Bot Protection ─────────────────────────
@app.middleware("http")
async def track_and_protect(request: Request, call_next):
    client_ip = request.headers.get("CF-Connecting-IP") or get_remote_address(request)
    ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()

    # Block IPs
    if client_ip in blocked_ips:
        if time.time() - blocked_ips[client_ip] < 900:  # 15 min block
            return JSONResponse({"detail": "Too many failed attempts"}, status_code=429)
        else:
            del blocked_ips[client_ip]
            if client_ip in failed_attempts:
                del failed_attempts[client_ip]

    # Log visitors (only for page routes, not static/api)
    path = request.url.path
    if not path.startswith("/static") and not path.startswith("/api"):
        try:
            supabase_admin.table("visitors").insert({
                "page": path,
                "ip_hash": ip_hash
            }).execute()
        except Exception:
            pass

    start = time.time()
    response = await call_next(request)
    duration = time.time() - start

    if duration > 2:
        logger.warning(f"Slow Supabase/response: {path} took {duration:.2f}s")

    return response

# ─── Static Files & Templates ──────────────────────────────────────────────
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ─── Routers ──────────────────────────────────────────────────────────────
app.include_router(public.router)
app.include_router(auth.router, prefix="/api/auth")
app.include_router(products.router, prefix="/api/products")
app.include_router(orders.router, prefix="/api/orders")
app.include_router(shopkeepers.router, prefix="/api/shopkeepers")
app.include_router(admin.router, prefix="/api/admin")
app.include_router(whatsapp.router, prefix="/api/whatsapp")
app.include_router(analytics.router, prefix="/api/analytics")
app.include_router(categories.router, prefix="/api/categories")

# Expose blocked_ips and failed_attempts globally for auth router
app.state.blocked_ips = blocked_ips
app.state.failed_attempts = failed_attempts
