# Fixing Incomplete Scans

## Problem
Due to the chunk query bug in versions before v2.2.59, some files were marked as "completed" but were never actually scanned. These files show:
- Tool Details: N/A
- Scan Date: N/A
- No actual scan results

## Solutions

### Option 1: Use the API Endpoint
```bash
# Reset all incomplete scans
curl -X POST https://pixelprobe.ttlequals0.com/api/reset-incomplete-scans \
  -H "Authorization: Bearer $TOKEN"
```

### Option 2: Use the Python Script
```bash
# Dry run to see affected files
python scripts/reset_incomplete_scans.py --dry-run

# Actually reset the files
python scripts/reset_incomplete_scans.py
```

### Option 3: Direct SQL Query
```sql
-- First, check how many files are affected
SELECT COUNT(*) 
FROM scan_results 
WHERE scan_status = 'completed'
AND (scan_date IS NULL OR tool_output IS NULL OR tool_output = '' OR tool_output = 'N/A');

-- Reset them to pending
UPDATE scan_results 
SET scan_status = 'pending',
    is_corrupted = NULL,
    error_message = 'Reset due to incomplete scan data'
WHERE scan_status = 'completed'
AND (scan_date IS NULL OR tool_output IS NULL OR tool_output = '' OR tool_output = 'N/A');
```

## After Reset
Once files are reset to 'pending':
1. Run a normal scan - it will pick up all pending files
2. Or run a "Force Scan Pending" to specifically target these files

## Prevention
This issue is fixed in v2.2.59 and later versions.