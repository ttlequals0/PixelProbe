#!/usr/bin/env python3
"""
Migration script to fix v2.2.46 database schema issues:
1. Add last_update column to scan_state table
2. Ensure files_processed column exists in scan_chunks table
3. Clean up any stuck/invalid scan states
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import ProgrammingError, OperationalError
from datetime import datetime, timezone
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration(database_url):
    """Run the migration to fix v2.2.46 issues"""
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        # Start a transaction
        trans = conn.begin()
        
        try:
            # 1. Add last_update column to scan_state if it doesn't exist
            logger.info("Checking scan_state table for last_update column...")
            inspector = inspect(engine)
            
            if 'scan_state' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('scan_state')]
                
                if 'last_update' not in columns:
                    logger.info("Adding last_update column to scan_state table...")
                    conn.execute(text("""
                        ALTER TABLE scan_state 
                        ADD COLUMN last_update TIMESTAMP
                    """))
                    
                    # Initialize last_update with start_time for existing records
                    conn.execute(text("""
                        UPDATE scan_state 
                        SET last_update = start_time
                        WHERE last_update IS NULL
                    """))
                    logger.info("✓ Added last_update column to scan_state table")
                else:
                    logger.info("✓ last_update column already exists in scan_state table")
            
            # 2. Ensure files_processed column exists in scan_chunks table
            logger.info("Checking scan_chunks table for files_processed column...")
            
            if 'scan_chunks' in inspector.get_table_names():
                columns = [col['name'] for col in inspector.get_columns('scan_chunks')]
                
                if 'files_processed' not in columns:
                    logger.info("Adding files_processed column to scan_chunks table...")
                    conn.execute(text("""
                        ALTER TABLE scan_chunks 
                        ADD COLUMN files_processed INTEGER DEFAULT 0 NOT NULL
                    """))
                    
                    # Initialize files_processed with files_scanned for existing records
                    conn.execute(text("""
                        UPDATE scan_chunks 
                        SET files_processed = files_scanned
                        WHERE files_processed = 0
                    """))
                    logger.info("✓ Added files_processed column to scan_chunks table")
                else:
                    logger.info("✓ files_processed column already exists in scan_chunks table")
            
            # 3. Clean up any stuck scan states
            logger.info("Cleaning up stuck scan states...")
            
            # Mark any active scans older than 1 hour as crashed
            conn.execute(text("""
                UPDATE scan_state 
                SET phase = 'crashed',
                    is_active = FALSE,
                    error_message = 'Scan marked as crashed by v2.2.46 migration - was stuck',
                    end_time = CURRENT_TIMESTAMP
                WHERE is_active = TRUE 
                AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
                AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled')
            """))
            
            # Ensure only one scan can be active
            result = conn.execute(text("""
                SELECT COUNT(*) as count FROM scan_state WHERE is_active = TRUE
            """))
            active_count = result.scalar()
            
            if active_count > 1:
                logger.warning(f"Found {active_count} active scans, keeping only the most recent...")
                conn.execute(text("""
                    UPDATE scan_state 
                    SET is_active = FALSE,
                        phase = 'crashed',
                        error_message = 'Multiple active scans detected - cleaned up by migration'
                    WHERE is_active = TRUE 
                    AND id NOT IN (
                        SELECT id FROM scan_state 
                        WHERE is_active = TRUE 
                        ORDER BY start_time DESC 
                        LIMIT 1
                    )
                """))
            
            logger.info("✓ Cleaned up stuck scan states")
            
            # Commit the transaction
            trans.commit()
            logger.info("✅ Migration completed successfully")
            
        except Exception as e:
            trans.rollback()
            logger.error(f"❌ Migration failed: {e}")
            raise

if __name__ == "__main__":
    # Get database URL from environment or use default
    database_url = os.environ.get('DATABASE_URL')
    
    if not database_url:
        # Try to get from config
        try:
            from config import Config
            config = Config()
            database_url = config.SQLALCHEMY_DATABASE_URI
        except ImportError:
            logger.error("Could not determine database URL. Set DATABASE_URL environment variable.")
            sys.exit(1)
    
    try:
        run_migration(database_url)
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        sys.exit(1)