import logging
from fastapi import APIRouter, Request, Depends, HTTPException, UploadFile, File, Form, Query
from pydantic import BaseModel
from typing import Optional, List
import json
import uuid

from utils.db import supabase_admin
from utils.cache import (
    cache_get, cache_set, cache_clear_pattern,
    two_layer_get, two_layer_set, two_layer_clear_pattern, mem_clear_pattern,
)
from utils.auth_utils import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()

SAFE_FIELDS = "id,name,description,our_price,mrp,sizes,colors,image,images,category,gender,featured,stock,shopkeeper_code,view_count,created_at,size_chart"


# ─── PUBLIC ENDPOINTS ─────────────────────────────────────────────────────

@router.get("/")
async def list_products(
    category: Optional[str] = None,
    search: Optional[str] = None,      # searches name, color, size, category, shopkeeper code
    sort: Optional[str] = None,
    featured: Optional[bool] = None,
    gender: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    sizes: Optional[str] = None,        # comma separated e.g. "S,M,XL"
    colors: Optional[str] = None,       # comma separated e.g. "Red,Black"
    on_sale: Optional[bool] = None,     # only discounted products
):
    cache_key = f"products:list:{category}:{search}:{sort}:{featured}:{gender}:{min_price}:{max_price}:{sizes}:{colors}:{on_sale}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    try:
        query = supabase_admin.table("products").select(SAFE_FIELDS).gt("stock", 0)
        if category:
            query = query.eq("category", category)
        if featured is not None:
            query = query.eq("featured", featured)
        if gender:
            query = query.eq("gender", gender)

        if search:
            # name / category / shopkeeper_code text match, or search term present in the sizes/colors JSONB arrays
            safe_search = search.replace('"', '')
            query = query.or_(
                f'name.ilike.%{safe_search}%,'
                f'category.ilike.%{safe_search}%,'
                f'shopkeeper_code.ilike.%{safe_search}%,'
                f'colors.cs.["{safe_search}"],'
                f'sizes.cs.["{safe_search}"]'
            )

        if min_price is not None:
            query = query.gte("our_price", min_price)
        if max_price is not None:
            query = query.lte("our_price", max_price)

        if sizes:
            size_list = [s.strip() for s in sizes.split(",") if s.strip()]
            if size_list:
                or_clause = ",".join(f'sizes.cs.["{s}"]' for s in size_list)
                query = query.or_(or_clause)

        if colors:
            color_list = [c.strip() for c in colors.split(",") if c.strip()]
            if color_list:
                or_clause = ",".join(f'colors.cs.["{c}"]' for c in color_list)
                query = query.or_(or_clause)

        if on_sale:
            query = query.not_.is_("mrp", "null")

        if sort == "price_asc":
            query = query.order("our_price", desc=False)
        elif sort == "price_desc":
            query = query.order("our_price", desc=True)
        elif sort == "discount":
            query = query.not_.is_("mrp", "null").order("mrp", desc=True)
        else:
            query = query.order("created_at", desc=True)

        res = query.execute()
        data = res.data or []
        await cache_set(cache_key, data, ttl=900)
        return data
    except Exception as e:
        logger.error(f"Failed to fetch products: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch products")


@router.get("/filter-options")
async def get_filter_options(gender: Optional[str] = None):
    cache_key = f"products:filter-options:{gender}"
    cached = await two_layer_get(cache_key)
    if cached:
        return cached
    try:
        query = supabase_admin.table("products").select("sizes,colors,our_price").gt("stock", 0)
        if gender:
            query = query.eq("gender", gender)
        res = query.execute()
        rows = res.data or []

        size_set, color_set = set(), set()
        prices = []
        for row in rows:
            for s in (row.get("sizes") or []):
                size_set.add(s)
            for c in (row.get("colors") or []):
                color_set.add(c)
            if row.get("our_price") is not None:
                prices.append(row["our_price"])

        result = {
            "sizes": sorted(size_set),
            "colors": sorted(color_set),
            "price_range": {
                "min": min(prices) if prices else 0,
                "max": max(prices) if prices else 0,
            },
        }
        await two_layer_set(cache_key, result, redis_ttl=900, mem_ttl=120)
        return result
    except Exception as e:
        logger.error(f"Failed to fetch filter options: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch filter options")


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
        logger.error(f"Failed to fetch featured products: {e}", exc_info=True)
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
        logger.error(f"Failed to fetch categories: {e}", exc_info=True)
        return []


@router.get("/{product_id}")
async def get_product(product_id: str):
    cache_key = f"product:{product_id}"
    try:
        cached = await two_layer_get(cache_key)
        if cached:
            # View count still increments on every request, cache hit or miss.
            # Read the live count rather than the cached one so concurrent
            # cache hits don't clobber each other's increments.
            try:
                live = supabase_admin.table("products").select("view_count").eq("id", product_id).single().execute()
                if live.data:
                    new_count = live.data["view_count"] + 1
                    supabase_admin.table("products").update({"view_count": new_count}).eq("id", product_id).execute()
                    cached = {**cached, "view_count": new_count}
                    logger.info(f"Product view incremented (cache hit): {product_id}")
            except Exception as e:
                logger.warning(f"View count increment failed for {product_id}: {e}")
            return cached

        res = supabase_admin.table("products").select(SAFE_FIELDS).eq("id", product_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Product not found")
        supabase_admin.table("products").update({"view_count": res.data["view_count"] + 1}).eq("id", product_id).execute()
        logger.info(f"Product view incremented: {product_id}")
        await two_layer_set(cache_key, res.data, redis_ttl=900, mem_ttl=120)
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch product {product_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to fetch product")


@router.get("/{product_id}/related")
async def related_products(product_id: str):
    cache_key = f"product:{product_id}:related"
    try:
        cached = await cache_get(cache_key)
        if cached:
            return cached

        prod = supabase_admin.table("products").select("category,gender").eq("id", product_id).single().execute()
        if not prod.data:
            return []
        q = supabase_admin.table("products").select(SAFE_FIELDS).eq("category", prod.data["category"]).neq("id", product_id).gt("stock", 0).limit(4)
        if prod.data.get("gender"):
            q = q.eq("gender", prod.data["gender"])
        res = q.execute()
        data = res.data or []
        await cache_set(cache_key, data, ttl=900)
        return data
    except Exception as e:
        logger.error(f"Failed related products: {e}", exc_info=True)
        return []


# ─── ADMIN ENDPOINTS ──────────────────────────────────────────────────────

@router.get("/admin/all")
async def admin_list_products(admin=Depends(require_admin)):
    try:
        res = supabase_admin.table("products").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"Admin: failed to list products: {e}", exc_info=True)
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
    gender: str = Form("Girls"),
    featured: bool = Form(False),
    stock: int = Form(1),
    mrp: float = Form(None),
    shopkeeper_id: int = Form(...),
    size_chart: str = Form(None),
    clear_size_chart: str = Form("false"),
    images: List[UploadFile] = File(default=[]),
    admin=Depends(require_admin)
):
    try:
        sk = supabase_admin.table("shopkeepers").select("id").eq("id", shopkeeper_id).single().execute()
        if not sk.data:
            raise HTTPException(status_code=404, detail="Shopkeeper not found")
        shopkeeper_code = f"#{sk.data['id']:03d}"

        # Upload each image (max 6) to Supabase storage
        image_urls = []
        valid_images = [img for img in images if img and img.filename]
        for img in valid_images[:6]:
            contents = await img.read()
            ext = img.filename.rsplit(".", 1)[-1].lower()
            fname = f"{uuid.uuid4()}.{ext}"
            supabase_admin.storage.from_("product-images").upload(
                fname, contents, {"content-type": img.content_type or "image/jpeg"}
            )
            url = supabase_admin.storage.from_("product-images").get_public_url(fname)
            image_urls.append(url)

        # First image = primary (backward compat)
        primary_image = image_urls[0] if image_urls else None

        # Parse size chart
        parsed_size_chart = None
        if size_chart and size_chart.strip() and clear_size_chart != "true":
            try:
                parsed_size_chart = json.loads(size_chart)
            except Exception:
                parsed_size_chart = None

        product = {
            "name": name,
            "description": description,
            "our_price": our_price,
            "shopkeeper_price": shopkeeper_price,
            "sizes": json.loads(sizes) if isinstance(sizes, str) else sizes,
            "colors": json.loads(colors) if isinstance(colors, str) else colors,
            "category": category,
            "gender": gender,
            "mrp": mrp if mrp else None,
            "featured": featured,
            "stock": stock,
            "shopkeeper_id": shopkeeper_id,
            "shopkeeper_code": shopkeeper_code,
            "image": primary_image,
            "images": image_urls,
            "size_chart": parsed_size_chart,
        }
        res = supabase_admin.table("products").insert(product).execute()
        await cache_clear_pattern("products:*")
        await two_layer_clear_pattern("products:filter-options:")
        mem_clear_pattern("product:")
        logger.info(f"Product added: {name} by admin {admin['sub']}")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to add product: {e}", exc_info=True)
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
    gender: str = Form(None),
    featured: bool = Form(None),
    stock: int = Form(None),
    mrp: float = Form(None),
    shopkeeper_id: int = Form(None),
    size_chart: str = Form(None),
    clear_size_chart: str = Form("false"),
    keep_images: str = Form("[]"),       # JSON array of existing image URLs to keep
    new_images: List[UploadFile] = File(default=[]),   # newly uploaded images
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
        if gender is not None: updates["gender"] = gender
        if mrp is not None: updates["mrp"] = mrp if mrp > 0 else None
        if featured is not None: updates["featured"] = featured
        if stock is not None: updates["stock"] = stock
        if shopkeeper_id is not None:
            updates["shopkeeper_id"] = shopkeeper_id
            updates["shopkeeper_code"] = f"#{shopkeeper_id:03d}"

        # Size chart
        if clear_size_chart == "true":
            updates["size_chart"] = None
        elif size_chart and size_chart.strip():
            try:
                updates["size_chart"] = json.loads(size_chart)
            except Exception:
                pass

        # Multi-image handling
        existing_urls = []
        try:
            existing_urls = json.loads(keep_images) if keep_images else []
        except Exception:
            existing_urls = []

        # Upload new images
        new_urls = []
        valid_new = [img for img in new_images if img and img.filename]
        slots_remaining = max(0, 6 - len(existing_urls))
        for img in valid_new[:slots_remaining]:
            contents = await img.read()
            ext = img.filename.rsplit(".", 1)[-1].lower()
            fname = f"{uuid.uuid4()}.{ext}"
            supabase_admin.storage.from_("product-images").upload(
                fname, contents, {"content-type": img.content_type or "image/jpeg"}
            )
            url = supabase_admin.storage.from_("product-images").get_public_url(fname)
            new_urls.append(url)

        # Merge and cap at 6
        all_image_urls = (existing_urls + new_urls)[:6]

        # Only update images if the field was explicitly sent (keep_images or new_images present)
        if keep_images != "[]" or valid_new:
            updates["images"] = all_image_urls
            updates["image"] = all_image_urls[0] if all_image_urls else None
        elif valid_new:
            # new images only (no keep_images sent) — append to existing
            if new_urls:
                updates["images"] = new_urls
                updates["image"] = new_urls[0]

        res = supabase_admin.table("products").update(updates).eq("id", product_id).execute()
        await cache_clear_pattern("products:*")
        await two_layer_clear_pattern("products:filter-options:")
        mem_clear_pattern("product:")
        logger.info(f"Product edited: {product_id} by admin {admin['sub']}")
        return res.data[0] if res.data else {}
    except Exception as e:
        logger.error(f"Failed to edit product: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to edit product: {str(e)}")


@router.delete("/admin/{product_id}/image")
async def delete_product_image(
    product_id: str,
    image_url: str = Query(...),
    admin=Depends(require_admin)
):
    """Remove a single image URL from the product's images array."""
    try:
        res = supabase_admin.table("products").select("image,images").eq("id", product_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Product not found")

        current_images = res.data.get("images") or []
        updated_images = [u for u in current_images if u != image_url]
        primary = updated_images[0] if updated_images else None

        supabase_admin.table("products").update({
            "images": updated_images,
            "image": primary
        }).eq("id", product_id).execute()

        await cache_clear_pattern("products:*")
        mem_clear_pattern("product:")
        logger.info(f"Image removed from product {product_id} by admin {admin['sub']}")
        return {"success": True, "images": updated_images}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete product image: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete image")


@router.delete("/admin/{product_id}")
async def delete_product(product_id: str, admin=Depends(require_admin)):
    try:
        prod = supabase_admin.table("products").select("name").eq("id", product_id).single().execute()
        supabase_admin.table("products").delete().eq("id", product_id).execute()
        await cache_clear_pattern("products:*")
        await two_layer_clear_pattern("products:filter-options:")
        mem_clear_pattern("product:")
        logger.info(f"Product deleted: {prod.data.get('name')} by admin {admin['sub']}")
        return {"success": True}
    except Exception as e:
        logger.error(f"Failed to delete product: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete product")