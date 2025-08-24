#!/bin/bash
# Docker-compatible migration script for v2.2.46
# Run this from the host machine or inside the app container

echo "========================================"
echo "PixelProbe v2.2.46 Database Migration"
echo "========================================"
echo ""
echo "This script will apply critical database schema fixes"
echo ""

# Method 1: If running from host machine with docker-compose
if command -v docker-compose &> /dev/null; then
    echo "Using docker-compose to apply migration..."
    
    # Create the SQL migration file
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

    # Apply migration using docker-compose
    docker-compose exec -T postgres psql -U pixelprobe -d pixelprobe < /tmp/v2_2_46_migration.sql
    
    if [ $? -eq 0 ]; then
        echo ""
        echo "========================================"
        echo "Migration completed successfully!"
        echo "========================================"
        echo ""
        echo "Next steps:"
        echo "1. Restart the PixelProbe container:"
        echo "   docker-compose restart pixelprobe"
        echo ""
    else
        echo ""
        echo "========================================"
        echo "Migration failed!"
        echo "========================================"
        exit 1
    fi

# Method 2: If running from inside the app container
elif [ -f /app/app.py ]; then
    echo "Running from inside container, using Python to apply migration..."
    
    python3 << 'PYTHON_SCRIPT'
import os
import sys
from sqlalchemy import create_engine, text

# Get database URL from environment
db_host = os.environ.get('POSTGRES_HOST', 'postgres')
db_port = os.environ.get('POSTGRES_PORT', '5432')
db_name = os.environ.get('POSTGRES_DB', 'pixelprobe')
db_user = os.environ.get('POSTGRES_USER', 'pixelprobe')
db_pass = os.environ.get('POSTGRES_PASSWORD', '')

database_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"

print(f"Connecting to database at {db_host}:{db_port}/{db_name}...")

try:
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        # Start transaction
        trans = conn.begin()
        
        try:
            # 1. Add last_update column
            print("Checking scan_state.last_update column...")
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'scan_state' AND column_name = 'last_update'
            """))
            
            if not result.fetchone():
                print("Adding last_update column to scan_state...")
                conn.execute(text("ALTER TABLE scan_state ADD COLUMN last_update TIMESTAMP"))
                conn.execute(text("UPDATE scan_state SET last_update = start_time WHERE last_update IS NULL"))
                print("✓ Added last_update column")
            else:
                print("✓ last_update column already exists")
            
            # 2. Add files_processed column
            print("Checking scan_chunks.files_processed column...")
            result = conn.execute(text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = 'scan_chunks' AND column_name = 'files_processed'
            """))
            
            if not result.fetchone():
                print("Adding files_processed column to scan_chunks...")
                conn.execute(text("ALTER TABLE scan_chunks ADD COLUMN files_processed INTEGER DEFAULT 0 NOT NULL"))
                conn.execute(text("UPDATE scan_chunks SET files_processed = files_scanned WHERE files_processed = 0"))
                print("✓ Added files_processed column")
            else:
                print("✓ files_processed column already exists")
            
            # 3. Clean up stuck scans
            print("Cleaning up stuck scans...")
            result = conn.execute(text("""
                UPDATE scan_state 
                SET phase = 'crashed',
                    is_active = FALSE,
                    error_message = 'Cleaned up by v2.2.46 migration',
                    end_time = CURRENT_TIMESTAMP
                WHERE is_active = TRUE 
                AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
                AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled')
            """))
            
            if result.rowcount > 0:
                print(f"✓ Cleaned up {result.rowcount} stuck scans")
            else:
                print("✓ No stuck scans found")
            
            # Commit transaction
            trans.commit()
            print("\n========================================")
            print("Migration completed successfully!")
            print("========================================")
            print("\nRestart the container to apply changes:")
            print("From host: docker-compose restart pixelprobe")
            
        except Exception as e:
            trans.rollback()
            print(f"\n❌ Migration failed: {e}")
            sys.exit(1)
            
except Exception as e:
    print(f"\n❌ Could not connect to database: {e}")
    print("\nMake sure POSTGRES_* environment variables are set correctly")
    sys.exit(1)
PYTHON_SCRIPT

else
    echo "========================================"
    echo "Error: Could not determine environment"
    echo "========================================"
    echo ""
    echo "Please run this script either:"
    echo "1. From the host machine with docker-compose installed"
    echo "2. From inside the PixelProbe container"
    echo ""
    echo "Alternative: Connect directly to PostgreSQL container:"
    echo "  docker exec -it pixelprobe-postgres psql -U pixelprobe -d pixelprobe"
    echo ""
    echo "Then run these SQL commands:"
    echo "  ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS last_update TIMESTAMP;"
    echo "  ALTER TABLE scan_chunks ADD COLUMN IF NOT EXISTS files_processed INTEGER DEFAULT 0;"
    echo ""
    exit 1
fi