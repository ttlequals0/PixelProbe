-- PixelProbe v2.2.46/47 Database Migration SQL
-- Run this directly in PostgreSQL if the Python scripts fail

-- 1. Add last_update column to scan_state if missing
ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS last_update TIMESTAMP;

-- 2. Update existing rows with start_time as last_update
UPDATE scan_state SET last_update = start_time WHERE last_update IS NULL;

-- 3. Add files_processed column to scan_chunks if missing
ALTER TABLE scan_chunks ADD COLUMN IF NOT EXISTS files_processed INTEGER DEFAULT 0 NOT NULL;

-- 4. Add is_complete column to scan_chunks if missing (CRITICAL FIX)
ALTER TABLE scan_chunks ADD COLUMN IF NOT EXISTS is_complete BOOLEAN DEFAULT FALSE NOT NULL;

-- 5. Update existing rows with files_scanned as files_processed
UPDATE scan_chunks SET files_processed = files_scanned WHERE files_processed = 0;

-- 5. Clean up stuck scans (optional - be careful)
UPDATE scan_state 
SET phase = 'crashed',
    is_active = FALSE,
    error_message = 'Cleaned up by v2.2.46 migration',
    end_time = CURRENT_TIMESTAMP
WHERE is_active = TRUE 
AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled');

-- 6. Ensure only one active scan (optional - be careful)
WITH latest_active AS (
    SELECT id 
    FROM scan_state 
    WHERE is_active = TRUE 
    ORDER BY start_time DESC 
    LIMIT 1
)
UPDATE scan_state 
SET is_active = FALSE,
    phase = 'crashed',
    error_message = 'Multiple active scans detected - cleaned up by migration'
WHERE is_active = TRUE 
AND id NOT IN (SELECT id FROM latest_active);

-- Verify the migration
SELECT 
    'scan_state.last_update' as column_check,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'scan_state' AND column_name = 'last_update'
        ) THEN '✅ OK' 
        ELSE '❌ MISSING' 
    END as status
UNION ALL
SELECT 
    'scan_chunks.files_processed' as column_check,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'scan_chunks' AND column_name = 'files_processed'
        ) THEN '✅ OK' 
        ELSE '❌ MISSING' 
    END as status;