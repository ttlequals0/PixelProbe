"""
Database migration functions executed during PixelProbe startup.

These are run once on application startup to ensure the database schema
is up-to-date. Each migration is idempotent (safe to re-run).
"""

import os
import logging
from sqlalchemy import text, inspect, exc

logger = logging.getLogger(__name__)

MIGRATION_ADVISORY_LOCK_ID = 7283945162


def run_auth_migration(db):
    """Run authentication tables migration for v2.4.0"""
    try:
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        with db.engine.connect() as conn:
            if 'users' not in existing_tables:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        username VARCHAR(80) UNIQUE NOT NULL,
                        email VARCHAR(120) UNIQUE NOT NULL,
                        password_hash VARCHAR(128) NOT NULL,
                        is_admin BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMP WITH TIME ZONE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        first_setup_required BOOLEAN NOT NULL DEFAULT FALSE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))
                logger.info("Created users table via migration")

            if 'api_tokens' not in existing_tables:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS api_tokens (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        token VARCHAR(64) UNIQUE NOT NULL,
                        description VARCHAR(200),
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_used TIMESTAMP WITH TIME ZONE,
                        expires_at TIMESTAMP WITH TIME ZONE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_tokens_token ON api_tokens(token)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id)"))
                logger.info("Created api_tokens table via migration")

            logger.info("Authentication tables migration completed")
            conn.commit()

    except Exception as e:
        logger.warning(f"Authentication migration encountered issues: {e}")


def run_v2_4_35_migrations(db):
    """Run migrations for v2.4.35 - add last_heartbeat column to file_changes_state"""
    try:
        with db.engine.connect() as conn:
            table_check = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name = 'file_changes_state'
            """))

            if not table_check.fetchone():
                logger.debug("file_changes_state table does not exist - skipping migration (new installation)")
                return

            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'file_changes_state'
                AND column_name = 'last_heartbeat'
            """))

            if not result.fetchone():
                logger.info("Applying migration: Adding last_heartbeat column to file_changes_state table")
                conn.execute(text("""
                    ALTER TABLE file_changes_state
                    ADD COLUMN last_heartbeat TIMESTAMP WITH TIME ZONE
                """))
                conn.commit()
                logger.info("Migration completed: last_heartbeat column added successfully")
            else:
                logger.debug("Migration already applied: last_heartbeat column exists")

    except Exception as e:
        logger.error(f"Migration v2.4.35 failed: {e}")


def run_v2_4_113_migrations(db):
    """Run migrations for v2.4.113 - add last_integrity_check_date column to scan_results"""
    try:
        with db.engine.connect() as conn:
            table_check = conn.execute(text("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_name = 'scan_results'
            """))

            if not table_check.fetchone():
                logger.debug("scan_results table does not exist - skipping migration (new installation)")
                return

            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'scan_results'
                AND column_name = 'last_integrity_check_date'
            """))

            if not result.fetchone():
                logger.info("Applying migration: Adding last_integrity_check_date column to scan_results table")
                conn.execute(text("""
                    ALTER TABLE scan_results
                    ADD COLUMN last_integrity_check_date TIMESTAMP
                """))
                conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS idx_scan_results_last_integrity_check
                    ON scan_results(last_integrity_check_date)
                """))
                conn.commit()
                logger.info("Migration completed: last_integrity_check_date column and index added successfully")
            else:
                logger.debug("Migration already applied: last_integrity_check_date column exists")

    except Exception as e:
        logger.error(f"Migration v2.4.113 failed: {e}")


def create_performance_indexes(db):
    """Create performance indexes"""
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_scan_status ON scan_results(scan_status)",
        "CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_results(scan_date)",
        "CREATE INDEX IF NOT EXISTS idx_is_corrupted ON scan_results(is_corrupted)",
        "CREATE INDEX IF NOT EXISTS idx_marked_as_good ON scan_results(marked_as_good)",
        "CREATE INDEX IF NOT EXISTS idx_discovered_date ON scan_results(discovered_date)",
        "CREATE INDEX IF NOT EXISTS idx_file_hash ON scan_results(file_hash)",
        "CREATE INDEX IF NOT EXISTS idx_last_modified ON scan_results(last_modified)",
        "CREATE INDEX IF NOT EXISTS idx_file_path ON scan_results(file_path)",
        "CREATE INDEX IF NOT EXISTS idx_status_date ON scan_results(scan_status, scan_date)",
        "CREATE INDEX IF NOT EXISTS idx_corrupted_good ON scan_results(is_corrupted, marked_as_good)",
        "CREATE INDEX IF NOT EXISTS idx_file_path_status ON scan_results(file_path, scan_status)"
    ]

    logger.info("Creating performance indexes...")
    created_count = 0
    for index_sql in indexes:
        try:
            with db.engine.begin() as conn:
                conn.execute(text(index_sql))
            created_count += 1
        except Exception as e:
            if 'already exists' not in str(e).lower() and 'does not exist' not in str(e).lower():
                logger.debug(f"Could not create index: {e}")

    if created_count > 0:
        logger.info(f"Created {created_count} performance indexes")
    else:
        logger.debug("All performance indexes already exist")


def _run_all_migrations(db):
    """Execute all database migrations. Called by migrate_database() after acquiring lock."""
    from tools.app_startup_migration import run_startup_migrations

    logger.info("Running startup migrations...")
    try:
        run_startup_migrations(db)
        logger.info("Startup migrations completed successfully")
    except Exception as e:
        logger.error(f"Startup migration failed: {e}")

    logger.info("Checking authentication tables...")
    try:
        run_auth_migration(db)
        logger.info("Authentication tables verified")
    except Exception as e:
        logger.error(f"Authentication migration failed: {e}")

    logger.info("Running v2.4.35 migration...")
    try:
        run_v2_4_35_migrations(db)
        logger.info("v2.4.35 migration completed successfully")
    except Exception as e:
        logger.error(f"v2.4.35 migration failed: {e}")

    logger.info("Running v2.4.113 migration...")
    try:
        run_v2_4_113_migrations(db)
        logger.info("v2.4.113 migration completed successfully")
    except Exception as e:
        logger.error(f"v2.4.113 migration failed: {e}")

    logger.info("Creating performance indexes...")
    try:
        create_performance_indexes(db)
        logger.info("Performance indexes created successfully")
    except Exception as e:
        logger.error(f"Failed to create performance indexes: {e}")

    logger.info("Database initialization completed")


def migrate_database(db):
    """Run database migrations - uses PostgreSQL advisory lock to coordinate across containers.

    Advisory locks work across all connections to the same database, unlike file locks
    which are scoped to a single container's filesystem.
    """
    lock_conn = None
    try:
        lock_conn = db.engine.connect()

        result = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
        )
        acquired = result.scalar()

        if acquired:
            logger.info(f"Acquired PostgreSQL advisory lock in process {os.getpid()}, running migrations")
            try:
                _run_all_migrations(db)
            except Exception as mig_err:
                logger.error(f"Migration error (lock held): {mig_err}")
            finally:
                lock_conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
                )
                logger.info("Released PostgreSQL advisory lock")
        else:
            logger.info(f"Migrations already running in another process, waiting for completion (process {os.getpid()})...")
            lock_conn.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
            )
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
            )
            logger.info(f"Migrations completed by another process, continuing startup in process {os.getpid()}")

    except Exception as e:
        logger.warning(f"Could not use advisory lock ({e}), running migrations without coordination")
        _run_all_migrations(db)

    finally:
        if lock_conn is not None:
            try:
                lock_conn.close()
            except Exception:
                pass
