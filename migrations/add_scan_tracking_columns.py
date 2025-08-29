#!/usr/bin/env python3
"""
Database migration to add tracking columns to scan_state table
Run this migration to add num_workers, files_added, and files_updated columns
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration():
    """Add new tracking columns to scan_state table"""
    
    # Get database URL from environment
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        # Try constructing from individual env vars
        pg_host = os.environ.get('POSTGRES_HOST', 'localhost')
        pg_port = os.environ.get('POSTGRES_PORT', '5432')
        pg_db = os.environ.get('POSTGRES_DB', 'pixelprobe')
        pg_user = os.environ.get('POSTGRES_USER', 'pixelprobe')
        pg_pass = os.environ.get('POSTGRES_PASSWORD', '')
        
        if pg_pass:
            database_url = f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"
        else:
            logger.error("Database connection details not found in environment")
            return False
    
    try:
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # Check if columns already exist
            check_query = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='scan_state' 
                AND column_name IN ('num_workers', 'files_added', 'files_updated')
            """)
            
            existing_columns = [row[0] for row in conn.execute(check_query)]
            
            # Add missing columns
            if 'num_workers' not in existing_columns:
                logger.info("Adding num_workers column to scan_state table...")
                conn.execute(text("ALTER TABLE scan_state ADD COLUMN num_workers INTEGER DEFAULT 1"))
                conn.commit()
                logger.info("✓ Added num_workers column")
            else:
                logger.info("✓ num_workers column already exists")
            
            if 'files_added' not in existing_columns:
                logger.info("Adding files_added column to scan_state table...")
                conn.execute(text("ALTER TABLE scan_state ADD COLUMN files_added INTEGER DEFAULT 0"))
                conn.commit()
                logger.info("✓ Added files_added column")
            else:
                logger.info("✓ files_added column already exists")
                
            if 'files_updated' not in existing_columns:
                logger.info("Adding files_updated column to scan_state table...")
                conn.execute(text("ALTER TABLE scan_state ADD COLUMN files_updated INTEGER DEFAULT 0"))
                conn.commit()
                logger.info("✓ Added files_updated column")
            else:
                logger.info("✓ files_updated column already exists")
            
            logger.info("Migration completed successfully!")
            return True
            
    except (OperationalError, ProgrammingError) as e:
        logger.error(f"Database migration failed: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during migration: {e}")
        return False

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)