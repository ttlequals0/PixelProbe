#!/bin/bash
# Direct SQLite approach to reset NAL files when database is locked

DB_PATH="/app/instance/media_checker.db"

echo "=== NAL File Reset Script (Direct SQLite) ==="
echo "Database: $DB_PATH"

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: Database not found at $DB_PATH"
    exit 1
fi

# First, let's see what we're going to update
echo ""
echo "Finding files with NAL unit errors..."
sqlite3 "$DB_PATH" <<EOF
.timeout 30000
.mode column
.headers on
SELECT COUNT(*) as count FROM scan_results 
WHERE is_corrupted = 1 
AND scan_tool = 'ffmpeg'
AND (
    corruption_details LIKE '%Invalid NAL unit%' 
    OR corruption_details LIKE '%NAL unit errors%'
    OR scan_output LIKE '%Invalid NAL unit%'
);
EOF

# Show a few examples
echo ""
echo "Example files that will be reset:"
sqlite3 "$DB_PATH" <<EOF
.timeout 30000
.mode list
SELECT file_path FROM scan_results 
WHERE is_corrupted = 1 
AND scan_tool = 'ffmpeg'
AND (
    corruption_details LIKE '%Invalid NAL unit%' 
    OR corruption_details LIKE '%NAL unit errors%'
    OR scan_output LIKE '%Invalid NAL unit%'
)
LIMIT 5;
EOF

# Confirm before proceeding
echo ""
read -p "Do you want to reset these files to pending status? (y/N) " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Resetting files..."
    
    # Try to update with retries
    for i in {1..5}; do
        echo "Attempt $i of 5..."
        
        sqlite3 "$DB_PATH" <<EOF 2>&1 | tee /tmp/reset_result.txt
.timeout 30000
PRAGMA busy_timeout = 30000;
BEGIN IMMEDIATE;
UPDATE scan_results 
SET scan_status = 'pending',
    is_corrupted = NULL,
    corruption_details = NULL,
    scan_date = NULL,
    scan_tool = NULL,
    scan_output = NULL,
    scan_duration = NULL,
    has_warnings = 0,
    warning_details = NULL
WHERE is_corrupted = 1 
AND scan_tool = 'ffmpeg'
AND (
    corruption_details LIKE '%Invalid NAL unit%' 
    OR corruption_details LIKE '%NAL unit errors%'
    OR scan_output LIKE '%Invalid NAL unit%'
);
SELECT changes() as files_updated;
COMMIT;
EOF
        
        # Check if successful
        if grep -q "database is locked" /tmp/reset_result.txt; then
            echo "Database is locked, waiting 5 seconds before retry..."
            sleep 5
        else
            echo "Reset completed successfully!"
            break
        fi
    done
    
    # Verify the results
    echo ""
    echo "Verification - Files now pending:"
    sqlite3 "$DB_PATH" <<EOF
.timeout 30000
SELECT COUNT(*) as pending_nal_files FROM scan_results 
WHERE scan_status = 'pending'
AND file_path IN (
    SELECT file_path FROM scan_results 
    WHERE corruption_details LIKE '%NAL unit%' 
    OR scan_output LIKE '%Invalid NAL unit%'
);
EOF
    
else
    echo "Reset cancelled."
fi

echo ""
echo "Done."