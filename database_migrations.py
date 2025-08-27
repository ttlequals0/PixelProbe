#!/usr/bin/env python3
"""
Database migration runner for PixelProbe
Automatically applies missing database schema changes on startup
"""

import logging
from sqlalchemy import text
from models import db

logger = logging.getLogger(__name__)

def run_migrations():
    """Run all pending database migrations"""
    migrations_applied = []
    
    try:
        # Migration 1: Add celery_task_id column to scan_chunks
        with db.engine.connect() as conn:
            # Check if column exists
            result = conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'scan_chunks' 
                AND column_name = 'celery_task_id'
            """))
            
            if not result.fetchone():
                logger.info("Applying migration: Adding celery_task_id to scan_chunks")
                conn.execute(text("""
                    ALTER TABLE scan_chunks 
                    ADD COLUMN celery_task_id VARCHAR(36)
                """))
                
                # Create index for performance
                conn.execute(text("""
                    CREATE INDEX idx_scan_chunks_celery_task_id 
                    ON scan_chunks (celery_task_id) 
                    WHERE celery_task_id IS NOT NULL
                """))
                
                conn.commit()
                migrations_applied.append("add_celery_task_id_to_scan_chunks")
                logger.info("Migration applied successfully: celery_task_id column added")
            else:
                logger.debug("Migration already applied: celery_task_id column exists")
                
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        # Don't fail startup if migration fails - app might still work
        
    if migrations_applied:
        logger.info(f"Applied {len(migrations_applied)} migrations: {', '.join(migrations_applied)}")
    else:
        logger.debug("No pending migrations to apply")
        
    return migrations_applied

def check_database_schema():
    """Verify database schema is correct"""
    issues = []
    
    try:
        with db.engine.connect() as conn:
            # Check for required columns
            required_columns = [
                ('scan_chunks', 'celery_task_id'),
                ('scan_results', 'scan_output'),
                ('scan_results', 'scan_date'),
                ('scan_results', 'is_corrupted'),
                ('scan_state', 'celery_task_id'),
            ]
            
            for table, column in required_columns:
                result = conn.execute(text(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = '{table}' 
                    AND column_name = '{column}'
                """))
                
                if not result.fetchone():
                    issues.append(f"Missing column: {table}.{column}")
                    
    except Exception as e:
        logger.error(f"Schema check failed: {e}")
        issues.append(f"Schema check error: {e}")
        
    if issues:
        logger.warning(f"Database schema issues found: {', '.join(issues)}")
    else:
        logger.info("Database schema verification passed")
        
    return issues