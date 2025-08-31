#!/usr/bin/env python3
"""
Emergency database schema fix script for v2.2.46
Applies necessary schema changes to fix production issues
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from models import db
from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_database_schema():
    """Apply emergency database schema fixes"""
    app = create_app()
    
    with app.app_context():
        try:
            # Check and add last_update column to scan_state
            logger.info("Checking scan_state table...")
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'scan_state' 
                AND column_name = 'last_update'
            """))
            
            if not result.fetchone():
                logger.info("Adding last_update column to scan_state...")
                db.session.execute(text("""
                    ALTER TABLE scan_state 
                    ADD COLUMN last_update TIMESTAMP
                """))
                db.session.execute(text("""
                    UPDATE scan_state 
                    SET last_update = start_time 
                    WHERE last_update IS NULL
                """))
                db.session.commit()
                logger.info("✓ Added last_update column")
            else:
                logger.info("✓ last_update column already exists")
            
            # Check and add files_processed column to scan_chunks
            logger.info("Checking scan_chunks table...")
            result = db.session.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'scan_chunks' 
                AND column_name = 'files_processed'
            """))
            
            if not result.fetchone():
                logger.info("Adding files_processed column to scan_chunks...")
                db.session.execute(text("""
                    ALTER TABLE scan_chunks 
                    ADD COLUMN files_processed INTEGER DEFAULT 0 NOT NULL
                """))
                db.session.execute(text("""
                    UPDATE scan_chunks 
                    SET files_processed = files_scanned 
                    WHERE files_processed = 0
                """))
                db.session.commit()
                logger.info("✓ Added files_processed column")
            else:
                logger.info("✓ files_processed column already exists")
            
            # Clean up stuck scans
            logger.info("Cleaning up stuck scans...")
            result = db.session.execute(text("""
                UPDATE scan_state 
                SET phase = 'crashed',
                    is_active = FALSE,
                    error_message = 'Cleaned up by v2.2.46 schema fix',
                    end_time = CURRENT_TIMESTAMP
                WHERE is_active = TRUE 
                AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
                AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled')
            """))
            
            if result.rowcount > 0:
                logger.info(f"✓ Cleaned up {result.rowcount} stuck scans")
            else:
                logger.info("✓ No stuck scans found")
            
            db.session.commit()
            
            logger.info("✅ All database schema fixes applied successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to apply schema fixes: {e}")
            db.session.rollback()
            return False

if __name__ == "__main__":
    success = fix_database_schema()
    sys.exit(0 if success else 1)