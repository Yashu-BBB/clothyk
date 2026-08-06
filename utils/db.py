import os
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from supabase import create_client, Client

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # service role key

supabase_admin: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── Blocking-call escape hatch ─────────────────────────────────────────────
# supabase-py's client is synchronous. Calling `.execute()` directly inside an
# `async def` route would block the single event loop for the entire DB round
# trip (100-400ms), serializing every other in-flight request behind it.
#
# `run_query()` pushes the blocking `.execute()` call onto a dedicated thread
# pool instead, so the event loop stays free to handle other requests while
# Supabase's HTTP call is in flight. Use it everywhere a Supabase query is
# executed from inside an async function:
#
#   res = await run_query(supabase_admin.table("orders").select("*").eq("id", order_id))
#
# A dedicated pool (rather than the default asyncio executor, which caps at
# ~32 workers and is also used for other blocking work) keeps DB concurrency
# predictable and independent from anything else that happens to use
# asyncio.to_thread elsewhere in the app.
_DB_THREAD_POOL = ThreadPoolExecutor(max_workers=20, thread_name_prefix="supabase-db")


async def run_query(query):
    """Executes a not-yet-executed Supabase query builder object in a worker
    thread and returns its result (same object `.execute()` would return).
    Never call `.execute()` yourself before passing the query in here."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_DB_THREAD_POOL, query.execute)


async def run_blocking(fn, *args, **kwargs):
    """General-purpose helper for any other blocking call (e.g. a `requests`
    call to NimbusPost/WhatsApp) that needs to run off the event loop from
    inside an async function."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_DB_THREAD_POOL, lambda: fn(*args, **kwargs))