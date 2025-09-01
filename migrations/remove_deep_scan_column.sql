-- Remove deep_scan column that was supposed to be removed in v2.2.79
-- This migration fixes the Phase 2 stuck issue where inserts fail due to NOT NULL constraint

ALTER TABLE scan_results ALTER COLUMN deep_scan DROP NOT NULL;
ALTER TABLE scan_results ALTER COLUMN deep_scan SET DEFAULT FALSE;

-- Eventually we can drop the column entirely, but for now just make it nullable 
-- to prevent insert failures while maintaining backwards compatibility