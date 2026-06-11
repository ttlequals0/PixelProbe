"""
Tests for PostgreSQL advisory lock migration coordination.

The migration logic now lives in pixelprobe.migrations.startup.
"""

import re
import sys
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def _get_migrations_module():
    """Import the migrations startup module directly (safe, no Celery side effects)."""
    from pixelprobe.migrations import startup
    return startup


def test_advisory_lock_id_is_stable():
    """MIGRATION_ADVISORY_LOCK_ID must never change -- other running containers
    depend on the same value to coordinate."""
    source = Path(__file__).parent.parent / 'pixelprobe' / 'migrations' / 'startup.py'
    content = source.read_text()
    match = re.search(r'MIGRATION_ADVISORY_LOCK_ID\s*=\s*(\d+)', content)
    assert match is not None, "MIGRATION_ADVISORY_LOCK_ID not found in pixelprobe/migrations/startup.py"
    assert int(match.group(1)) == 7283945162


def test_migrate_database_falls_back_on_advisory_lock_failure(app):
    """When advisory lock acquisition fails (e.g., SQLite test DB), migrations
    still run via the fallback path."""
    mig_mod = _get_migrations_module()
    with app.app_context():
        with patch.object(mig_mod, '_run_all_migrations') as mock_migrations:
            from pixelprobe.models import db
            mig_mod.migrate_database(db)
            mock_migrations.assert_called_once()


def test_migrate_database_releases_lock_on_success(app):
    """Advisory lock is released after migrations complete successfully."""
    mig_mod = _get_migrations_module()
    with app.app_context():
        mock_conn = MagicMock()
        mock_scalar = MagicMock(return_value=True)
        mock_result = MagicMock()
        mock_result.scalar = mock_scalar
        mock_conn.execute.return_value = mock_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        mock_db = MagicMock()
        mock_db.engine = mock_engine

        with patch.object(mig_mod, '_run_all_migrations') as mock_migrations:
            mig_mod.migrate_database(mock_db)

            mock_migrations.assert_called_once()
            assert mock_conn.execute.call_count == 2
            unlock_text_arg = mock_conn.execute.call_args_list[1][0][0]
            assert 'pg_advisory_unlock' in unlock_text_arg.text
            mock_conn.close.assert_called_once()


def test_migrate_database_releases_lock_on_migration_failure(app):
    """Advisory lock is released even if migrations raise an exception."""
    mig_mod = _get_migrations_module()
    with app.app_context():
        mock_conn = MagicMock()
        mock_scalar = MagicMock(return_value=True)
        mock_result = MagicMock()
        mock_result.scalar = mock_scalar
        mock_conn.execute.return_value = mock_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        mock_db = MagicMock()
        mock_db.engine = mock_engine

        with patch.object(mig_mod, '_run_all_migrations', side_effect=RuntimeError("migration boom")) as mock_migrations:
            mig_mod.migrate_database(mock_db)

            mock_migrations.assert_called_once()
            assert mock_conn.execute.call_count == 2
            unlock_text_arg = mock_conn.execute.call_args_list[1][0][0]
            assert 'pg_advisory_unlock' in unlock_text_arg.text
            mock_conn.close.assert_called_once()


def test_migrate_database_waiter_path(app):
    """When another process holds the lock, we wait then skip migrations."""
    mig_mod = _get_migrations_module()
    with app.app_context():
        mock_conn = MagicMock()

        call_count = [0]
        def mock_execute(stmt, params=None):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                result.scalar.return_value = False
            return result

        mock_conn.execute = mock_execute

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        mock_db = MagicMock()
        mock_db.engine = mock_engine

        with patch.object(mig_mod, '_run_all_migrations') as mock_migrations:
            mig_mod.migrate_database(mock_db)

            mock_migrations.assert_not_called()
            # try_lock + SET LOCAL statement_timeout (bounds the wait) + lock + unlock
            assert call_count[0] == 4
