"""Read and refresh persisted duplicate group summaries."""

from datetime import datetime, timedelta, timezone
import time

from sqlalchemy import and_, func, or_, text

from pixelprobe.models import (db, DuplicateDirtyKey, DuplicateGroupSummary,
                               DuplicateIndexState, ScanResult)

_DUPLICATE_REFRESH_LOCK_ID = 7283945191
_DIRTY_KEY_BATCH_SIZE = 1000
_REFRESH_TIME_BUDGET_SECS = 20


def _basename_expression():
    return (func.split_part(ScanResult.file_path, '/', -1)
            if db.engine.dialect.name == 'postgresql'
            else func.substr(ScanResult.file_path,
                             func.length(func.rtrim(ScanResult.file_path,
                                                    func.replace(ScanResult.file_path, '/', ''))) + 1))


def _eligible(mode):
    eligible = [ScanResult.file_exists.is_(True)]
    if mode == 'hash':
        eligible.extend([
            ScanResult.file_hash.isnot(None),
            func.trim(ScanResult.file_hash) != '',
            ScanResult.file_size.isnot(None),
            ScanResult.bitrot_suspected.is_(False),
            or_(ScanResult.scan_status.is_(None),
                ScanResult.scan_status.notin_(['error', 'failed', 'unreadable'])),
            or_(ScanResult.scan_tool.is_(None), ScanResult.scan_tool != 'error'),
            or_(ScanResult.last_integrity_outcome.is_(None),
                ScanResult.last_integrity_outcome.notin_(['error', 'unreadable'])),
        ])
    else:
        eligible.append(_basename_expression() != '')
    return eligible


def _duplicate_groups(mode):
    """Reference grouping query for tests and cache reconciliation checks."""
    name = _basename_expression()
    keys = [ScanResult.file_hash, ScanResult.file_size] if mode == 'hash' else [name]
    groups = db.session.query(
        *(key.label(f'key_{index}') for index, key in enumerate(keys)),
        func.count().label('group_size'),
        func.min(ScanResult.id).label('group_id'),
    ).filter(*_eligible(mode)).group_by(*keys).having(func.count() > 1).subquery()
    return groups, keys


def duplicate_index_status():
    state = db.session.get(DuplicateIndexState, 1)
    if state is None:
        return {'ready': False, 'updated_at': None, 'stale': False}

    updated_at = state.updated_at
    if updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        updated_at = updated_at.astimezone(timezone.utc).isoformat()
    stale_pending = db.session.query(DuplicateDirtyKey.mode).filter(
        DuplicateDirtyKey.enqueued_at <= datetime.now(timezone.utc) - timedelta(seconds=60)
    ).order_by(DuplicateDirtyKey.enqueued_at, DuplicateDirtyKey.mode,
               DuplicateDirtyKey.key_text, DuplicateDirtyKey.key_size).first() is not None
    return {
        'ready': bool(state.ready),
        'updated_at': updated_at,
        'stale': bool(state.refresh_failed or stale_pending),
    }


def duplicate_statistics(status=None):
    status = status or duplicate_index_status()
    prefixes = {'hash': 'duplicate', 'name': 'filename_duplicate'}
    if not status['ready']:
        return {
            f'{prefix}_{suffix}': None
            for prefix in prefixes.values()
            for suffix in ('files', 'groups', 'extra_files')
        }

    groups = db.session.query(
        DuplicateGroupSummary.mode,
        func.sum(DuplicateGroupSummary.group_size),
        func.count(),
    ).group_by(DuplicateGroupSummary.mode).all()
    totals = {mode: (int(files or 0), int(count or 0))
              for mode, files, count in groups}
    result = {}
    for mode, prefix in prefixes.items():
        files, count = totals.get(mode, (0, 0))
        result.update({
            f'{prefix}_files': files,
            f'{prefix}_groups': count,
            f'{prefix}_extra_files': files - count,
        })
    return result


def duplicate_members(mode):
    if mode not in ('hash', 'name'):
        raise ValueError('Duplicate mode must be hash or name')
    summary = DuplicateGroupSummary
    if mode == 'hash':
        key_match = and_(summary.key_text == ScanResult.file_hash,
                         summary.key_size == ScanResult.file_size)
    else:
        key_match = and_(summary.key_text == _basename_expression(), summary.key_size == -1)
    return db.session.query(
        ScanResult.id.label('file_id'), summary.group_size, summary.group_id,
    ).join(summary, and_(summary.mode == mode, key_match)).filter(
        *_eligible(mode)
    ).subquery()


def _ensure_duplicate_indexes(can_dispatch=None):
    if db.engine.dialect.name != 'postgresql':
        return
    indexes = (
        ('idx_scan_results_duplicate_hash_key',
         "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scan_results_duplicate_hash_key "
         "ON scan_results (file_hash, file_size) WHERE file_exists IS TRUE "
         "AND file_hash IS NOT NULL AND btrim(file_hash) <> '' AND file_size IS NOT NULL "
         "AND bitrot_suspected IS FALSE "
         "AND (scan_status IS NULL OR scan_status NOT IN ('error', 'failed', 'unreadable')) "
         "AND (scan_tool IS NULL OR scan_tool <> 'error') "
         "AND (last_integrity_outcome IS NULL "
         "OR last_integrity_outcome NOT IN ('error', 'unreadable'))"),
        ('idx_scan_results_duplicate_name_key',
         "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_scan_results_duplicate_name_key "
         "ON scan_results ((split_part(file_path, '/', -1))) "
         "WHERE file_exists IS TRUE AND split_part(file_path, '/', -1) <> ''"),
    )
    with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        conn.execute(text('SET statement_timeout = 300000'))
        try:
            for name, create_sql in indexes:
                if can_dispatch is not None and not can_dispatch():
                    return False
                valid = conn.execute(text("""
                    SELECT i.indisvalid
                    FROM pg_index i
                    JOIN pg_class idx ON idx.oid = i.indexrelid
                    JOIN pg_namespace ns ON ns.oid = idx.relnamespace
                    WHERE idx.relname = :name AND ns.nspname = current_schema()
                """), {'name': name}).scalar_one_or_none()
                if valid is True:
                    continue
                if valid is False:
                    conn.execute(text(f'DROP INDEX CONCURRENTLY IF EXISTS {name}'))
                conn.execute(text(create_sql))
            return True
        finally:
            try:
                conn.execute(text('RESET statement_timeout'))
            except Exception:
                conn.invalidate()


def _initial_grouping(conn):
    conn.execute(text('DELETE FROM duplicate_group_summaries'))
    conn.execute(text(f"""
        INSERT INTO duplicate_group_summaries
            (mode, key_text, key_size, group_size, group_id, updated_at)
        SELECT 'hash', file_hash, file_size, count(*), min(id), clock_timestamp()
        FROM scan_results
        WHERE file_exists IS TRUE AND file_hash IS NOT NULL AND btrim(file_hash) <> ''
          AND file_size IS NOT NULL AND bitrot_suspected IS FALSE
          AND (scan_status IS NULL OR scan_status NOT IN ('error', 'failed', 'unreadable'))
          AND (scan_tool IS NULL OR scan_tool <> 'error')
          AND (last_integrity_outcome IS NULL
               OR last_integrity_outcome NOT IN ('error', 'unreadable'))
        GROUP BY file_hash, file_size
        HAVING count(*) > 1
    """))
    conn.execute(text("""
        INSERT INTO duplicate_group_summaries
            (mode, key_text, key_size, group_size, group_id, updated_at)
        SELECT 'name', split_part(file_path, '/', -1), -1, count(*), min(id), clock_timestamp()
        FROM scan_results
        WHERE file_exists IS TRUE AND split_part(file_path, '/', -1) <> ''
        GROUP BY split_part(file_path, '/', -1)
        HAVING count(*) > 1
    """))


def _values_clause(keys):
    values = []
    params = {}
    for index, (mode, key_text, key_size) in enumerate(keys):
        values.append(f"(CAST(:mode_{index} AS varchar(8)), "
                      f"CAST(:text_{index} AS varchar(500)), "
                      f"CAST(:size_{index} AS bigint))")
        params[f'mode_{index}'] = mode
        params[f'text_{index}'] = key_text
        params[f'size_{index}'] = key_size
    return ', '.join(values), params


def _bounded_execute(conn, deadline, sql, params=None):
    remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
    conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                 {'timeout': f'{remaining_ms}ms'})
    return conn.execute(text(sql), params or {})


def _refresh_claimed_keys(conn, keys, deadline):
    all_values_sql, all_params = _values_clause(keys)
    _bounded_execute(conn, deadline, f"""
        DELETE FROM duplicate_group_summaries AS summary
        USING (VALUES {all_values_sql}) AS wanted(mode, key_text, key_size)
        WHERE summary.mode = wanted.mode AND summary.key_text = wanted.key_text
          AND summary.key_size = wanted.key_size
    """, all_params)

    for mode in ('hash', 'name'):
        mode_keys = [key for key in keys if key[0] == mode]
        if not mode_keys:
            continue
        values_sql, params = _values_clause(mode_keys)
        if mode == 'hash':
            match = """scan.file_hash = wanted.key_text AND scan.file_size = wanted.key_size
                AND scan.file_exists IS TRUE AND scan.file_hash IS NOT NULL
                AND btrim(scan.file_hash) <> '' AND scan.file_size IS NOT NULL
                AND scan.bitrot_suspected IS FALSE
                AND (scan.scan_status IS NULL OR scan.scan_status NOT IN ('error', 'failed', 'unreadable'))
                AND (scan.scan_tool IS NULL OR scan.scan_tool <> 'error')
                AND (scan.last_integrity_outcome IS NULL
                     OR scan.last_integrity_outcome NOT IN ('error', 'unreadable'))"""
        else:
            match = """split_part(scan.file_path, '/', -1) = wanted.key_text
                AND scan.file_exists IS TRUE
                AND split_part(scan.file_path, '/', -1) <> ''"""
        _bounded_execute(conn, deadline, f"""
            INSERT INTO duplicate_group_summaries
                (mode, key_text, key_size, group_size, group_id, updated_at)
            WITH wanted(mode, key_text, key_size) AS (VALUES {values_sql}), grouped AS (
                SELECT wanted.key_text, wanted.key_size, count(*) AS group_size,
                       min(scan.id) AS group_id
                FROM wanted
                JOIN scan_results AS scan ON wanted.mode = '{mode}' AND {match}
                GROUP BY wanted.key_text, wanted.key_size
                HAVING count(*) > 1
            )
            SELECT '{mode}', key_text, key_size, group_size, group_id, clock_timestamp()
            FROM grouped
            ON CONFLICT (mode, key_text, key_size) DO UPDATE SET
                group_size = EXCLUDED.group_size,
                group_id = EXCLUDED.group_id,
                updated_at = EXCLUDED.updated_at
        """, params)

    _bounded_execute(conn, deadline, f"""
        DELETE FROM duplicate_dirty_keys AS dirty
        USING (VALUES {all_values_sql}) AS wanted(mode, key_text, key_size)
        WHERE dirty.mode = wanted.mode AND dirty.key_text = wanted.key_text
          AND dirty.key_size = wanted.key_size
    """, all_params)


def refresh_duplicate_index(batch_size=_DIRTY_KEY_BATCH_SIZE, can_dispatch=None):
    """Create lookup indexes, backfill once, then reconcile queued keys."""
    if db.engine.dialect.name != 'postgresql':
        return False
    if can_dispatch is not None and not can_dispatch():
        return False
    lock_conn = db.engine.connect()
    lock_acquired = False
    lock_attempted = False
    lock_result_known = False
    try:
        lock_attempted = True
        lock_acquired = bool(lock_conn.execute(text(
            'SELECT pg_try_advisory_lock(:lock_id)'),
            {'lock_id': _DUPLICATE_REFRESH_LOCK_ID},
        ).scalar())
        lock_conn.commit()
        lock_result_known = True
        if not lock_acquired:
            return False

        if not _ensure_duplicate_indexes(can_dispatch):
            return False
        if can_dispatch is not None and not can_dispatch():
            return False
        with db.engine.begin() as conn:
            conn.execute(text('SET LOCAL lock_timeout = 5000'))

            state = conn.execute(text(
                'SELECT ready FROM duplicate_index_state WHERE id = 1 FOR UPDATE'
            )).scalar_one_or_none()
            if state is None:
                conn.execute(text("""
                    INSERT INTO duplicate_index_state (id, ready, refresh_failed)
                    VALUES (1, FALSE, FALSE) ON CONFLICT (id) DO NOTHING
                """))
                state = False

            if not state:
                conn.execute(text('SET LOCAL statement_timeout = 300000'))
                _initial_grouping(conn)
                conn.execute(text("""
                    UPDATE duplicate_index_state
                    SET ready = TRUE, updated_at = clock_timestamp(), refresh_failed = FALSE
                    WHERE id = 1
                """))
                return True

        deadline = time.monotonic() + _REFRESH_TIME_BUDGET_SECS
        while time.monotonic() < deadline:
            if can_dispatch is not None and not can_dispatch():
                return False
            with db.engine.begin() as conn:
                conn.execute(text('SET LOCAL lock_timeout = 5000'))
                batch_deadline = min(deadline, time.monotonic() + 8)
                rows = _bounded_execute(conn, batch_deadline, """
                    WITH oldest AS (
                        SELECT mode, key_text, key_size
                        FROM duplicate_dirty_keys
                        ORDER BY enqueued_at, mode, key_text, key_size
                        LIMIT :batch_size
                    )
                    SELECT dirty.mode, dirty.key_text, dirty.key_size
                    FROM duplicate_dirty_keys AS dirty
                    JOIN oldest USING (mode, key_text, key_size)
                    ORDER BY dirty.mode, dirty.key_text, dirty.key_size
                    FOR UPDATE OF dirty SKIP LOCKED
                """, {'batch_size': max(1, int(batch_size))}).all()
                keys = [(row.mode, row.key_text, row.key_size) for row in rows]
                if not keys:
                    _bounded_execute(conn, batch_deadline, """
                        UPDATE duplicate_index_state
                        SET updated_at = clock_timestamp(), refresh_failed = FALSE
                        WHERE id = 1
                    """)
                    break
                _refresh_claimed_keys(conn, keys, batch_deadline)
                _bounded_execute(conn, batch_deadline, """
                    UPDATE duplicate_index_state
                    SET updated_at = clock_timestamp(), refresh_failed = FALSE
                    WHERE id = 1
                """)
        return True
    except Exception:
        try:
            with db.engine.begin() as conn:
                conn.execute(text("""
                    UPDATE duplicate_index_state
                    SET refresh_failed = TRUE
                    WHERE id = 1
                """))
        except Exception:
            pass
        raise
    finally:
        if lock_acquired:
            try:
                unlocked = lock_conn.execute(text(
                    'SELECT pg_advisory_unlock(:lock_id)'),
                    {'lock_id': _DUPLICATE_REFRESH_LOCK_ID},
                ).scalar()
                lock_conn.commit()
                if not unlocked:
                    lock_conn.invalidate()
            except Exception:
                lock_conn.invalidate()
        elif lock_attempted and not lock_result_known:
            lock_conn.invalidate()
        lock_conn.close()
