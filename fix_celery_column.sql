-- Emergency fix for missing celery_task_id column
-- This script adds the missing column to the scan_state table

-- Add celery_task_id column to scan_state table
ALTER TABLE scan_state 
ADD COLUMN celery_task_id VARCHAR(36);

-- Add index for performance
CREATE INDEX IF NOT EXISTS idx_scan_state_celery_task_id ON scan_state(celery_task_id);

-- Verify the column was added
\d scan_state;