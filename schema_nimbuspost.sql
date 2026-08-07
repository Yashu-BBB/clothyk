-- ═══════════════════════════════════════════════════════════════════
-- clovical SCHEMA UPDATE — NimbusPost shipping + shopkeeper address
--   + delivery fee + package-PDF tracking
-- Run in Supabase SQL Editor
--
-- Every statement below is additive and idempotent (IF NOT EXISTS /
-- ON CONFLICT DO NOTHING), so it's safe to run even if some of these
-- columns already exist in your database from earlier manual changes.
--
-- WHY THIS FILE EXISTS: routers/orders.py, routers/admin.py,
-- routers/shopkeepers.py and utils/nimbuspost.py already read/write all
-- of the columns below, but no migration for them shipped in this repo
-- (schema.sql / schema_updates.sql predate the NimbusPost integration).
-- If your live Supabase project already has these columns (e.g. you
-- added them by hand while building the feature), this file is a no-op
-- documentation pass. If it doesn't, running this is what makes order
-- creation, shipment creation, and shopkeeper address saves stop
-- failing.
-- ═══════════════════════════════════════════════════════════════════

-- ─── ORDERS: shipping + delivery-fee + package-PDF columns ────────────
ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_pincode TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS product_image TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_fee NUMERIC(10,2) DEFAULT 0;

-- Mirrors the existing `profit` generated column's pattern — total_amount
-- is read everywhere (routers/whatsapp.py, routers/orders.py) with a
-- manual `our_price + delivery_fee` fallback for rows created before
-- this column existed.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS total_amount NUMERIC(10,2)
    GENERATED ALWAYS AS (our_price + COALESCE(delivery_fee, 0)) STORED;

ALTER TABLE orders ADD COLUMN IF NOT EXISTS nimbuspost_awb TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS nimbuspost_shipment_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS label_url TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS shipping_status TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS package_pdf_status TEXT;

CREATE INDEX IF NOT EXISTS idx_orders_nimbuspost_awb ON orders(nimbuspost_awb);

-- ─── SHOPKEEPERS: pickup address (needed for NimbusPost warehouse regn) ─
ALTER TABLE shopkeepers ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE shopkeepers ADD COLUMN IF NOT EXISTS pincode TEXT;
ALTER TABLE shopkeepers ADD COLUMN IF NOT EXISTS city TEXT;
ALTER TABLE shopkeepers ADD COLUMN IF NOT EXISTS state TEXT;
ALTER TABLE shopkeepers ADD COLUMN IF NOT EXISTS nimbuspost_pickup_id TEXT;

-- ─── SETTINGS: NimbusPost auto-ship mode + delivery fee defaults ──────
-- (settings table itself is created in schema_updates.sql)
INSERT INTO settings (key, value)
VALUES ('nimbuspost_auto_mode', 'false')
ON CONFLICT (key) DO NOTHING;

INSERT INTO settings (key, value)
VALUES ('delivery_fee', '0')
ON CONFLICT (key) DO NOTHING;