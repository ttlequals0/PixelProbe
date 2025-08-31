-- Find files marked as healthy but never actually scanned
-- These show as HEALTHY in UI but have N/A for scan details

-- Count files marked healthy but with no scan date
SELECT COUNT(*) as false_healthy_count
FROM scan_results 
WHERE is_corrupted = false
AND scan_date IS NULL;

-- List these files to understand the pattern
SELECT 
    file_path,
    scan_status,
    is_corrupted,
    scan_date,
    scan_output,
    discovered_date,
    error_message
FROM scan_results 
WHERE is_corrupted = false
AND scan_date IS NULL
LIMIT 10;

-- Also check for files with scan_status = 'completed' but no scan_date
SELECT COUNT(*) as completed_no_date
FROM scan_results 
WHERE scan_status = 'completed'
AND scan_date IS NULL;

-- Check what's in scan_output for these files
SELECT DISTINCT 
    LEFT(scan_output, 100) as scan_output_preview,
    COUNT(*) as count
FROM scan_results 
WHERE is_corrupted = false
AND scan_date IS NULL
GROUP BY LEFT(scan_output, 100)
LIMIT 10;

-- Reset these falsely healthy files to pending
-- UNCOMMENT TO EXECUTE:
-- UPDATE scan_results 
-- SET scan_status = 'pending',
--     is_corrupted = NULL,
--     error_message = 'Reset - marked healthy but never scanned'
-- WHERE is_corrupted = false
-- AND scan_date IS NULL;