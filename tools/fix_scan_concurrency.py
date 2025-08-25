#!/usr/bin/env python3
"""
Fix for scan concurrency issue - ensures only one scan can run at a time
This script cleans up stale scans and implements proper locking
"""

import os
import sys
from sqlalchemy import create_engine, text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_scan_concurrency():
    """Fix scan concurrency issues in the database"""
    
    # Get database configuration
    db_host = os.environ.get('POSTGRES_HOST', 'postgres')
    db_port = os.environ.get('POSTGRES_PORT', '5432')
    db_name = os.environ.get('POSTGRES_DB', 'pixelprobe')
    db_user = os.environ.get('POSTGRES_USER', 'pixelprobe')
    db_pass = os.environ.get('POSTGRES_PASSWORD', '')
    
    database_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    
    logger.info("=" * 50)
    logger.info("PixelProbe Scan Concurrency Fix")
    logger.info("=" * 50)
    
    try:
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # 1. Clean up all stale/crashed scans
            logger.info("\n1. Cleaning up stale scans...")
            trans = conn.begin()
            try:
                result = conn.execute(text("""
                    UPDATE scan_state 
                    SET is_active = FALSE,
                        phase = CASE 
                            WHEN phase IN ('discovering', 'adding', 'scanning') THEN 'crashed'
                            ELSE phase
                        END,
                        error_message = 'Cleaned up by concurrency fix',
                        end_time = CURRENT_TIMESTAMP
                    WHERE is_active = TRUE
                    AND (
                        -- Scans older than 1 hour with no recent update
                        (last_update IS NOT NULL AND last_update < CURRENT_TIMESTAMP - INTERVAL '1 hour')
                        OR 
                        (last_update IS NULL AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour')
                        OR
                        -- Any scan in crashed state
                        phase = 'crashed'
                    )
                """))
                
                cleaned = result.rowcount
                if cleaned > 0:
                    logger.info(f"   ✅ Cleaned up {cleaned} stale/crashed scans")
                else:
                    logger.info("   ✅ No stale scans found")
                    
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.error(f"   ❌ Failed to clean stale scans: {e}")
            
            # 2. Ensure only ONE active scan exists
            logger.info("\n2. Checking for multiple active scans...")
            trans = conn.begin()
            try:
                result = conn.execute(text("""
                    SELECT id, scan_id, phase, start_time, is_active
                    FROM scan_state
                    WHERE is_active = TRUE
                    ORDER BY start_time DESC
                """))
                
                active_scans = result.fetchall()
                if len(active_scans) > 1:
                    logger.warning(f"   ⚠️ Found {len(active_scans)} active scans - cleaning up...")
                    
                    # Keep only the most recent one
                    keep_id = active_scans[0][0]
                    
                    # Deactivate all others
                    for scan in active_scans[1:]:
                        conn.execute(text("""
                            UPDATE scan_state
                            SET is_active = FALSE,
                                phase = 'crashed',
                                error_message = 'Multiple active scans detected - cleaned up',
                                end_time = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """), {'id': scan[0]})
                        logger.info(f"   Deactivated scan {scan[1]} (id={scan[0]})")
                    
                    logger.info(f"   ✅ Kept only scan {active_scans[0][1]} (id={keep_id}) active")
                elif len(active_scans) == 1:
                    logger.info(f"   ✅ Single active scan found: {active_scans[0][1]}")
                else:
                    logger.info("   ✅ No active scans found")
                
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.error(f"   ❌ Failed to check active scans: {e}")
            
            # 3. Create a constraint to prevent multiple active scans (if not exists)
            logger.info("\n3. Adding database constraint for single active scan...")
            trans = conn.begin()
            try:
                # Check if constraint exists
                result = conn.execute(text("""
                    SELECT constraint_name 
                    FROM information_schema.table_constraints 
                    WHERE table_name = 'scan_state' 
                    AND constraint_name = 'only_one_active_scan'
                """))
                
                if not result.fetchone():
                    # Create partial unique index to ensure only one active scan
                    conn.execute(text("""
                        CREATE UNIQUE INDEX only_one_active_scan 
                        ON scan_state (is_active) 
                        WHERE is_active = TRUE
                    """))
                    logger.info("   ✅ Added unique constraint for active scans")
                else:
                    logger.info("   ✅ Constraint already exists")
                    
                trans.commit()
            except Exception as e:
                trans.rollback()
                if "already exists" in str(e).lower():
                    logger.info("   ✅ Constraint already exists")
                else:
                    logger.warning(f"   ⚠️ Could not add constraint: {e}")
            
            # 4. Reset scan_id sequence to prevent ID conflicts
            logger.info("\n4. Resetting scan ID sequence...")
            trans = conn.begin()
            try:
                # Get the max ID
                result = conn.execute(text("SELECT MAX(id) FROM scan_state"))
                max_id = result.scalar() or 0
                
                # Reset sequence
                conn.execute(text(f"""
                    SELECT setval('scan_state_id_seq', {max_id + 1}, false)
                """))
                
                logger.info(f"   ✅ Reset scan_state sequence to {max_id + 1}")
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.warning(f"   ⚠️ Could not reset sequence: {e}")
            
            # 5. Final verification
            logger.info("\n5. Final verification...")
            result = conn.execute(text("""
                SELECT COUNT(*) FROM scan_state WHERE is_active = TRUE
            """))
            active_count = result.scalar()
            
            logger.info("\n" + "=" * 50)
            if active_count <= 1:
                logger.info("✅ Scan concurrency fix completed successfully!")
                logger.info(f"   Active scans: {active_count}")
                logger.info("   Database constraint added to prevent future issues")
            else:
                logger.warning(f"⚠️ Still have {active_count} active scans - manual intervention may be needed")
            logger.info("=" * 50)
            
            return active_count <= 1
                
    except Exception as e:
        logger.error(f"\n❌ Could not connect to database: {e}")
        return False

if __name__ == "__main__":
    success = fix_scan_concurrency()
    sys.exit(0 if success else 1)