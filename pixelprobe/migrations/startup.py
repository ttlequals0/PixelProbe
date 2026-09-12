"""
Database migration functions executed during PixelProbe startup.

These are run once on application startup to ensure the database schema
is up-to-date. Each migration is idempotent (safe to re-run).
"""

import os
import logging
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import text, inspect, exc
from pixelprobe.constants import (CONFIG_LOG_RETENTION_DAYS, CONFIG_LOG_EXCLUDE_LOGGERS,
                                  DEFAULT_LOG_EXCLUDE_LOGGERS, SCANNER_SETTINGS)
from pixelprobe.models import CleanupState
from pixelprobe.utils.helpers import env_int
from pixelprobe.utils.overrides import classify_findings, encode_verdict

logger = logging.getLogger(__name__)

MIGRATION_ADVISORY_LOCK_ID = 7283945162

# DDL that blocks behind another session's lock (e.g. a still-running worker
# container holding idle-in-transaction locks during an app-only update) must
# fail fast, not wedge every gunicorn worker behind the migration step forever.
# Migrations are idempotent: a timed-out one is retried on the next boot.
MIGRATION_LOCK_TIMEOUT_MS = env_int('MIGRATION_LOCK_TIMEOUT_MS', 10000, floor=1000)
MIGRATION_STATEMENT_TIMEOUT_MS = env_int('MIGRATION_STATEMENT_TIMEOUT_MS', 300000, floor=10000)
_migration_owner_connection = ContextVar('migration_owner_connection', default=None)


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
    owner_connection = _migration_owner_connection.get()
    if owner_connection is not None:
        set_ddl_timeouts(owner_connection)
        try:
            yield owner_connection
        except Exception:
            owner_connection.rollback()
            raise
        return
    with db.engine.connect() as conn:
        set_ddl_timeouts(conn)
        yield conn


@contextmanager
def migration_owner_connection(connection):
    """Make all startup migration helpers use the advisory-lock connection."""
    token = _migration_owner_connection.set(connection)
    try:
        yield
    finally:
        _migration_owner_connection.reset(token)


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
                        is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_login TIMESTAMP WITH TIME ZONE,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        first_setup_required BOOLEAN NOT NULL DEFAULT FALSE
                    )
                """))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))
                logger.info("Created users table via migration")

            conn.execute(text(
                "ALTER TABLE users ALTER COLUMN is_admin SET DEFAULT FALSE"
            ))

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
        raise


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
        raise


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
        raise


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
        raise


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
        raise


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
        raise


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
        raise


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
        raise


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
        raise


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
        raise


def run_v2_8_8_migrations(db):
    """Scope the mark-as-good override to what was actually excused.

    Adds the columns recording which file version and which finding class a
    mark-as-good judged. Existing marked rows are backfilled treating the
    upgrade as the review moment: the current hash becomes the reviewed hash,
    and the excused class is derived from whatever details the row carries.
    Rows with no stored details keep a NULL verdict, which excuses everything,
    so a mark placed before scoping existed keeps its old behaviour.
    """
    try:
        with migration_connection(db) as conn:
            existing = {c['name'] for c in inspect(conn).get_columns('scan_results')}
            for name, ddl in (
                ('marked_good_hash', 'VARCHAR(64)'),
                ('marked_good_date', 'TIMESTAMP'),
                ('marked_good_verdict', 'VARCHAR(128)'),
            ):
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE scan_results ADD COLUMN {name} {ddl}'))
                    logger.info(f"Added scan_results.{name}")
            # Commit the columns before touching rows: a backfill failure must
            # leave the schema in place (the model declares these columns, so
            # rolling them back would fail every ScanResult query app-wide)
            conn.commit()

            rows = conn.execute(text("""
                SELECT id, corruption_details, warning_details
                FROM scan_results
                WHERE marked_as_good = TRUE AND marked_good_date IS NULL
            """)).fetchall()
            for row in rows:
                verdict = encode_verdict(classify_findings(row[1], row[2]))
                conn.execute(text("""
                    UPDATE scan_results
                    SET marked_good_hash = file_hash,
                        marked_good_date = (now() AT TIME ZONE 'utc'),
                        marked_good_verdict = :verdict
                    WHERE id = :id
                """), {'id': row[0], 'verdict': verdict})
            conn.commit()
            if rows:
                logger.info(f"Backfilled override scope for {len(rows)} marked-good file(s)")

    except Exception as e:
        logger.error(f"Migration v2.8.8 failed: {e}")
        raise


def run_v2_8_12_lifecycle_migrations(db):
    """Persist scan scope, task ownership, immutable results, and integrity outcomes."""
    statements = (
        "ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE scan_state ADD COLUMN IF NOT EXISTS dispatch_generation INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS last_integrity_attempt_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS last_integrity_success_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE scan_results ADD COLUMN IF NOT EXISTS last_integrity_outcome VARCHAR(32)",
        "ALTER TABLE file_changes_state ADD COLUMN IF NOT EXISTS integrity_attempted INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE file_changes_state ADD COLUMN IF NOT EXISTS integrity_successful INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE file_changes_state ADD COLUMN IF NOT EXISTS integrity_errors INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE file_changes_state ADD COLUMN IF NOT EXISTS integrity_unavailable INTEGER NOT NULL DEFAULT 0",
        "CREATE TABLE IF NOT EXISTS scan_run_roots (id SERIAL PRIMARY KEY, scan_id VARCHAR(64) NOT NULL, root_path TEXT NOT NULL, resolved_path TEXT, status VARCHAR(20) NOT NULL DEFAULT 'pending', error_message TEXT, discovered_count INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP WITH TIME ZONE, CONSTRAINT uq_scan_run_roots_scan_root UNIQUE (scan_id, root_path))",
        "CREATE TABLE IF NOT EXISTS scan_run_files (id SERIAL PRIMARY KEY, scan_id VARCHAR(64) NOT NULL, scan_result_id INTEGER, file_path TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', outcome VARCHAR(32), file_hash VARCHAR(64), file_size BIGINT, last_modified TIMESTAMP WITH TIME ZONE, is_corrupted BOOLEAN, has_warnings BOOLEAN, corruption_details TEXT, warning_details TEXT, file_type VARCHAR(100), scan_tool VARCHAR(50), scan_output TEXT, marked_as_good BOOLEAN, error_message TEXT, claimed_at TIMESTAMP WITH TIME ZONE, completed_at TIMESTAMP WITH TIME ZONE, CONSTRAINT uq_scan_run_files_scan_path UNIQUE (scan_id, file_path))",
        "CREATE TABLE IF NOT EXISTS scan_tasks (id SERIAL PRIMARY KEY, scan_id VARCHAR(64) NOT NULL, chunk_id INTEGER, purpose VARCHAR(32) NOT NULL, celery_task_id VARCHAR(64) NOT NULL UNIQUE, generation INTEGER NOT NULL DEFAULT 0, status VARCHAR(20) NOT NULL DEFAULT 'queued', payload JSONB, dispatch_attempts INTEGER NOT NULL DEFAULT 0, dispatch_lease_expires_at TIMESTAMP WITH TIME ZONE, error_message TEXT, created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP WITH TIME ZONE)",
        "ALTER TABLE scan_tasks ADD COLUMN IF NOT EXISTS payload JSONB",
        "ALTER TABLE scan_tasks ADD COLUMN IF NOT EXISTS dispatch_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE scan_tasks ADD COLUMN IF NOT EXISTS dispatch_lease_expires_at TIMESTAMP WITH TIME ZONE",
        "CREATE INDEX IF NOT EXISTS idx_scan_tasks_recovery ON scan_tasks (status, dispatch_lease_expires_at, id)",
        "CREATE TABLE IF NOT EXISTS scan_notification_outbox (id SERIAL PRIMARY KEY, scan_id VARCHAR(64) NOT NULL UNIQUE, event VARCHAR(64) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, error_message TEXT, created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, delivered_at TIMESTAMP WITH TIME ZONE, payload JSONB, targets_initialized BOOLEAN NOT NULL DEFAULT false, terminal_reason VARCHAR(64))",
        "ALTER TABLE scan_notification_outbox ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE scan_notification_outbox ADD COLUMN IF NOT EXISTS payload JSONB",
        "ALTER TABLE scan_notification_outbox ADD COLUMN IF NOT EXISTS targets_initialized BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE scan_notification_outbox ADD COLUMN IF NOT EXISTS terminal_reason VARCHAR(64)",
        "CREATE TABLE IF NOT EXISTS scan_notification_deliveries (id SERIAL PRIMARY KEY, outbox_id INTEGER NOT NULL REFERENCES scan_notification_outbox(id) ON DELETE CASCADE, rule_id INTEGER, provider_id INTEGER, provider_type VARCHAR(20), provider_config JSONB, conditions JSONB, priority VARCHAR(10) NOT NULL DEFAULT 'normal', status VARCHAR(20) NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, lease_expires_at TIMESTAMP WITH TIME ZONE, lease_token VARCHAR(36), outcome VARCHAR(32), error_message TEXT, skip_reason VARCHAR(64), created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, delivered_at TIMESTAMP WITH TIME ZONE, CONSTRAINT uq_scan_notification_delivery_rule UNIQUE (outbox_id, rule_id))",
        "ALTER TABLE scan_notification_deliveries ADD COLUMN IF NOT EXISTS conditions JSONB",
        "CREATE INDEX IF NOT EXISTS idx_scan_notification_delivery_claim ON scan_notification_deliveries (outbox_id, status, lease_expires_at, id)",
        "CREATE INDEX IF NOT EXISTS idx_scan_run_files_claim ON scan_run_files (scan_id, status, id)",
        "CREATE INDEX IF NOT EXISTS idx_scan_results_integrity_attempt ON scan_results (last_integrity_attempt_at ASC NULLS FIRST, id ASC)",
        "ALTER TABLE scan_run_files ADD COLUMN IF NOT EXISTS file_type VARCHAR(100)",
        "ALTER TABLE scan_run_files ADD COLUMN IF NOT EXISTS scan_tool VARCHAR(50)",
        "ALTER TABLE scan_run_files ADD COLUMN IF NOT EXISTS scan_output TEXT",
        "ALTER TABLE scan_run_files ADD COLUMN IF NOT EXISTS marked_as_good BOOLEAN",
        """UPDATE scan_results
           SET scan_status = CASE lower(trim(coalesce(scan_tool, '')))
               WHEN 'error' THEN 'error'
               WHEN 'unsupported' THEN 'unsupported'
           END
           WHERE scan_status = 'completed'
             AND lower(trim(coalesce(scan_tool, ''))) IN ('error', 'unsupported')""",
    )
    with migration_connection(db) as conn:
        for statement in statements:
            conn.execute(text(statement))
        conn.commit()


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
            with migration_connection(db) as conn:
                conn.execute(text(index_sql))
                conn.commit()
            created_count += 1
        except Exception as e:
            if 'already exists' not in str(e).lower() and 'does not exist' not in str(e).lower():
                logger.debug(f"Could not create index: {e}")

    if created_count > 0:
        logger.info(f"Created {created_count} performance indexes")
    else:
        logger.debug("All performance indexes already exist")


def run_v2_8_9_migrations(db):
    """Record how many entries a cleanup kept because it could not confirm them.

    A cleanup that holds records back has to be able to say so afterwards, and
    the count is what the UI offers to act on.
    """
    # From the model, not spelled out: naming the wrong table here adds nothing,
    # logs the failure, and leaves every query for a column the model declares
    # failing against a database that does not have it.
    table = CleanupState.__tablename__
    try:
        with migration_connection(db) as conn:
            existing = {c['name'] for c in inspect(conn).get_columns(table)}
            if 'records_kept' not in existing:
                conn.execute(text(
                    f'ALTER TABLE {table} ADD COLUMN records_kept INTEGER DEFAULT 0'))
                logger.info(f"Added {table}.records_kept")
            conn.commit()
    except Exception:
        logger.exception("Migration v2.8.9 failed")
        raise


def run_v2_8_13_cleanup_decision_migrations(db):
    """Persist cleanup decisions without retaining an unbounded path list."""
    statements = (
        """CREATE TABLE IF NOT EXISTS cleanup_file_decisions (
            id SERIAL PRIMARY KEY,
            cleanup_run_id VARCHAR(36) NOT NULL,
            scan_result_id INTEGER,
            file_path TEXT NOT NULL,
            decision VARCHAR(20) NOT NULL DEFAULT 'pending',
            reason VARCHAR(64),
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            decided_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT uq_cleanup_file_decision_run_result
                UNIQUE (cleanup_run_id, scan_result_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_cleanup_file_decision_run_state ON cleanup_file_decisions (cleanup_run_id, decision, id)",
        "CREATE INDEX IF NOT EXISTS ix_cleanup_file_decisions_cleanup_run_id ON cleanup_file_decisions (cleanup_run_id)",
        "CREATE INDEX IF NOT EXISTS ix_cleanup_file_decisions_scan_result_id ON cleanup_file_decisions (scan_result_id)",
        "ALTER TABLE scan_reports ADD COLUMN IF NOT EXISTS cleanup_run_id VARCHAR(36)",
        "ALTER TABLE scan_reports ADD COLUMN IF NOT EXISTS cleanup_details_total INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE scan_reports ADD COLUMN IF NOT EXISTS cleanup_details_truncated BOOLEAN NOT NULL DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS ix_scan_reports_cleanup_run_id ON scan_reports (cleanup_run_id)",
    )
    try:
        with migration_connection(db) as conn:
            for statement in statements:
                conn.execute(text(statement))
            conn.commit()
    except Exception:
        logger.exception("Migration v2.8.13 cleanup decision schema failed")
        raise


def run_v2_8_14_mount_policy_migrations(db):
    """Persist administrator-approved mount baselines for scan roots."""
    statements = (
        "ALTER TABLE scan_configurations ADD COLUMN IF NOT EXISTS require_mount BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE scan_configurations ADD COLUMN IF NOT EXISTS mount_filesystem_type VARCHAR(100)",
        "ALTER TABLE scan_configurations ADD COLUMN IF NOT EXISTS mount_source TEXT",
        "ALTER TABLE scan_configurations ADD COLUMN IF NOT EXISTS mount_root TEXT",
    )
    with migration_connection(db) as conn:
        for statement in statements:
            conn.execute(text(statement))
        conn.commit()


def verify_schema_ready(db, connection):
    """Verify every model table and column through the migration connection."""
    inspector = inspect(connection)
    table_names = set(inspector.get_table_names())
    missing = []
    for table_name, table in db.metadata.tables.items():
        if table_name not in table_names:
            missing.append(table_name)
            continue
        actual = {column['name'] for column in inspector.get_columns(table_name)}
        missing.extend(f'{table_name}.{column.name}' for column in table.columns
                       if column.name not in actual)
    if missing:
        raise RuntimeError(f'Schema is not ready after migrations: {", ".join(missing[:10])}')
    users = {column['name']: column for column in inspector.get_columns('users')}
    admin_default = str(users['is_admin'].get('default') or '').lower()
    if 'false' not in admin_default and '0' not in admin_default:
        raise RuntimeError('Schema is not ready: users.is_admin server default is not false')


def _run_all_migrations(db, connection):
    """Execute all database migrations. Called by migrate_database() after acquiring lock."""
    from tools.app_startup_migration import run_startup_migrations

    logger.info("Running startup migrations...")
    run_startup_migrations(db, connection=connection)
    logger.info("Startup migrations completed successfully")

    logger.info("Recording cleanup records kept...")
    run_v2_8_9_migrations(db)

    logger.info("Scoping mark-as-good overrides...")
    run_v2_8_8_migrations(db)

    logger.info("Adopting scanner settings from the environment...")
    run_v2_8_7_migrations(db)

    logger.info("Checking authentication tables...")
    run_auth_migration(db)
    logger.info("Authentication tables verified")

    # This runs while migrate_database holds the PostgreSQL advisory lock.
    # Do not defer it to request-time authentication: token storage changes
    # must be complete before any worker can accept credentials.
    from pixelprobe.migrations.security import migrate_security_schema
    migrate_security_schema(db, connection=connection)
    from pixelprobe.migrations.security_audit import migrate_security_audit_schema
    migrate_security_audit_schema(db, connection=connection)

    logger.info("Running v2.4.35 migration...")
    run_v2_4_35_migrations(db)
    logger.info("v2.4.35 migration completed successfully")

    logger.info("Running v2.4.113 migration...")
    run_v2_4_113_migrations(db)
    logger.info("v2.4.113 migration completed successfully")

    logger.info("Running v2.6.0 migration...")
    run_v2_6_0_migrations(db)
    logger.info("v2.6.0 migration completed successfully")

    logger.info("Running v2.6.33 migration (schema sync)...")
    run_v2_6_33_migrations(db)

    logger.info("Running v2.6.49 migration (chunk engine schema)...")
    run_v2_6_49_migrations(db)

    logger.info("Running v2.6.53 migration (log-exclusion backfill)...")
    run_v2_6_53_migrations(db)

    logger.info("Running v2.6.60 migration (schedule time budget)...")
    run_v2_6_60_migrations(db)

    logger.info("Running v2.6.61 migration (bitrot classification)...")
    run_v2_6_61_migrations(db)

    logger.info("Running v2.8.12 lifecycle migration...")
    run_v2_8_12_lifecycle_migrations(db)

    logger.info("Running v2.8.13 cleanup decision migration...")
    run_v2_8_13_cleanup_decision_migrations(db)

    logger.info("Running v2.8.14 mount policy migration...")
    run_v2_8_14_mount_policy_migrations(db)

    logger.info("Creating performance indexes...")
    create_performance_indexes(db)
    logger.info("Performance indexes created successfully")

    verify_schema_ready(db, connection)
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
                with migration_owner_connection(lock_conn):
                    # New installations must be created by the lock owner too.
                    # create_all alone does not evolve old schemas, so it is
                    # followed by the complete migration sequence below.
                    db.metadata.create_all(bind=lock_conn)
                    lock_conn.commit()
                    _run_all_migrations(db, lock_conn)
                    verify_schema_ready(db, lock_conn)
            except Exception as mig_err:
                logger.error(f"Migration error (lock held): {mig_err}")
                raise
            finally:
                # A failed DDL statement leaves PostgreSQL's transaction
                # aborted. Advisory locks are session-scoped, so roll back
                # first and then release the lock on this same connection.
                lock_conn.rollback()
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
            verify_schema_ready(db, lock_conn)
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": MIGRATION_ADVISORY_LOCK_ID}
            )
            logger.info(f"Migrations completed by another process, continuing startup in process {os.getpid()}")

    except Exception as e:
        logger.error(f"Migration lock/readiness failed: {e}")
        raise

    finally:
        if lock_conn is not None:
            try:
                lock_conn.close()
            except Exception:
                pass
