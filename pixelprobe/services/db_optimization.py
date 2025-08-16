"""
Database optimization utilities for handling large file databases
"""

import logging
from sqlalchemy import text, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool

logger = logging.getLogger(__name__)

def setup_database_optimizations(db):
    """Configure database for optimal performance with large databases"""
    
    @event.listens_for(Engine, "connect")
    def set_database_optimizations(dbapi_connection, connection_record):
        """Set database optimizations for better performance"""
        db_type = str(dbapi_connection).lower()
        
        if 'sqlite' in db_type:
            cursor = dbapi_connection.cursor()
            
            # Enable Write-Ahead Logging for better concurrency
            cursor.execute("PRAGMA journal_mode = WAL")
            
            # Reduce fsync operations for better write performance
            cursor.execute("PRAGMA synchronous = NORMAL")
            
            # Increase cache size (negative = KB, default is -2000 = 2MB)
            # Set to 64MB for better performance with large databases
            cursor.execute("PRAGMA cache_size = -65536")
            
            # Increase page size for better performance with large files
            cursor.execute("PRAGMA page_size = 4096")
            
            # Enable memory-mapped I/O (256MB)
            cursor.execute("PRAGMA mmap_size = 268435456")
            
            # Optimize for faster inserts
            cursor.execute("PRAGMA temp_store = MEMORY")
            
            # Enable foreign key constraints
            cursor.execute("PRAGMA foreign_keys = ON")
            
            cursor.close()
            logger.info("SQLite optimizations applied")
        elif 'postgresql' in db_type:
            # PostgreSQL optimizations are handled via connection pool settings in config.py
            logger.info("PostgreSQL optimizations applied via connection pool settings")
    
    @event.listens_for(Pool, "connect")
    def set_pool_optimizations(dbapi_connection, connection_record):
        """Set connection-level optimizations"""
        db_type = str(dbapi_connection).lower()
        
        if 'sqlite' in db_type:
            cursor = dbapi_connection.cursor()
            # Ensure each connection uses the same settings
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.close()
        # PostgreSQL pool optimizations are handled in config.py

def optimize_for_bulk_insert(db):
    """Temporarily optimize database for bulk inserts"""
    with db.engine.connect() as conn:
        db_url = str(conn.engine.url).lower()
        if 'sqlite' in db_url:
            # Disable some safety features temporarily for speed
            conn.execute(text("PRAGMA synchronous = OFF"))
            conn.execute(text("PRAGMA journal_mode = MEMORY"))
            conn.commit()
        elif 'postgresql' in db_url:
            # PostgreSQL bulk insert optimizations
            conn.execute(text("SET synchronous_commit = OFF"))
            conn.commit()

def restore_normal_operation(db):
    """Restore normal database operation after bulk inserts"""
    with db.engine.connect() as conn:
        db_url = str(conn.engine.url).lower()
        if 'sqlite' in db_url:
            # Re-enable safety features
            conn.execute(text("PRAGMA synchronous = NORMAL"))
            conn.execute(text("PRAGMA journal_mode = WAL"))
            conn.commit()
        elif 'postgresql' in db_url:
            # Restore PostgreSQL normal settings
            conn.execute(text("SET synchronous_commit = ON"))
            conn.commit()

def vacuum_database(db):
    """Vacuum the database to reclaim space and optimize performance"""
    with db.engine.connect() as conn:
        db_url = str(conn.engine.url).lower()
        if 'sqlite' in db_url:
            logger.info("Starting database vacuum...")
            conn.execute(text("VACUUM"))
            conn.commit()
            logger.info("Database vacuum completed")
        elif 'postgresql' in db_url:
            logger.info("Starting database vacuum...")
            conn.execute(text("VACUUM ANALYZE"))
            conn.commit()
            logger.info("Database vacuum completed")

def analyze_database(db):
    """Update database's internal statistics for better query planning"""
    with db.engine.connect() as conn:
        db_url = str(conn.engine.url).lower()
        if 'sqlite' in db_url:
            logger.info("Analyzing database statistics...")
            conn.execute(text("ANALYZE"))
            conn.commit()
            logger.info("Database analysis completed")
        elif 'postgresql' in db_url:
            logger.info("Analyzing database statistics...")
            conn.execute(text("ANALYZE"))
            conn.commit()
            logger.info("Database analysis completed")

def run_database_optimize(db):
    """Run database-specific optimization commands"""
    with db.engine.connect() as conn:
        db_url = str(conn.engine.url).lower()
        if 'sqlite' in db_url:
            logger.debug("Running PRAGMA optimize...")
            conn.execute(text("PRAGMA optimize"))
            conn.commit()
        elif 'postgresql' in db_url:
            logger.debug("Running PostgreSQL ANALYZE...")
            conn.execute(text("ANALYZE"))
            conn.commit()
            
def schedule_periodic_optimization(db, interval_operations=10000):
    """Set up periodic optimization after N database operations"""
    operation_count = 0
    
    @event.listens_for(db.session, "after_commit")
    def optimize_periodically(session):
        nonlocal operation_count
        operation_count += 1
        
        if operation_count >= interval_operations:
            operation_count = 0
            try:
                run_database_optimize(db)
            except Exception as e:
                logger.warning(f"Failed to run periodic optimization: {e}")