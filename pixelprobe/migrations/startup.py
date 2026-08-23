"""
Database migration functions executed during PixelProbe startup.

These are run once on application startup to ensure the database schema
is up-to-date. Each migration is idempotent (safe to re-run).
"""

import os
import logging
from contextlib import contextmanager

from sqlalchemy import text, inspect, exc
from pixelprobe.constants import (CONFIG_LOG_RETENTION_DAYS, CONFIG_LOG_EXCLUDE_LOGGERS,
                                  DEFAULT_LOG_EXCLUDE_LOGGERS, SCANNER_SETTINGS)
from pixelprobe.utils.helpers import env_int

logger = logging.getLogger(__name__)

MIGRATION_ADVISORY_LOCK_ID = 7283945162

# DDL that blocks behind another session's lock (e.g. a still-running worker
# container holding idle-in-transaction locks during an app-only update) must
# fail fast, not wedge every gunicorn worker behind the migration step forever.
# Migrations are idempotent: a timed-out one is retried on the next boot.
MIGRATION_LOCK_TIMEOUT_MS = env_int('MIGRATION_LOCK_TIMEOUT_MS', 10000, floor=1000)
MIGRATION_STATEMENT_TIMEOUT_MS = env_int('MIGRATION_STATEMENT_TIMEOUT_MS', 300000, floor=10000)


def set_ddl_timeouts(conn):
    """Apply lock/statement timeouts to a migration connection.

    SET LOCAL: the timeouts must die with the migration transaction. A plain
    SET is session-scoped and survives the connection's return to the pool,
    silently imposing migration timeouts on unrelated app queries.
    """
    conn.execute(text(f"SET LOCAL lock_timeout = {MIGRATION_LOCK_TIMEOUT_MS}"))
    conn.execute(text(f"SET LOCAL statement_timeout = {MIGRATION_STATEMENT_TIMEOUT_MS}"))


@contextmanager
def migration_connection(db):
    """Engine connection with fail-fast DDL timeouts applied."""
    with db.engine.connect() as conn:
        set_ddl_timeouts(conn)
        yield conn


def run_auth_migration(db):
    """Run authentication tables migration for v2.4.0"""
    try:
        inspector = inspect(db.engine)
        existing_tables = inspector.get_table_names()

        with migration_connection(db) as conn:
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
        with migration_connection(db) as conn:
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
        with migration_connection(db) as conn:
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


def run_v2_6_0_migrations(db):
    """Run migrations for v2.6.0 - add log_entries and app_configs tables"""
    try:
        with migration_connection(db) as conn:
            # Create log_entries table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS log_entries (
                    id SERIAL PRIMARY KEY,
                    scan_id VARCHAR(64),
                    celery_task_id VARCHAR(64),
                    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    level VARCHAR(10) NOT NULL,
                    logger_name VARCHAR(200),
                    message TEXT NOT NULL,
                    traceback TEXT
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_log_scan_timestamp ON log_entries(scan_id, timestamp)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_log_timestamp ON log_entries(timestamp)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_log_level ON log_entries(level)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_log_scan_id ON log_entries(scan_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_log_celery_task_id ON log_entries(celery_task_id)"))
            logger.info("Ensured log_entries table exists")

            # Create app_configs table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS app_configs (
                    id SERIAL PRIMARY KEY,
                    key VARCHAR(100) UNIQUE NOT NULL,
                    value TEXT NOT NULL,
                    description VARCHAR(500),
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            logger.info("Ensured app_configs table exists")

            # Ensure server defaults exist on timestamp columns (fixes seed INSERT
            # failure when SQLAlchemy's create_all() created the table without
            # them). Conditional: the unconditional ALTER took an ACCESS
            # EXCLUSIVE lock on every boot and wedged startup behind any other
            # session's lock (observed 2026-06-10 during an app-only update).
            missing_default = conn.execute(text("""
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'app_configs'
                  AND column_name IN ('created_at', 'updated_at')
                  AND column_default IS NULL
            """)).fetchone()
            if missing_default:
                conn.execute(text("""
                    ALTER TABLE app_configs
                        ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP,
                        ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP
                """))
                logger.info("Added missing timestamp defaults on app_configs")

            # Seed default configuration values
            conn.execute(text("""
                INSERT INTO app_configs (key, value, description)
                VALUES (:key, '30', 'Number of days to retain log entries before automatic cleanup')
                ON CONFLICT (key) DO NOTHING
            """), {'key': CONFIG_LOG_RETENTION_DAYS})
            conn.execute(text("""
                INSERT INTO app_configs (key, value, description)
                VALUES (:key, :value, 'Comma-separated list of logger names to exclude from database storage')
                ON CONFLICT (key) DO NOTHING
            """), {'key': CONFIG_LOG_EXCLUDE_LOGGERS, 'value': DEFAULT_LOG_EXCLUDE_LOGGERS})
            logger.info("Seeded default app_configs values")

            conn.commit()
            logger.info("v2.6.0 migration completed successfully")

    except Exception as e:
        logger.error(f"Migration v2.6.0 failed: {e}")


def run_v2_6_33_migrations(db):
    """Ensure scan_state and scan_chunks tables have all columns the ORM models expect.

    db.create_all() creates new tables but does NOT add columns to existing ones.
    Several columns were added to the models over time without ALTER TABLE migrations,
    causing IndexError ('tuple index out of range') when SQLAlchemy tries to load rows
    with fewer columns than the mapper expects.
    """
    # (table, column, sql_type_with_default)
    missing_cols = [
        ('scan_state', 'num_workers', 'INTEGER NOT NULL DEFAULT 1'),
        ('scan_state', 'files_added', 'INTEGER NOT NULL DEFAULT 0'),
        ('scan_state', 'files_updated', 'INTEGER NOT NULL DEFAULT 0'),
        ('scan_chunks', 'files_processed', 'INTEGER NOT NULL DEFAULT 0'),
        ('scan_chunks', 'is_complete', 'BOOLEAN NOT NULL DEFAULT FALSE'),
        ('scan_chunks', 'celery_task_id', 'VARCHAR(36)'),
        ('scan_chunks', 'files_added', 'INTEGER NOT NULL DEFAULT 0'),
    ]

    try:
        with migration_connection(db) as conn:
            for table, column, col_type in missing_cols:
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :tbl AND column_name = :col"
                ), {'tbl': table, 'col': column}).fetchone()
                if not exists:
                    logger.info(f"Adding missing column {table}.{column} ({col_type})")
                    conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                    ))
            conn.commit()
            logger.info("v2.6.33 schema sync completed")
    except Exception as e:
        logger.error(f"Migration v2.6.33 failed: {e}")


def run_v2_6_49_migrations(db):
    """Chunk-engine convergence schema changes.

    - scan_state.scan_type: lets the finalizer (incl. the sweeper backstop)
      pick the right report type without threading it through task signatures.
    - scan_chunks.directory_path -> TEXT: FCP range chunks store two full file
      paths as JSON, which can exceed the old VARCHAR(500).
    """
    try:
        with migration_connection(db) as conn:
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'scan_state' AND column_name = 'scan_type'"
            )).fetchone()
            if not exists:
                logger.info("Adding column scan_state.scan_type (VARCHAR(20))")
                conn.execute(text(
                    "ALTER TABLE scan_state ADD COLUMN scan_type VARCHAR(20)"
                ))

            current_type = conn.execute(text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'scan_chunks' AND column_name = 'directory_path'"
            )).fetchone()
            if current_type and current_type[0] != 'text':
                logger.info("Widening scan_chunks.directory_path to TEXT")
                conn.execute(text(
                    "ALTER TABLE scan_chunks ALTER COLUMN directory_path TYPE TEXT"
                ))
            conn.commit()
            logger.info("v2.6.49 schema sync completed")
    except Exception as e:
        logger.error(f"Migration v2.6.49 failed: {e}")


def run_v2_6_53_migrations(db):
    """Append celery.app.trace to the stored log-exclusion list if absent.

    The exclude config is seeded once (ON CONFLICT DO NOTHING), so default
    changes never reach existing installs. Additive so user customizations
    survive. celery.app.trace logs one row per task; at maintenance-run rates
    that WAL volume drives 5-minute checkpoint IO storms.
    """
    try:
        with migration_connection(db) as conn:
            conn.execute(text("""
                UPDATE app_configs
                SET value = value || ',celery.app.trace'
                WHERE key = :key
                  AND ',' || replace(value, ' ', '') || ',' NOT LIKE '%,celery.app.trace,%'
            """), {'key': CONFIG_LOG_EXCLUDE_LOGGERS})
            conn.commit()
            logger.info("v2.6.53 log-exclusion backfill completed")
    except Exception as e:
        logger.error(f"Migration v2.6.53 failed: {e}")


def run_v2_6_60_migrations(db):
    """Add scan_schedules.time_budget_minutes for budgeted integrity runs.

    NULL = unlimited (current behavior). Only meaningful for
    scan_type='file_changes'; the API rejects it on other types.
    """
    try:
        with migration_connection(db) as conn:
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = 'scan_schedules' AND column_name = 'time_budget_minutes'"
            )).fetchone()
            if not exists:
                logger.info("Adding column scan_schedules.time_budget_minutes (INTEGER)")
                conn.execute(text(
                    "ALTER TABLE scan_schedules ADD COLUMN time_budget_minutes INTEGER"
                ))
            conn.commit()
            logger.info("v2.6.60 schedule budget migration completed")
    except Exception as e:
        logger.error(f"Migration v2.6.60 failed: {e}")


def run_v2_6_61_migrations(db):
    """Bitrot classification columns on scan_results.

    bitrot_suspected: hash changed while mtime did not - flagged for review.
    bitrot_detected_date/bitrot_details: permanent detection record.
    bitrot_candidate_hash/bitrot_stable_checks: auto-expire state machine.
    mtime_baseline_utc: false for all pre-upgrade rows, whose last_modified
    was written as naive local time; bitrot classification requires a
    trusted (UTC) baseline, so those rows re-baseline on first check.
    """
    columns = [
        ("bitrot_suspected", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("bitrot_detected_date", "TIMESTAMP"),
        ("bitrot_details", "TEXT"),
        ("bitrot_candidate_hash", "VARCHAR(64)"),
        ("bitrot_stable_checks", "INTEGER NOT NULL DEFAULT 0"),
        ("mtime_baseline_utc", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ]
    try:
        with migration_connection(db) as conn:
            for name, ddl in columns:
                exists = conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'scan_results' AND column_name = :col"
                ), {'col': name}).fetchone()
                if not exists:
                    logger.info(f"Adding column scan_results.{name}")
                    conn.execute(text(
                        f"ALTER TABLE scan_results ADD COLUMN {name} {ddl}"
                    ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_scan_results_bitrot_suspected "
                "ON scan_results(bitrot_suspected)"
            ))
            # Supports the rolling-queue fetch ordering exactly; without it
            # every ~10k-row batch fetch is a full-table scan + top-N sort
            # (~100 scans per run at 1M files).
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_scan_results_integrity_queue "
                "ON scan_results (bitrot_suspected DESC, "
                "last_integrity_check_date ASC NULLS FIRST, id ASC)"
            ))
            conn.commit()
            logger.info("v2.6.61 bitrot classification migration completed")
    except Exception as e:
        logger.error(f"Migration v2.6.61 failed: {e}")


def run_v2_8_7_migrations(db):
    """Move scanner settings out of the environment and into the database.

    These were read from environment variables at import time, so changing one
    meant editing a compose file and restarting. They are now stored rows the
    API and UI can edit while a scan runs.

    An existing deployment may already have some of these set in its
    environment. Those values are copied across once, here, so behaviour does
    not change under an operator who never opens the settings screen. After
    this runs the stored value is authoritative and the variable is ignored.
    Rows already present are left alone, which is what makes the migration
    idempotent and stops it undoing later edits.
    """
    try:
        with migration_connection(db) as conn:
            seeded = []
            for spec in SCANNER_SETTINGS:
                env_name = spec.get('legacy_env')
                raw = os.environ.get(env_name) if env_name else None
                if raw is None:
                    continue
                result = conn.execute(text("""
                    INSERT INTO app_configs (key, value, description)
                    VALUES (:key, :value, :description)
                    ON CONFLICT (key) DO NOTHING
                """), {'key': spec['key'], 'value': str(raw).strip(),
                       'description': spec['label']})
                if result.rowcount:
                    seeded.append(f"{spec['key']}={raw} (from {env_name})")

            conn.commit()
            if seeded:
                logger.info(
                    "Adopted %d scanner setting(s) from the environment: %s",
                    len(seeded), '; '.join(seeded))
            else:
                logger.info("No environment scanner settings to adopt")

    except Exception as e:
        logger.error(f"Migration v2.8.7 failed: {e}")


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
        "CREATE INDEX IF NOT EXISTS idx_file_path_status ON scan_results(file_path, scan_status)",
        "CREATE INDEX IF NOT EXISTS idx_status_file_path ON scan_results(scan_status, file_path)"
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

    logger.info("Adopting scanner settings from the environment...")
    try:
        run_v2_8_7_migrations(db)
    except Exception as e:
        logger.error(f"v2.8.7 migration failed: {e}")

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

    logger.info("Running v2.6.0 migration...")
    try:
        run_v2_6_0_migrations(db)
        logger.info("v2.6.0 migration completed successfully")
    except Exception as e:
        logger.error(f"v2.6.0 migration failed: {e}")

    logger.info("Running v2.6.33 migration (schema sync)...")
    try:
        run_v2_6_33_migrations(db)
    except Exception as e:
        logger.error(f"v2.6.33 migration failed: {e}")

    logger.info("Running v2.6.49 migration (chunk engine schema)...")
    try:
        run_v2_6_49_migrations(db)
    except Exception as e:
        logger.error(f"v2.6.49 migration failed: {e}")

    logger.info("Running v2.6.53 migration (log-exclusion backfill)...")
    try:
        run_v2_6_53_migrations(db)
    except Exception as e:
        logger.error(f"v2.6.53 migration failed: {e}")

    logger.info("Running v2.6.60 migration (schedule time budget)...")
    try:
        run_v2_6_60_migrations(db)
    except Exception as e:
        logger.error(f"v2.6.60 migration failed: {e}")

    logger.info("Running v2.6.61 migration (bitrot classification)...")
    try:
        run_v2_6_61_migrations(db)
    except Exception as e:
        logger.error(f"v2.6.61 migration failed: {e}")

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
            # Bound the wait: if the lock holder hangs for a non-DDL reason,
            # this would otherwise be the last remaining infinite-wait path.
            # SET LOCAL scopes the timeout to this connection's open
            # transaction (rolled back at close), so nothing leaks to the pool.
            lock_conn.execute(text(f"SET LOCAL statement_timeout = {MIGRATION_STATEMENT_TIMEOUT_MS}"))
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
