"""
Startup migration to add missing columns.
This handles the cancel_requested column that was added in v2.0.89.
"""

import logging
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

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
        }
    ]
    
    for migration in migrations:
        try:
            # Try to select the column - if it fails, the column doesn't exist
            db.session.execute(text(migration['check_sql']))
        except OperationalError:
            # Column doesn't exist, add it
            try:
                logger.info(f"Running migration: {migration['description']}")
                db.session.execute(text(migration['migration_sql']))
                db.session.commit()
                logger.info(f"Migration successful: {migration['description']}")
            except OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    logger.info(f"Column already exists, skipping: {migration['description']}")
                else:
                    logger.error(f"Failed to run migration {migration['description']}: {e}")
                db.session.rollback()
            except Exception as e:
                logger.error(f"Failed to run migration {migration['description']}: {e}")
                db.session.rollback()
        except Exception as e:
            # Some other error - log it but continue
            logger.warning(f"Error checking {migration['table']}: {e}")