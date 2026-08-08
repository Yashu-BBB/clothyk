-- ═══════════════════════════════════════════════════════════════════
-- clovical SCHEMA UPDATE — Shopkeeper package-PDF pull
-- Run in Supabase SQL Editor
--
-- Background: the packing PDF used to be pushed to the shopkeeper's
-- WhatsApp the instant an order was confirmed/shipped. Because that's an
-- unsolicited business-initiated message on every single order, WhatsApp's
-- spam heuristics flagged the number and it got temporarily restricted.
--
-- New behaviour: the PDF is generated and stored here instead. It's only
-- ever sent when the shopkeeper's own registered number messages the bot
-- (see handle_shopkeeper_order_pull in routers/whatsapp.py) — a reply, not
-- a cold push.
-- ═══════════════════════════════════════════════════════════════════

-- Existing installs may already have package_pdf_status from an earlier
-- ad-hoc ALTER — IF NOT EXISTS makes this safe to re-run either way.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS package_pdf_status TEXT;
-- Allowed values (not enforced with a CHECK, in case existing rows already
-- hold a different value from before this migration):
--   NULL     — PDF not generated yet
--   'ready'  — generated and waiting for the shopkeeper to pull it
--   'sent'   — delivered to the shopkeeper over WhatsApp
--   'failed' — PDF generation itself failed

ALTER TABLE orders ADD COLUMN IF NOT EXISTS package_pdf_base64 TEXT;
-- The built PDF, base64-encoded, while it's waiting to be pulled.
-- Cleared back to NULL once package_pdf_status is set to 'sent'.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS package_pdf_filename TEXT;

ALTER TABLE orders ADD COLUMN IF NOT EXISTS package_pdf_generated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_orders_package_pdf_status ON orders(package_pdf_status);