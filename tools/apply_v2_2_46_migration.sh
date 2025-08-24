#!/bin/bash
# Emergency migration script for v2.2.46
# Run this on the production server to apply database schema fixes

echo "========================================"
echo "PixelProbe v2.2.46 Database Migration"
echo "========================================"
echo ""
echo "This script will apply critical database schema fixes:"
echo "1. Add last_update column to scan_state table"
echo "2. Add files_processed column to scan_chunks table"
echo "3. Clean up any stuck scans"
echo ""

# Get database credentials from environment or use defaults
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-pixelprobe}"
POSTGRES_USER="${POSTGRES_USER:-pixelprobe}"

echo "Database: $POSTGRES_USER@$POSTGRES_HOST:$POSTGRES_PORT/$POSTGRES_DB"
echo ""
read -p "Continue with migration? (y/n): " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Migration cancelled."
    exit 1
fi

echo ""
echo "Starting migration..."
echo ""

# Create SQL migration script
cat > /tmp/v2_2_46_migration.sql << 'EOF'
-- v2.2.46 Database Migration Script
BEGIN;

-- 1. Add last_update column to scan_state if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'scan_state' 
        AND column_name = 'last_update'
    ) THEN
        ALTER TABLE scan_state ADD COLUMN last_update TIMESTAMP;
        UPDATE scan_state SET last_update = start_time WHERE last_update IS NULL;
        RAISE NOTICE 'Added last_update column to scan_state table';
    ELSE
        RAISE NOTICE 'last_update column already exists in scan_state table';
    END IF;
END $$;

-- 2. Add files_processed column to scan_chunks if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'scan_chunks' 
        AND column_name = 'files_processed'
    ) THEN
        ALTER TABLE scan_chunks ADD COLUMN files_processed INTEGER DEFAULT 0 NOT NULL;
        UPDATE scan_chunks SET files_processed = files_scanned WHERE files_processed = 0;
        RAISE NOTICE 'Added files_processed column to scan_chunks table';
    ELSE
        RAISE NOTICE 'files_processed column already exists in scan_chunks table';
    END IF;
END $$;

-- 3. Clean up any stuck scans (older than 1 hour)
UPDATE scan_state 
SET phase = 'crashed',
    is_active = FALSE,
    error_message = 'Cleaned up by v2.2.46 migration - scan was stuck',
    end_time = CURRENT_TIMESTAMP
WHERE is_active = TRUE 
AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled');

-- 4. Ensure only one scan is active
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
    error_message = 'Multiple active scans detected - cleaned up by v2.2.46 migration'
WHERE is_active = TRUE 
AND id NOT IN (SELECT id FROM latest_active);

COMMIT;

-- Verify the migration
SELECT 
    'scan_state.last_update' as column_check,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'scan_state' AND column_name = 'last_update'
        ) THEN 'OK' 
        ELSE 'MISSING' 
    END as status
UNION ALL
SELECT 
    'scan_chunks.files_processed' as column_check,
    CASE 
        WHEN EXISTS (
            SELECT 1 FROM information_schema.columns 
            WHERE table_name = 'scan_chunks' AND column_name = 'files_processed'
        ) THEN 'OK' 
        ELSE 'MISSING' 
    END as status;
EOF

# Execute the migration
echo "Executing migration..."
PGPASSWORD="${POSTGRES_PASSWORD}" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/v2_2_46_migration.sql

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================"
    echo "Migration completed successfully!"
    echo "========================================"
    echo ""
    echo "Next steps:"
    echo "1. Restart the PixelProbe container to clear any cached connections"
    echo "2. Monitor the logs to ensure no more schema errors"
    echo ""
    echo "To restart the container:"
    echo "  docker restart pixelprobe"
    echo ""
else
    echo ""
    echo "========================================"
    echo "Migration failed!"
    echo "========================================"
    echo "Please check the error messages above and contact support if needed."
    exit 1
fi

# Clean up
rm -f /tmp/v2_2_46_migration.sql