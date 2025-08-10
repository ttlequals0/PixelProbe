"""
Database optimization utilities for handling large file databases
"""

import logging
from sqlalchemy import text, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import Pool

logger = logging.getLogger(__name__)

def setup_sqlite_optimizations(db):
    """Configure SQLite for optimal performance with large databases"""
    
    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        """Set SQLite PRAGMAs for better performance"""
        if 'sqlite' in str(dbapi_connection):
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
    
    @event.listens_for(Pool, "connect")
    def set_sqlite_pool_pragma(dbapi_connection, connection_record):
        """Set connection-level PRAGMAs"""
        if 'sqlite' in str(dbapi_connection):
            cursor = dbapi_connection.cursor()
            # Ensure each connection uses the same settings
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.close()

def optimize_for_bulk_insert(db):
    """Temporarily optimize database for bulk inserts"""
    with db.engine.connect() as conn:
        if 'sqlite' in str(conn.engine.url):
            # Disable some safety features temporarily for speed
            conn.execute(text("PRAGMA synchronous = OFF"))
            conn.execute(text("PRAGMA journal_mode = MEMORY"))
            conn.commit()

def restore_normal_operation(db):
    """Restore normal database operation after bulk inserts"""
    with db.engine.connect() as conn:
        if 'sqlite' in str(conn.engine.url):
            # Re-enable safety features
            conn.execute(text("PRAGMA synchronous = NORMAL"))
            conn.execute(text("PRAGMA journal_mode = WAL"))
            conn.commit()

def vacuum_database(db):
    """Vacuum the database to reclaim space and optimize performance"""
    with db.engine.connect() as conn:
        if 'sqlite' in str(conn.engine.url):
            logger.info("Starting database vacuum...")
            conn.execute(text("VACUUM"))
            conn.commit()
            logger.info("Database vacuum completed")

def analyze_database(db):
    """Update SQLite's internal statistics for better query planning"""
    with db.engine.connect() as conn:
        if 'sqlite' in str(conn.engine.url):
            logger.info("Analyzing database statistics...")
            conn.execute(text("ANALYZE"))
            conn.commit()
            logger.info("Database analysis completed")

def run_pragma_optimize(db):
    """Run PRAGMA optimize to update internal statistics and optimize queries"""
    with db.engine.connect() as conn:
        if 'sqlite' in str(conn.engine.url):
            logger.debug("Running PRAGMA optimize...")
            conn.execute(text("PRAGMA optimize"))
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
                run_pragma_optimize(db)
            except Exception as e:
                logger.warning(f"Failed to run periodic optimization: {e}")