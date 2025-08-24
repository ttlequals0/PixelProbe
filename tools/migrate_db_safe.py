#!/usr/bin/env python3
"""
Safe database migration script for v2.2.46/47
Handles database locks and active transactions properly
"""

import os
import sys
import time
from sqlalchemy import create_engine, text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def kill_blocking_connections(engine, db_name):
    """Kill any blocking connections to allow migration"""
    try:
        # Use a separate connection for this check
        with engine.connect() as conn:
            # Get current connection PID
            result = conn.execute(text("SELECT pg_backend_pid()"))
            current_pid = result.scalar()
            
            # Terminate other connections that might be blocking
            logger.info("Checking for blocking connections...")
            result = conn.execute(text("""
                SELECT pid, state, query 
                FROM pg_stat_activity 
                WHERE datname = :db_name 
                AND pid != :current_pid
                AND state != 'idle'
            """), {'db_name': db_name, 'current_pid': current_pid})
            
            blocking = result.fetchall()
            if blocking:
                logger.warning(f"Found {len(blocking)} potentially blocking connections")
                for pid, state, query in blocking:
                    logger.info(f"  PID {pid}: {state} - {query[:50]}...")
                    
                # Ask user before killing connections
                response = input("\nTerminate blocking connections? (y/n): ")
                if response.lower() == 'y':
                    conn.execute(text("""
                        SELECT pg_terminate_backend(pid)
                        FROM pg_stat_activity
                        WHERE datname = :db_name
                        AND pid != :current_pid
                        AND state != 'idle'
                    """), {'db_name': db_name, 'current_pid': current_pid})
                    logger.info("Terminated blocking connections")
                    conn.commit()  # Commit the termination
                    time.sleep(1)  # Give time for connections to close
            else:
                logger.info("No blocking connections found")
            
    except Exception as e:
        logger.warning(f"Could not check blocking connections: {e}")

def run_migration_with_timeout(conn, statement, description, timeout_seconds=5):
    """Run a statement with a timeout to avoid hanging"""
    try:
        # Set statement timeout for this transaction
        conn.execute(text(f"SET LOCAL statement_timeout = '{timeout_seconds}s'"))
        conn.execute(statement)
        logger.info(f"   ✅ {description}")
        return True
    except Exception as e:
        if "lock" in str(e).lower() or "timeout" in str(e).lower():
            logger.warning(f"   ⚠️ {description} - Table locked, will retry later")
            return False
        else:
            raise e

def run_migration():
    """Apply v2.2.46/47 database migrations with lock handling"""
    
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
    logger.info("PixelProbe v2.2.46/47 Safe Database Migration")
    logger.info("=" * 50)
    logger.info(f"Database: {db_user}@{db_host}:{db_port}/{db_name}")
    
    try:
        # Create engine with shorter timeouts
        engine = create_engine(
            database_url,
            connect_args={
                'connect_timeout': 10,
                'options': '-c statement_timeout=30s'
            }
        )
        
        # Check for blocking connections (uses its own connection)
        kill_blocking_connections(engine, db_name)
        
        # Now proceed with migration using a fresh connection
        with engine.connect() as conn:
            
            # Use separate transactions for each operation
            migrations_pending = []
            
            # 1. Check last_update column
            logger.info("\n1. Checking scan_state.last_update column...")
            trans = conn.begin()
            try:
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'scan_state' AND column_name = 'last_update'
                """))
                
                if not result.fetchone():
                    logger.info("   Column missing, will add...")
                    migrations_pending.append(('last_update', 
                        "ALTER TABLE scan_state ADD COLUMN last_update TIMESTAMP",
                        "UPDATE scan_state SET last_update = start_time WHERE last_update IS NULL"))
                else:
                    logger.info("   ✅ last_update column already exists")
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.error(f"   ❌ Failed to check column: {e}")
                
            # 2. Check files_processed column
            logger.info("\n2. Checking scan_chunks.files_processed column...")
            trans = conn.begin()
            try:
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'scan_chunks' AND column_name = 'files_processed'
                """))
                
                if not result.fetchone():
                    logger.info("   Column missing, will add...")
                    migrations_pending.append(('files_processed',
                        "ALTER TABLE scan_chunks ADD COLUMN files_processed INTEGER DEFAULT 0 NOT NULL",
                        "UPDATE scan_chunks SET files_processed = files_scanned WHERE files_processed = 0"))
                else:
                    logger.info("   ✅ files_processed column already exists")
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.error(f"   ❌ Failed to check column: {e}")
            
            # Apply pending migrations with timeout
            if migrations_pending:
                logger.info(f"\n3. Applying {len(migrations_pending)} migrations...")
                
                for name, create_sql, update_sql in migrations_pending:
                    logger.info(f"\n   Adding {name} column...")
                    
                    # Try with short timeout first
                    trans = conn.begin()
                    try:
                        # Use NOWAIT to fail fast if table is locked
                        conn.execute(text(f"LOCK TABLE {'scan_state' if 'scan_state' in create_sql else 'scan_chunks'} IN ACCESS EXCLUSIVE MODE NOWAIT"))
                        conn.execute(text(create_sql))
                        if update_sql:
                            conn.execute(text(update_sql))
                        trans.commit()
                        logger.info(f"   ✅ Added {name} column")
                    except Exception as e:
                        trans.rollback()
                        if "lock" in str(e).lower():
                            logger.warning(f"   ⚠️ Table locked, trying alternative approach...")
                            
                            # Try adding column without locking
                            trans = conn.begin()
                            try:
                                # Use IF NOT EXISTS for safety
                                safe_sql = create_sql.replace("ADD COLUMN", "ADD COLUMN IF NOT EXISTS")
                                conn.execute(text(safe_sql))
                                if update_sql:
                                    conn.execute(text(update_sql))
                                trans.commit()
                                logger.info(f"   ✅ Added {name} column (alternative method)")
                            except Exception as e2:
                                trans.rollback()
                                logger.error(f"   ❌ Failed to add {name}: {e2}")
                                logger.info("\n   MANUAL FIX REQUIRED:")
                                logger.info(f"   Run this SQL manually when database is idle:")
                                logger.info(f"   {create_sql};")
                                if update_sql:
                                    logger.info(f"   {update_sql};")
                        else:
                            logger.error(f"   ❌ Failed to add {name}: {e}")
            
            # 3. Clean up stuck scans (non-blocking)
            logger.info("\n4. Cleaning up stuck scans...")
            trans = conn.begin()
            try:
                # Use shorter timeout for cleanup
                conn.execute(text("SET LOCAL statement_timeout = '2s'"))
                result = conn.execute(text("""
                    UPDATE scan_state 
                    SET phase = 'crashed',
                        is_active = FALSE,
                        error_message = 'Cleaned up by v2.2.46 migration',
                        end_time = CURRENT_TIMESTAMP
                    WHERE is_active = TRUE 
                    AND start_time < CURRENT_TIMESTAMP - INTERVAL '1 hour'
                    AND phase NOT IN ('completed', 'error', 'crashed', 'cancelled')
                """))
                
                if result.rowcount > 0:
                    logger.info(f"   ✅ Cleaned up {result.rowcount} stuck scans")
                else:
                    logger.info("   ✅ No stuck scans found")
                trans.commit()
            except Exception as e:
                trans.rollback()
                if "timeout" in str(e).lower() or "lock" in str(e).lower():
                    logger.warning("   ⚠️ Could not clean stuck scans (table locked)")
                else:
                    logger.error(f"   ❌ Cleanup failed: {e}")
            
            # 5. Final verification
            logger.info("\n5. Verifying migration...")
            success = True
            
            trans = conn.begin()
            try:
                # Check last_update
                result = conn.execute(text("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'scan_state' AND column_name = 'last_update'
                """))
                if result.fetchone():
                    logger.info("   ✅ scan_state.last_update: OK")
                else:
                    logger.warning("   ⚠️ scan_state.last_update: MISSING (manual fix needed)")
                    success = False
                
                # Check files_processed
                result = conn.execute(text("""
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'scan_chunks' AND column_name = 'files_processed'
                """))
                if result.fetchone():
                    logger.info("   ✅ scan_chunks.files_processed: OK")
                else:
                    logger.warning("   ⚠️ scan_chunks.files_processed: MISSING (manual fix needed)")
                    success = False
                
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.error(f"   ❌ Verification failed: {e}")
                success = False
            
            logger.info("\n" + "=" * 50)
            if success:
                logger.info("✅ Migration completed successfully!")
                logger.info("=" * 50)
                logger.info("\nIMPORTANT: Restart the container to apply changes:")
                logger.info("  docker-compose restart pixelprobe")
            else:
                logger.warning("⚠️ Migration partially completed")
                logger.info("=" * 50)
                logger.info("\nSome columns need manual addition.")
                logger.info("Run these SQL commands when the database is idle:")
                logger.info("\n  ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS last_update TIMESTAMP;")
                logger.info("  ALTER TABLE scan_chunks ADD COLUMN IF NOT EXISTS files_processed INTEGER DEFAULT 0;")
                logger.info("\nThen restart the container:")
                logger.info("  docker-compose restart pixelprobe")
            
            return success
                
    except Exception as e:
        logger.error(f"\n❌ Could not connect to database: {e}")
        logger.error("\nTroubleshooting:")
        logger.error("1. Check if another migration is running")
        logger.error("2. Stop PixelProbe container: docker-compose stop pixelprobe")
        logger.error("3. Run migration again")
        logger.error("4. Start PixelProbe: docker-compose start pixelprobe")
        return False

if __name__ == "__main__":
    success = run_migration()
    sys.exit(0 if success else 1)