#!/usr/bin/env python3
"""
Migration to fix scan issues identified in v2.2.5
- Increases VARCHAR limits for progress messages
- Adds better indexing for scan operations
- Fixes constraint issues
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import OperationalError, ProgrammingError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration(database_url):
    """Run migration to fix scan issues"""
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        # Start transaction
        trans = conn.begin()
        
        try:
            logger.info("Starting migration to fix scan issues...")
            
            # 1. Fix progress_message fields - increase from 200 to 1000 chars
            logger.info("Updating progress_message column lengths...")
            
            tables_to_update = [
                ('scan_state', 'progress_message'),
                ('cleanup_state', 'progress_message'),
                ('file_changes_state', 'progress_message')
            ]
            
            for table_name, column_name in tables_to_update:
                try:
                    # Check if table exists
                    result = conn.execute(text(f"""
                        SELECT EXISTS (
                            SELECT FROM information_schema.tables 
                            WHERE table_name = '{table_name}'
                        )
                    """))
                    if result.scalar():
                        # Alter column type
                        conn.execute(text(f"""
                            ALTER TABLE {table_name} 
                            ALTER COLUMN {column_name} TYPE VARCHAR(1000)
                        """))
                        logger.info(f"  ✓ Updated {table_name}.{column_name} to VARCHAR(1000)")
                except Exception as e:
                    logger.warning(f"  ⚠ Could not update {table_name}.{column_name}: {e}")
            
            # 2. Fix chunk_id uniqueness - add timestamp component
            logger.info("Checking scan_chunks table...")
            try:
                # Drop the unique constraint if it exists
                conn.execute(text("""
                    ALTER TABLE scan_chunks 
                    DROP CONSTRAINT IF EXISTS ix_scan_chunks_chunk_id
                """))
                
                # Create a non-unique index instead
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS ix_scan_chunks_chunk_id 
                    ON scan_chunks(chunk_id)
                """))
                
                # Add composite unique constraint on scan_id + chunk_id
                conn.execute(text("""
                    ALTER TABLE scan_chunks 
                    ADD CONSTRAINT uq_scan_chunks_scan_chunk 
                    UNIQUE (scan_id, chunk_id)
                """))
                logger.info("  ✓ Fixed scan_chunks uniqueness constraints")
            except Exception as e:
                logger.warning(f"  ⚠ Could not update scan_chunks constraints: {e}")
            
            # 3. Add timeout tracking columns
            logger.info("Adding timeout tracking columns...")
            try:
                # Check if columns exist first
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'scan_results' 
                    AND column_name = 'scan_timeout'
                """))
                
                if not result.fetchone():
                    conn.execute(text("""
                        ALTER TABLE scan_results 
                        ADD COLUMN scan_timeout BOOLEAN DEFAULT FALSE
                    """))
                    logger.info("  ✓ Added scan_timeout column")
                    
                # Add timeout_reason column
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'scan_results' 
                    AND column_name = 'timeout_reason'
                """))
                
                if not result.fetchone():
                    conn.execute(text("""
                        ALTER TABLE scan_results 
                        ADD COLUMN timeout_reason VARCHAR(200)
                    """))
                    logger.info("  ✓ Added timeout_reason column")
                    
            except Exception as e:
                logger.warning(f"  ⚠ Could not add timeout columns: {e}")
            
            # 4. Add scan recovery tracking
            logger.info("Adding scan recovery tracking...")
            try:
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'scan_state' 
                    AND column_name = 'crash_count'
                """))
                
                if not result.fetchone():
                    conn.execute(text("""
                        ALTER TABLE scan_state 
                        ADD COLUMN crash_count INTEGER DEFAULT 0
                    """))
                    logger.info("  ✓ Added crash_count column")
                    
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'scan_state' 
                    AND column_name = 'last_crash_time'
                """))
                
                if not result.fetchone():
                    conn.execute(text("""
                        ALTER TABLE scan_state 
                        ADD COLUMN last_crash_time TIMESTAMP
                    """))
                    logger.info("  ✓ Added last_crash_time column")
                    
            except Exception as e:
                logger.warning(f"  ⚠ Could not add recovery tracking columns: {e}")
            
            # 5. Clean up any stuck scans
            logger.info("Cleaning up stuck scans...")
            try:
                # Mark scans that have been running for > 24 hours as crashed
                conn.execute(text("""
                    UPDATE scan_state 
                    SET is_active = FALSE,
                        phase = 'crashed',
                        end_time = NOW(),
                        error_message = 'Scan crashed - exceeded 24 hour runtime'
                    WHERE is_active = TRUE 
                    AND start_time < NOW() - INTERVAL '24 hours'
                """))
                logger.info("  ✓ Cleaned up stuck scans")
            except Exception as e:
                logger.warning(f"  ⚠ Could not clean up stuck scans: {e}")
            
            # Commit transaction
            trans.commit()
            logger.info("✅ Migration completed successfully!")
            
        except Exception as e:
            trans.rollback()
            logger.error(f"❌ Migration failed: {e}")
            raise

if __name__ == "__main__":
    # Get database URL from environment or use default
    database_url = os.getenv('DATABASE_URL')
    
    if not database_url:
        # Build from components
        host = os.getenv('POSTGRES_HOST', 'localhost')
        port = os.getenv('POSTGRES_PORT', '5432')
        db = os.getenv('POSTGRES_DB', 'pixelprobe')
        user = os.getenv('POSTGRES_USER', 'pixelprobe')
        password = os.getenv('POSTGRES_PASSWORD', '')
        
        if password:
            database_url = f"postgresql://{user}:{password}@{host}:{port}/{db}"
        else:
            database_url = f"postgresql://{user}@{host}:{port}/{db}"
    
    run_migration(database_url)