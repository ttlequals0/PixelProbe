"""Database objects that keep duplicate group keys queued for refresh."""

import logging

from sqlalchemy import text

from pixelprobe.migrations.startup import migration_connection

logger = logging.getLogger(__name__)

_HASH_ELIGIBLE = """file_exists IS TRUE
    AND file_hash IS NOT NULL AND btrim(file_hash) <> ''
    AND file_size IS NOT NULL AND bitrot_suspected IS FALSE
    AND (scan_status IS NULL OR scan_status NOT IN ('error', 'failed', 'unreadable'))
    AND (scan_tool IS NULL OR scan_tool <> 'error')
    AND (last_integrity_outcome IS NULL
         OR last_integrity_outcome NOT IN ('error', 'unreadable'))"""


def _hash_eligible(alias):
    return f"""{alias}.file_exists IS TRUE
        AND {alias}.file_hash IS NOT NULL AND btrim({alias}.file_hash) <> ''
        AND {alias}.file_size IS NOT NULL AND {alias}.bitrot_suspected IS FALSE
        AND ({alias}.scan_status IS NULL
             OR {alias}.scan_status NOT IN ('error', 'failed', 'unreadable'))
        AND ({alias}.scan_tool IS NULL OR {alias}.scan_tool <> 'error')
        AND ({alias}.last_integrity_outcome IS NULL
             OR {alias}.last_integrity_outcome NOT IN ('error', 'unreadable'))"""


def _enqueue_function(name, rows_sql):
    return f"""CREATE OR REPLACE FUNCTION {name}() RETURNS trigger AS $$
BEGIN
    WITH changed_rows AS (
        {rows_sql}
    ), dirty AS (
        SELECT DISTINCT 'hash'::varchar(8) AS mode, file_hash AS key_text,
               file_size AS key_size
        FROM changed_rows
        WHERE hash_changed IS TRUE AND {_HASH_ELIGIBLE}
        UNION
        SELECT DISTINCT 'name'::varchar(8), split_part(file_path, '/', -1), -1::bigint
        FROM changed_rows
        WHERE name_changed IS TRUE AND file_exists IS TRUE
          AND split_part(file_path, '/', -1) <> ''
    )
    INSERT INTO duplicate_dirty_keys (mode, key_text, key_size, enqueued_at)
    SELECT mode, key_text, key_size, clock_timestamp()
    FROM dirty
    ORDER BY mode, key_text, key_size
    ON CONFLICT (mode, key_text, key_size) DO UPDATE
        SET enqueued_at = LEAST(duplicate_dirty_keys.enqueued_at, EXCLUDED.enqueued_at);
    RETURN NULL;
END;
$$ LANGUAGE plpgsql"""


_ROW_COLUMNS_OLD = """o.id, o.file_path, o.file_hash, o.file_size, o.file_exists,
    o.bitrot_suspected, o.scan_status, o.scan_tool, o.last_integrity_outcome"""
_ROW_COLUMNS_NEW = """n.id, n.file_path, n.file_hash, n.file_size, n.file_exists,
    n.bitrot_suspected, n.scan_status, n.scan_tool, n.last_integrity_outcome"""
_ROW_COLUMNS = """id, file_path, file_hash, file_size, file_exists, bitrot_suspected,
    scan_status, scan_tool, last_integrity_outcome"""
_INSERT_ROWS = f"SELECT {_ROW_COLUMNS}, TRUE AS hash_changed, TRUE AS name_changed FROM new_rows"
_DELETE_ROWS = f"SELECT {_ROW_COLUMNS}, TRUE AS hash_changed, TRUE AS name_changed FROM old_rows"
_UPDATE_ROWS = f"""SELECT {_ROW_COLUMNS_OLD}
             , (n.id IS NULL OR o.id IS DISTINCT FROM n.id
                OR o.file_hash IS DISTINCT FROM n.file_hash
                OR o.file_size IS DISTINCT FROM n.file_size
                OR ({_hash_eligible('o')}) IS DISTINCT FROM ({_hash_eligible('n')})) AS hash_changed
             , (n.id IS NULL OR o.id IS DISTINCT FROM n.id
                OR split_part(o.file_path, '/', -1) IS DISTINCT FROM split_part(n.file_path, '/', -1)
                OR (o.file_exists IS TRUE AND split_part(o.file_path, '/', -1) <> '')
                   IS DISTINCT FROM
                   (n.file_exists IS TRUE AND split_part(n.file_path, '/', -1) <> '')) AS name_changed
        FROM old_rows o LEFT JOIN new_rows n ON n.id = o.id
        WHERE n.id IS NULL OR o.file_hash IS DISTINCT FROM n.file_hash
           OR o.file_size IS DISTINCT FROM n.file_size
           OR ({_hash_eligible('o')}) IS DISTINCT FROM ({_hash_eligible('n')})
           OR split_part(o.file_path, '/', -1) IS DISTINCT FROM split_part(n.file_path, '/', -1)
           OR (o.file_exists IS TRUE AND split_part(o.file_path, '/', -1) <> '')
              IS DISTINCT FROM
              (n.file_exists IS TRUE AND split_part(n.file_path, '/', -1) <> '')
        UNION ALL
        SELECT {_ROW_COLUMNS_NEW}
             , (o.id IS NULL OR o.id IS DISTINCT FROM n.id
                OR o.file_hash IS DISTINCT FROM n.file_hash
                OR o.file_size IS DISTINCT FROM n.file_size
                OR ({_hash_eligible('o')}) IS DISTINCT FROM ({_hash_eligible('n')})) AS hash_changed
             , (o.id IS NULL OR o.id IS DISTINCT FROM n.id
                OR split_part(o.file_path, '/', -1) IS DISTINCT FROM split_part(n.file_path, '/', -1)
                OR (o.file_exists IS TRUE AND split_part(o.file_path, '/', -1) <> '')
                   IS DISTINCT FROM
                   (n.file_exists IS TRUE AND split_part(n.file_path, '/', -1) <> '')) AS name_changed
        FROM new_rows n LEFT JOIN old_rows o ON o.id = n.id
        WHERE o.id IS NULL OR o.file_hash IS DISTINCT FROM n.file_hash
           OR o.file_size IS DISTINCT FROM n.file_size
           OR ({_hash_eligible('o')}) IS DISTINCT FROM ({_hash_eligible('n')})
           OR split_part(o.file_path, '/', -1) IS DISTINCT FROM split_part(n.file_path, '/', -1)
           OR (o.file_exists IS TRUE AND split_part(o.file_path, '/', -1) <> '')
              IS DISTINCT FROM
              (n.file_exists IS TRUE AND split_part(n.file_path, '/', -1) <> '')"""


_FUNCTIONS = (
    _enqueue_function('pixelprobe_enqueue_duplicates_insert', _INSERT_ROWS),
    _enqueue_function('pixelprobe_enqueue_duplicates_update', _UPDATE_ROWS),
    _enqueue_function('pixelprobe_enqueue_duplicates_delete', _DELETE_ROWS),
)

_TRIGGERS = (
    ("scan_results_duplicate_insert", """CREATE TRIGGER scan_results_duplicate_insert
        AFTER INSERT ON scan_results REFERENCING NEW TABLE AS new_rows
        FOR EACH STATEMENT EXECUTE FUNCTION pixelprobe_enqueue_duplicates_insert()"""),
    ("scan_results_duplicate_update", """CREATE TRIGGER scan_results_duplicate_update
        AFTER UPDATE ON scan_results
        REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows
        FOR EACH STATEMENT EXECUTE FUNCTION pixelprobe_enqueue_duplicates_update()"""),
    ("scan_results_duplicate_delete", """CREATE TRIGGER scan_results_duplicate_delete
        AFTER DELETE ON scan_results REFERENCING OLD TABLE AS old_rows
        FOR EACH STATEMENT EXECUTE FUNCTION pixelprobe_enqueue_duplicates_delete()"""),
)


def run_duplicate_index_migrations(db):
    """Create the duplicate summary state and statement-level invalidation triggers."""
    statements = (
        """CREATE TABLE IF NOT EXISTS duplicate_group_summaries (
            mode VARCHAR(8) NOT NULL,
            key_text VARCHAR(500) NOT NULL,
            key_size BIGINT NOT NULL DEFAULT -1,
            group_size INTEGER NOT NULL,
            group_id INTEGER NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mode, key_text, key_size),
            CONSTRAINT ck_duplicate_summary_mode CHECK (mode IN ('hash', 'name')),
            CONSTRAINT ck_duplicate_summary_size CHECK (group_size > 1)
        )""",
        """CREATE TABLE IF NOT EXISTS duplicate_dirty_keys (
            mode VARCHAR(8) NOT NULL,
            key_text VARCHAR(500) NOT NULL,
            key_size BIGINT NOT NULL DEFAULT -1,
            enqueued_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (mode, key_text, key_size),
            CONSTRAINT ck_duplicate_dirty_mode CHECK (mode IN ('hash', 'name'))
        )""",
        """CREATE INDEX IF NOT EXISTS idx_duplicate_dirty_enqueued_at
            ON duplicate_dirty_keys (enqueued_at, mode, key_text, key_size)""",
        """CREATE TABLE IF NOT EXISTS duplicate_index_state (
            id INTEGER PRIMARY KEY,
            ready BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP WITH TIME ZONE,
            refresh_failed BOOLEAN NOT NULL DEFAULT FALSE,
            CONSTRAINT ck_duplicate_state_singleton CHECK (id = 1)
        )""",
        """INSERT INTO duplicate_index_state (id, ready, refresh_failed)
            VALUES (1, FALSE, FALSE) ON CONFLICT (id) DO NOTHING""",
        *_FUNCTIONS,
    )
    try:
        with migration_connection(db) as conn:
            for statement in statements:
                conn.execute(text(statement))
            existing_triggers = set(conn.execute(text("""
                SELECT tgname FROM pg_trigger
                WHERE tgrelid = to_regclass('scan_results') AND NOT tgisinternal
            """)).scalars())
            for trigger_name, trigger_sql in _TRIGGERS:
                if trigger_name not in existing_triggers:
                    conn.execute(text(trigger_sql))
            conn.commit()
    except Exception:
        logger.exception('Duplicate index schema migration failed')
        raise
