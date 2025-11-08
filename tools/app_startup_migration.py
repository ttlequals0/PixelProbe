"""
Startup migration to add missing columns.
This handles the cancel_requested column that was added in v2.0.89.
"""

import logging
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)

def run_startup_migrations(db):
    """Run database migrations on startup to add any missing columns"""

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