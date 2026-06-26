import logging
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional
import json
import uuid

from utils.db import supabase_admin
from utils.cache import cache_get, cache_set, cache_clear_pattern
from utils.auth_utils import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()

SAFE_FIELDS = "id,name,description,our_price,sizes,colors,image,category,featured,stock,shopkeeper_code,view_count,created_at"


# ─── PUBLIC ENDPOINTS ─────────────────────────────────────────────────────

@router.get("/")
async def list_products(
    category: Optional[str] = None,
    search: Optional[str] = None,
    sort: Optional[str] = None,
    featured: Optional[bool] = None
):
    cache_key = f"products:list:{category}:{search}:{sort}:{featured}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    try:
        query = supabase_admin.table("products").select(SAFE_FIELDS).gt("stock", 0)
        if category:
            query = query.eq("category", category)
        if featured is not None:
            query = query.eq("featured", featured)
        if search:
            query = query.ilike("name", f"%{search}%")
        if sort == "price_asc":
            query = query.order("our_price", desc=False)
        elif sort == "price_desc":
            query = query.order("our_price", desc=True)
        else:
            query = query.order("created_at", desc=True)

        res = query.execute()
        data = res.data or []
        await cache_set(cache_key, data, ttl=900)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch products: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch products")


@router.get("/featured")
async def featured_products():
    cached = await cache_get("products:featured")
    if cached:
        return cached
    try:
        res = supabase_admin.table("products").select(SAFE_FIELDS).eq("featured", True).gt("stock", 0).limit(8).execute()
        data = res.data or []
        await cache_set("products:featured", data, ttl=900)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch featured products: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch products")


@router.get("/categories")
async def list_categories():
    cached = await cache_get("products:categories")
    if cached:
        return cached
    try:
        res = supabase_admin.table("products").select("category").gt("stock", 0).execute()
        cats = list(set(p["category"] for p in (res.data or []) if p.get("category")))
        await cache_set("products:categories", cats, ttl=1800)
        return cats
    except Exception as e:
        logger.error(f"Failed to fetch categories: {e}")
        return []


@router.get("/{product_id}")
async def get_product(product_id: str):
    try:
        res = supabase_admin.table("products").select(SAFE_FIELDS).eq("id", product_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Product not found")

        # Increment view_count
        supabase_admin.table("products").update({"view_count": res.data["view_count"] + 1}).eq("id", product_id).execute()
        logger.info(f"Product view incremented: {product_id}")
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch product {product_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch product")


@router.get("/{product_id}/related")
async def related_products(product_id: str):
    try:
        prod = supabase_admin.table("products").select("category").eq("id", product_id).single().execute()
        if not prod.data:
            return []
        res = supabase_admin.table("products").select(SAFE_FIELDS).eq("category", prod.data["category"]).neq("id", product_id).gt("stock", 0).limit(4).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Failed related products: {e}")
        return []


# ─── ADMIN ENDPOINTS ──────────────────────────────────────────────────────

@router.get("/admin/all")
async def admin_list_products(admin=Depends(require_admin)):
    """Admin view includes shopkeeper_price."""
    try:
        res = supabase_admin.table("products").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Admin: failed to list products: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch products")


@router.post("/admin/add")
async def add_product(
    name: str = Form(...),
    description: str = Form(""),
    our_price: float = Form(...),
    shopkeeper_price: float = Form(...),
    sizes: str = Form("[]"),
    colors: str = Form("[]"),
    category: str = Form(""),
    featured: bool = Form(False),
    stock: int = Form(1),
    shopkeeper_id: int = Form(...),
    image: Optional[UploadFile] = File(None),
    admin=Depends(require_admin)
):
    try:
        # Get shopkeeper code
        sk = supabase_admin.table("shopkeepers").select("id").eq("id", shopkeeper_id).single().execute()
        if not sk.data:
            raise HTTPException(status_code=404, detail="Shopkeeper not found")
        shopkeeper_code = f"#{sk.data['id']:03d}"

        image_url = None
        if image:
            contents = await image.read()
            ext = image.filename.split(".")[-1]
            fname = f"{uuid.uuid4()}.{ext}"
            supabase_admin.storage.from_("product-images").upload(fname, contents, {"content-type": image.content_type})
            image_url = supabase_admin.storage.from_("product-images").get_public_url(fname)

        product = {
            "name": name,
            "description": description,
            "our_price": our_price,
            "shopkeeper_price": shopkeeper_price,
            "sizes": json.loads(sizes) if isinstance(sizes, str) else sizes,
            "colors": json.loads(colors) if isinstance(colors, str) else colors,
            "category": category,
            "featured": featured,
            "stock": stock,
            "shopkeeper_id": shopkeeper_id,
            "shopkeeper_code": shopkeeper_code,
            "image": image_url
        }
        res = supabase_admin.table("products").insert(product).execute()
        await cache_clear_pattern("products:*")
        logger.info(f"Product added: {name} by admin {admin['sub']}")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add product: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to add product: {str(e)}")


@router.put("/admin/{product_id}")
async def edit_product(
    product_id: str,
    name: str = Form(None),
    description: str = Form(None),
    our_price: float = Form(None),
    shopkeeper_price: float = Form(None),
    sizes: str = Form(None),
    colors: str = Form(None),
    category: str = Form(None),
    featured: bool = Form(None),
    stock: int = Form(None),
    shopkeeper_id: int = Form(None),
    image: Optional[UploadFile] = File(None),
    admin=Depends(require_admin)
):
    try:
        updates = {}
        if name is not None: updates["name"] = name
        if description is not None: updates["description"] = description
        if our_price is not None: updates["our_price"] = our_price
        if shopkeeper_price is not None: updates["shopkeeper_price"] = shopkeeper_price
        if sizes is not None: updates["sizes"] = json.loads(sizes)
        if colors is not None: updates["colors"] = json.loads(colors)
        if category is not None: updates["category"] = category
        if featured is not None: updates["featured"] = featured
        if stock is not None: updates["stock"] = stock
        if shopkeeper_id is not None:
            updates["shopkeeper_id"] = shopkeeper_id
            updates["shopkeeper_code"] = f"#{shopkeeper_id:03d}"

        if image:
            contents = await image.read()
            ext = image.filename.split(".")[-1]
            fname = f"{uuid.uuid4()}.{ext}"
            supabase_admin.storage.from_("product-images").upload(fname, contents, {"content-type": image.content_type})
            updates["image"] = supabase_admin.storage.from_("product-images").get_public_url(fname)

        res = supabase_admin.table("products").update(updates).eq("id", product_id).execute()
        await cache_clear_pattern("products:*")
        logger.info(f"Product edited: {product_id} by admin {admin['sub']}")
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"Failed to edit product: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to edit product: {str(e)}")


@router.delete("/admin/{product_id}")
async def delete_product(product_id: str, admin=Depends(require_admin)):
    try:
        prod = supabase_admin.table("products").select("name").eq("id", product_id).single().execute()
        supabase_admin.table("products").delete().eq("id", product_id).execute()
        await cache_clear_pattern("products:*")
        logger.info(f"Product deleted: {prod.data.get('name')} by admin {admin['sub']}")
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to delete product: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete product")
