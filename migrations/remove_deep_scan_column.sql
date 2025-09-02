-- Remove deep_scan column that was supposed to be removed in v2.2.79
-- This migration fixes the Phase 2 stuck issue where inserts fail due to NOT NULL constraint

-- First, make the column nullable and add a default
ALTER TABLE scan_results ALTER COLUMN deep_scan DROP NOT NULL;
ALTER TABLE scan_results ALTER COLUMN deep_scan SET DEFAULT FALSE;

-- Update any NULL values to FALSE
UPDATE scan_results SET deep_scan = FALSE WHERE deep_scan IS NULL;

-- Eventually we can drop the column entirely with:
-- ALTER TABLE scan_results DROP COLUMN deep_scan;