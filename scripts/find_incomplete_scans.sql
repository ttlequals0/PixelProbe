-- Find files marked as completed but missing scan details
-- These files show "N/A" for Tool Details and Scan Date in the UI

-- Count of affected files
SELECT COUNT(*) as incomplete_scan_count
FROM scan_results 
WHERE scan_status = 'completed'
AND (
    scan_date IS NULL 
    OR tool_output IS NULL 
    OR tool_output = ''
    OR tool_output = 'N/A'
);

-- List affected files (limit to first 100 for review)
SELECT 
    file_path,
    scan_status,
    scan_date,
    tool_output,
    is_corrupted,
    discovered_date
FROM scan_results 
WHERE scan_status = 'completed'
AND (
    scan_date IS NULL 
    OR tool_output IS NULL 
    OR tool_output = ''
    OR tool_output = 'N/A'
)
LIMIT 100;

-- Reset these files to pending so they'll be rescanned
-- UNCOMMMENT TO EXECUTE:
-- UPDATE scan_results 
-- SET scan_status = 'pending',
--     error_message = 'Reset due to incomplete scan data'
-- WHERE scan_status = 'completed'
-- AND (
--     scan_date IS NULL 
--     OR tool_output IS NULL 
--     OR tool_output = ''
--     OR tool_output = 'N/A'
-- );