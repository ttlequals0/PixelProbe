#!/usr/bin/env python3
"""
Migration: Add celery_task_id column to scan_state table
P1 Implementation per 2.1_AUDIT_IMPLEMENTATION_PLAN.md

This migration adds support for tracking Celery task IDs in the scan state.
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from urllib.parse import quote_plus

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_database_connection():
    """Get database connection based on environment"""
    config = Config()
    
    # Build complete database URI
    if config.POSTGRES_PASSWORD:
        encoded_password = quote_plus(config.POSTGRES_PASSWORD)
        database_uri = (
            f"postgresql://{config.POSTGRES_USER}:{encoded_password}@"
            f"{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
        )
    else:
        database_uri = (
            f"postgresql://{config.POSTGRES_USER}@"
            f"{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
        )
    
    logger.info(f"Connecting to database: {config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}")
    
    engine = create_engine(database_uri)
    return engine


def migrate_database():
    """Add celery_task_id column to scan_state table"""
    try:
        engine = get_database_connection()
        
        with engine.connect() as conn:
            # Start transaction
            trans = conn.begin()
            
            try:
                # Check if column already exists
                logger.info("Checking if celery_task_id column already exists...")
                
                result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'scan_state' 
                    AND column_name = 'celery_task_id'
                """))
                
                if result.fetchone():
                    logger.info("celery_task_id column already exists, skipping migration")
                    trans.rollback()
                    return True
                
                # Add the column
                logger.info("Adding celery_task_id column to scan_state table...")
                conn.execute(text("""
                    ALTER TABLE scan_state 
                    ADD COLUMN celery_task_id VARCHAR(36)
                """))
                
                # Create index for performance
                logger.info("Creating index on celery_task_id column...")
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_scan_state_celery_task_id 
                    ON scan_state(celery_task_id)
                """))
                
                # Commit transaction
                trans.commit()
                logger.info("Successfully added celery_task_id column and index")
                
                return True
                
            except Exception as e:
                trans.rollback()
                logger.error(f"Error during migration: {e}")
                raise
                
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        return False


def verify_migration():
    """Verify the migration was successful"""
    try:
        engine = get_database_connection()
        
        with engine.connect() as conn:
            # Check column exists
            result = conn.execute(text("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns 
                WHERE table_name = 'scan_state' 
                AND column_name = 'celery_task_id'
            """))
            
            column_info = result.fetchone()
            if column_info:
                logger.info(f"✅ Column verified: {column_info[0]} ({column_info[1]}, nullable: {column_info[2]})")
                
                # Check index exists
                result = conn.execute(text("""
                    SELECT indexname 
                    FROM pg_indexes 
                    WHERE tablename = 'scan_state' 
                    AND indexname = 'idx_scan_state_celery_task_id'
                """))
                
                if result.fetchone():
                    logger.info("✅ Index verified: idx_scan_state_celery_task_id")
                    return True
                else:
                    logger.error("❌ Index not found")
                    return False
            else:
                logger.error("❌ Column not found")
                return False
                
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        return False


if __name__ == '__main__':
    logger.info("=== P1 Celery Task ID Migration ===")
    logger.info("Adding celery_task_id column to scan_state table")
    
    # Run migration
    if migrate_database():
        logger.info("Migration completed successfully")
        
        # Verify migration
        if verify_migration():
            logger.info("Migration verification passed")
            sys.exit(0)
        else:
            logger.error("Migration verification failed")
            sys.exit(1)
    else:
        logger.error("Migration failed")
        sys.exit(1)