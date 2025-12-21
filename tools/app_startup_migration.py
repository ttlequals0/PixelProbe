"""
Startup migration to add missing columns and optimize database indexes.
Handles schema changes and P0/P1 audit optimizations from v2.4.160.
"""

import logging
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


def run_index_optimizations(db):
    """
    Drop duplicate indexes and add composite indexes (v2.4.160 P0/P1 optimizations)
    Saves 625MB of disk space and improves query performance
    """

    # List of duplicate indexes to drop (they duplicate SQLAlchemy-generated indexes)
    duplicate_indexes = [
        'idx_file_path',
        'idx_file_hash',
        'idx_scan_date',
        'idx_discovered_date',
        'idx_last_modified',
        'idx_is_corrupted',
        'idx_marked_as_good',
        'idx_scan_status'
    ]

    # Composite indexes to add for common query patterns
    composite_indexes = [
        {
            'name': 'idx_status_corrupted',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_status_corrupted ON scan_results(scan_status, is_corrupted)',
            'description': 'Composite index for status/corruption queries'
        },
        {
            'name': 'idx_scan_date_desc',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_scan_date_desc ON scan_results(scan_date DESC)',
            'description': 'Descending index for recent scan lookups'
        },
        {
            'name': 'idx_scan_state_celery_task_id',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_scan_state_celery_task_id ON scan_state(celery_task_id)',
            'description': 'Index for Celery task ID lookups (v2.4.196)'
        },
        # P1 audit fix: Additional composite indexes (v2.5.26)
        {
            'name': 'idx_scan_date_corrupted',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_scan_date_corrupted ON scan_results(scan_date DESC, is_corrupted)',
            'description': 'Composite index for date-ordered corruption queries (P1 audit)'
        },
        {
            'name': 'idx_exists_status',
            'sql': 'CREATE INDEX IF NOT EXISTS idx_exists_status ON scan_results(file_exists, scan_status)',
            'description': 'Composite index for file existence/status queries (P1 audit)'
        }
    ]

    try:
        with db.engine.begin() as conn:
            # Drop duplicate indexes
            for index_name in duplicate_indexes:
                try:
                    conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
                    logger.info(f"Dropped duplicate index: {index_name}")
                except Exception as e:
                    # Index might not exist, that's ok
                    logger.debug(f"Could not drop index {index_name}: {e}")

            # Add composite indexes
            for index_info in composite_indexes:
                try:
                    conn.execute(text(index_info['sql']))
                    logger.info(f"Created composite index: {index_info['name']} - {index_info['description']}")
                except Exception as e:
                    err_str = str(e).lower()
                    if "already exists" in err_str or "duplicate" in err_str:
                        logger.debug(f"Index {index_info['name']} already exists")
                    else:
                        logger.warning(f"Could not create index {index_info['name']}: {e}")

        logger.info("Index optimizations completed successfully")

    except Exception as e:
        logger.error(f"Error during index optimizations: {e}")
        # Don't fail startup for index optimization errors

def run_startup_migrations(db):
    """Run database migrations on startup to add any missing columns and optimize indexes"""

    migrations = [
        # Add cancel_requested to cleanup_state
        {
            'table': 'cleanup_state',
            'check_sql': "SELECT cancel_requested FROM cleanup_state LIMIT 0",
            'migration_sql': "ALTER TABLE cleanup_state ADD COLUMN cancel_requested BOOLEAN DEFAULT 0",
            'description': 'Adding cancel_requested to cleanup_state'
        },
        # Add cancel_requested to file_changes_state
        {
            'table': 'file_changes_state',
            'check_sql': "SELECT cancel_requested FROM file_changes_state LIMIT 0",
            'migration_sql': "ALTER TABLE file_changes_state ADD COLUMN cancel_requested BOOLEAN DEFAULT 0",
            'description': 'Adding cancel_requested to file_changes_state'
        },
        # Add output_rotation_enabled to scan_results (v2.2.4)
        {
            'table': 'scan_results',
            'check_sql': "SELECT output_rotation_enabled FROM scan_results LIMIT 0",
            'migration_sql': "ALTER TABLE scan_results ADD COLUMN output_rotation_enabled BOOLEAN",
            'description': 'Adding output_rotation_enabled to scan_results'
        },
        # Add crash recovery columns to scan_state (v2.2.6)
        {
            'table': 'scan_state',
            'check_sql': "SELECT crash_count FROM scan_state LIMIT 0",
            'migration_sql': "ALTER TABLE scan_state ADD COLUMN crash_count INTEGER DEFAULT 0",
            'description': 'Adding crash_count to scan_state'
        },
        {
            'table': 'scan_state',
            'check_sql': "SELECT last_crash_time FROM scan_state LIMIT 0",
            'migration_sql': "ALTER TABLE scan_state ADD COLUMN last_crash_time TIMESTAMP",
            'description': 'Adding last_crash_time to scan_state'
        },
        # Add celery_task_id to scan_state (v2.4.196)
        {
            'table': 'scan_state',
            'check_sql': "SELECT celery_task_id FROM scan_state LIMIT 0",
            'migration_sql': "ALTER TABLE scan_state ADD COLUMN celery_task_id VARCHAR(36)",
            'description': 'Adding celery_task_id to scan_state'
        },
        # Add resumable scan fields to scan_state (v2.4.196)
        {
            'table': 'scan_state',
            'check_sql': "SELECT current_chunk_index FROM scan_state LIMIT 0",
            'migration_sql': "ALTER TABLE scan_state ADD COLUMN current_chunk_index INTEGER NOT NULL DEFAULT 0",
            'description': 'Adding current_chunk_index to scan_state'
        },
        {
            'table': 'scan_state',
            'check_sql': "SELECT total_chunks FROM scan_state LIMIT 0",
            'migration_sql': "ALTER TABLE scan_state ADD COLUMN total_chunks INTEGER NOT NULL DEFAULT 0",
            'description': 'Adding total_chunks to scan_state'
        },
        {
            'table': 'scan_state',
            'check_sql': "SELECT chunks_completed FROM scan_state LIMIT 0",
            'migration_sql': "ALTER TABLE scan_state ADD COLUMN chunks_completed TEXT",
            'description': 'Adding chunks_completed to scan_state'
        },
        # Add last_update to scan_state (v2.4.196)
        {
            'table': 'scan_state',
            'check_sql': "SELECT last_update FROM scan_state LIMIT 0",
            'migration_sql': "ALTER TABLE scan_state ADD COLUMN last_update TIMESTAMP",
            'description': 'Adding last_update to scan_state'
        }
    ]

    # Use engine.connect() instead of db.session to avoid transaction/context issues
    for migration in migrations:
        try:
            # Try to select the column - if it fails, the column doesn't exist
            with db.engine.connect() as conn:
                conn.execute(text(migration['check_sql']))
        except (OperationalError, ProgrammingError):
            # Column doesn't exist, add it
            try:
                logger.info(f"Running migration: {migration['description']}")
                with db.engine.begin() as conn:
                    conn.execute(text(migration['migration_sql']))
                logger.info(f"Migration successful: {migration['description']}")
            except (OperationalError, ProgrammingError) as e:
                err_str = str(e).lower()
                if "duplicate column name" in err_str or "already exists" in err_str:
                    logger.info(f"Column already exists, skipping: {migration['description']}")
                else:
                    logger.error(f"Failed to run migration {migration['description']}: {e}")
            except Exception as e:
                logger.error(f"Failed to run migration {migration['description']}: {e}")
        except Exception as e:
            # Some other error - log it but continue
            logger.warning(f"Error checking {migration['table']}: {e}")

    # Run index optimizations (v2.4.160 - P0/P1 audit fixes)
    run_index_optimizations(db)