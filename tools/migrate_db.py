#!/usr/bin/env python3
"""
Database migration script for v2.2.46/47
Can be run directly inside the PixelProbe container
"""

import os
import sys
from sqlalchemy import create_engine, text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_migration():
    """Apply v2.2.46/47 database migrations"""
    
    # Get database configuration
    db_host = os.environ.get('POSTGRES_HOST', 'postgres')
    db_port = os.environ.get('POSTGRES_PORT', '5432')
    db_name = os.environ.get('POSTGRES_DB', 'pixelprobe')
    db_user = os.environ.get('POSTGRES_USER', 'pixelprobe')
    db_pass = os.environ.get('POSTGRES_PASSWORD', '')
    
    if not db_pass:
        logger.warning("POSTGRES_PASSWORD not set - using empty password")
    
    database_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    
    logger.info("=" * 50)
    logger.info("PixelProbe v2.2.46/47 Database Migration")
    logger.info("=" * 50)
    logger.info(f"Database: {db_user}@{db_host}:{db_port}/{db_name}")
    
    try:
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # Start transaction
            trans = conn.begin()
            
            try:
                # 1. Add last_update column to scan_state
                logger.info("\n1. Checking scan_state.last_update column...")
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'scan_state' AND column_name = 'last_update'
                """))
                
                if not result.fetchone():
                    logger.info("   Adding last_update column to scan_state...")
                    conn.execute(text("ALTER TABLE scan_state ADD COLUMN last_update TIMESTAMP"))
                    conn.execute(text("UPDATE scan_state SET last_update = start_time WHERE last_update IS NULL"))
                    logger.info("   ✅ Added last_update column")
                else:
                    logger.info("   ✅ last_update column already exists")
                
                # 2. Add files_processed column to scan_chunks
                logger.info("\n2. Checking scan_chunks.files_processed column...")
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'scan_chunks' AND column_name = 'files_processed'
                """))
                
                if not result.fetchone():
                    logger.info("   Adding files_processed column to scan_chunks...")
                    conn.execute(text("ALTER TABLE scan_chunks ADD COLUMN files_processed INTEGER DEFAULT 0 NOT NULL"))
                    conn.execute(text("UPDATE scan_chunks SET files_processed = files_scanned WHERE files_processed = 0"))
                    logger.info("   ✅ Added files_processed column")
                else:
                    logger.info("   ✅ files_processed column already exists")
                
                # 3. Clean up stuck scans
                logger.info("\n3. Cleaning up stuck scans...")
                result = conn.execute(text("""
                    UPDATE scan_state 
                    SET phase = 'crashed',
                        is_active = FALSE,
                        error_message = 'Cleaned up by v2.2.46 migration - scan was stuck',
                        end_time = CURRENT_TIMESTAMP
                    WHERE is_active = TRUE 
                    AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
                    AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled')
                """))
                
                if result.rowcount > 0:
                    logger.info(f"   ✅ Cleaned up {result.rowcount} stuck scans")
                else:
                    logger.info("   ✅ No stuck scans found")
                
                # 4. Ensure only one scan is active
                logger.info("\n4. Checking for multiple active scans...")
                result = conn.execute(text("""
                    SELECT COUNT(*) FROM scan_state WHERE is_active = TRUE
                """))
                active_count = result.scalar()
                
                if active_count > 1:
                    logger.info(f"   Found {active_count} active scans, cleaning up...")
                    conn.execute(text("""
                        WITH latest_active AS (
                            SELECT id 
                            FROM scan_state 
                            WHERE is_active = TRUE 
                            ORDER BY start_time DESC 
                            LIMIT 1
                        )
                        UPDATE scan_state 
                        SET is_active = FALSE,
                            phase = 'crashed',
                            error_message = 'Multiple active scans detected - cleaned up by migration'
                        WHERE is_active = TRUE 
                        AND id NOT IN (SELECT id FROM latest_active)
                    """))
                    logger.info("   ✅ Cleaned up duplicate active scans")
                else:
                    logger.info(f"   ✅ {active_count} active scan(s) - no cleanup needed")
                
                # 5. Verify migration
                logger.info("\n5. Verifying migration...")
                
                # Check last_update
                result = conn.execute(text("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'scan_state' AND column_name = 'last_update'
                """))
                if result.fetchone():
                    logger.info("   ✅ scan_state.last_update: OK")
                else:
                    logger.error("   ❌ scan_state.last_update: MISSING")
                    raise Exception("Migration verification failed")
                
                # Check files_processed
                result = conn.execute(text("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'scan_chunks' AND column_name = 'files_processed'
                """))
                if result.fetchone():
                    logger.info("   ✅ scan_chunks.files_processed: OK")
                else:
                    logger.error("   ❌ scan_chunks.files_processed: MISSING")
                    raise Exception("Migration verification failed")
                
                # Commit transaction
                trans.commit()
                
                logger.info("\n" + "=" * 50)
                logger.info("✅ Migration completed successfully!")
                logger.info("=" * 50)
                logger.info("\nIMPORTANT: Restart the container to apply changes:")
                logger.info("  From host: docker-compose restart pixelprobe")
                logger.info("  Or: docker restart pixelprobe")
                
                return True
                
            except Exception as e:
                trans.rollback()
                logger.error(f"\n❌ Migration failed: {e}")
                return False
                
    except Exception as e:
        logger.error(f"\n❌ Could not connect to database: {e}")
        logger.error("\nTroubleshooting:")
        logger.error("1. Check POSTGRES_* environment variables")
        logger.error("2. Ensure PostgreSQL container is running")
        logger.error("3. Verify network connectivity between containers")
        return False

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)