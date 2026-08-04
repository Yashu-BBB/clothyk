"""
Builds the shopkeeper "package PDF" — a 2-page document:
  Page 1: the product photo, alone (nothing else on the page)
  Page 2: EITHER NimbusPost's own official shipping label (fetched and
          merged completely untouched — never edited, so its barcode/AWB
          stays scannable), OR, when no NimbusPost label is available, a
          simple fallback packing slip generated here.

Physical use: shopkeeper prints double-sided — photo faces the package,
label/details face outward, so nobody mixes up which item is which, and
NimbusPost's real barcode is never at risk of being altered.

Uses reportlab (pure Python, no native deps) for anything we draw
ourselves, and pypdf (also pure Python) to merge pages together.
"""

import io
import logging
import requests
from reportlab.lib.pagesizes import A6
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


def _wrap(text: str, width: int) -> list[str]:
    words = (text or "").split()
    lines, current = [], ""
    for w in words:
        candidate = f"{current} {w}".strip()
        if len(candidate) > width:
            lines.append(current)
            current = w
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def generate_photo_page_pdf(image_url: str) -> bytes | None:
    """Single page containing just the product photo, centered and scaled
    to fit. Returns None if the image can't be fetched (caller should treat
    that as non-fatal and fall back to sending without a photo page)."""
    try:
        resp = requests.get(image_url, timeout=10)
        resp.raise_for_status()
        img = ImageReader(io.BytesIO(resp.content))
        iw, ih = img.getSize()
    except Exception as e:
        logger.warning(f"Could not fetch product image for PDF: {e}")
        return None

    buf = io.BytesIO()
    width, height = A6
    c = canvas.Canvas(buf, pagesize=A6)

    margin = 6 * mm
    max_w = width - 2 * margin
    max_h = height - 2 * margin
    scale = min(max_w / iw, max_h / ih)
    draw_w, draw_h = iw * scale, ih * scale
    x = (width - draw_w) / 2
    y = (height - draw_h) / 2

    c.drawImage(img, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
    c.showPage()
    c.save()
    return buf.getvalue()


def generate_details_page_pdf(order: dict, shopkeeper: dict) -> bytes:
    """Single-page fallback packing slip — used only when no NimbusPost
    label is available for this order."""
    buf = io.BytesIO()
    width, height = A6
    c = canvas.Canvas(buf, pagesize=A6)

    margin = 8 * mm
    y = height - margin

    def line(text, size=10, bold=False, gap=6 * mm, color="#000000"):
        nonlocal y
        c.setFillColor(HexColor(color))
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(margin, y, text)
        y -= gap

    line("clovical — PACKING SLIP", size=13, bold=True, gap=8 * mm)
    c.setStrokeColor(HexColor("#cccccc"))
    c.line(margin, y, width - margin, y)
    y -= 6 * mm

    line("FROM:", size=9, bold=True, gap=5 * mm, color="#555555")
    line(shopkeeper.get("shop_name") or "Shop", size=11, bold=True)
    if shopkeeper.get("address"):
        for chunk in _wrap(shopkeeper["address"], 34):
            line(chunk, size=9)
    y -= 3 * mm

    line("SHIP TO:", size=9, bold=True, gap=5 * mm, color="#555555")
    line(order.get("customer_name") or "Customer", size=12, bold=True)
    for chunk in _wrap(order.get("customer_address") or "", 34):
        line(chunk, size=10)
    line(f"{order.get('customer_city') or ''} - {order.get('customer_pincode') or ''}", size=10)
    line(f"Ph: {order.get('customer_phone') or ''}", size=10)
    y -= 3 * mm

    c.line(margin, y, width - margin, y)
    y -= 6 * mm

    line("ORDER DETAILS:", size=9, bold=True, gap=5 * mm, color="#555555")
    line(f"Product: {order.get('product_name') or ''}", size=10)
    line(f"Size: {order.get('size') or '-'}   Color: {order.get('color') or '-'}", size=10)
    line(f"Order ID: {str(order.get('id') or '')[:8]}", size=9, color="#555555")
    line(f"Payment: {(order.get('payment_type') or '').upper() or '-'}", size=9, color="#555555")

    c.showPage()
    c.save()
    return buf.getvalue()


def merge_pdfs(pdf_byte_chunks: list[bytes]) -> bytes:
    """Merges multiple PDFs (each may be single or multi-page) into one,
    in the given order. Pages from each source PDF are copied as-is —
    never redrawn or altered — so an official label's barcode stays intact."""
    writer = PdfWriter()
    for chunk in pdf_byte_chunks:
        if not chunk:
            continue
        reader = PdfReader(io.BytesIO(chunk))
        for page in reader.pages:
            writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def build_shopkeeper_package_pdf(order: dict, shopkeeper: dict, nimbuspost_label_bytes: bytes | None) -> bytes:
    """
    Builds the final 2-page (or 1-page, if photo unavailable) PDF to send
    the shopkeeper: photo page first, then either NimbusPost's untouched
    label or our own fallback details slip.
    """
    pages = []

    photo_page = None
    if order.get("product_image"):
        photo_page = generate_photo_page_pdf(order["product_image"])
    if photo_page:
        pages.append(photo_page)
    else:
        logger.warning(f"No product photo available for order {order.get('id')} — sending details-only PDF")

    if nimbuspost_label_bytes:
        pages.append(nimbuspost_label_bytes)
    else:
        pages.append(generate_details_page_pdf(order, shopkeeper))

    return merge_pdfs(pages)