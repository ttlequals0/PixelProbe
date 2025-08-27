-- Migration to add celery_task_id column to scan_chunks table
-- This column was added in v2.2.59 but the migration was never run

-- Check if column exists before adding it
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 
        FROM information_schema.columns 
        WHERE table_name = 'scan_chunks' 
        AND column_name = 'celery_task_id'
    ) THEN
        ALTER TABLE scan_chunks 
        ADD COLUMN celery_task_id VARCHAR(36);
        
        -- Create index for faster lookups
        CREATE INDEX idx_scan_chunks_celery_task_id 
        ON scan_chunks (celery_task_id) 
        WHERE celery_task_id IS NOT NULL;
        
        RAISE NOTICE 'Column celery_task_id added to scan_chunks table';
    ELSE
        RAISE NOTICE 'Column celery_task_id already exists in scan_chunks table';
    END IF;
END $$;