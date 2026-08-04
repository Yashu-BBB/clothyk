-- ═══════════════════════════════════════════════════════════════════
-- clovical SCHEMA UPDATES
-- Run in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════

-- ─── FEATURE 1: Girls Section Toggle ────────────────────────────────
-- Ensure the settings table exists (it should already exist)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Add the girls_section_enabled setting (defaults to disabled)
INSERT INTO settings (key, value)
VALUES ('girls_section_enabled', 'false')
ON CONFLICT (key) DO NOTHING;

-- ─── FEATURE 2: Multiple Product Images ──────────────────────────────
-- Add images JSONB array column to products
ALTER TABLE products ADD COLUMN IF NOT EXISTS images JSONB DEFAULT '[]';

-- Migrate existing single image to images array
-- (Only for rows that have an image but empty images array)
UPDATE products
SET images = CASE
    WHEN image IS NOT NULL AND image != '' THEN jsonb_build_array(image)
    ELSE '[]'::jsonb
END
WHERE images = '[]'::jsonb OR images IS NULL;

-- Index for faster JSONB lookups (optional but recommended)
CREATE INDEX IF NOT EXISTS idx_products_images ON products USING gin(images);
