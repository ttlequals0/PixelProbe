"""
Tests for PostgreSQL advisory lock migration coordination in app.py
"""

import os
import pytest
from unittest.mock import patch, MagicMock, call


def test_advisory_lock_id_is_stable():
    """MIGRATION_ADVISORY_LOCK_ID must never change -- other running containers
    depend on the same value to coordinate."""
    # Set SECRET_KEY so config.py doesn't raise on import
    os.environ.setdefault('SECRET_KEY', 'test-secret-key')
    from app import MIGRATION_ADVISORY_LOCK_ID
    assert MIGRATION_ADVISORY_LOCK_ID == 7283945162


def test_migrate_database_falls_back_on_advisory_lock_failure(app):
    """When advisory lock acquisition fails (e.g., SQLite test DB), migrations
    still run via the fallback path."""
    with app.app_context():
        with patch('app._run_all_migrations') as mock_migrations:
            from app import migrate_database
            # SQLite does not support pg_try_advisory_lock, so this should
            # hit the except branch and fall back to uncoordinated execution
            migrate_database()
            mock_migrations.assert_called_once()


def test_migrate_database_releases_lock_on_success(app):
    """Advisory lock is released after migrations complete successfully."""
    with app.app_context():
        mock_conn = MagicMock()
        # pg_try_advisory_lock returns True (we are the leader)
        mock_scalar = MagicMock(return_value=True)
        mock_result = MagicMock()
        mock_result.scalar = mock_scalar
        mock_conn.execute.return_value = mock_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        with patch('app._run_all_migrations') as mock_migrations, \
             patch('app.db') as mock_db:
            mock_db.engine = mock_engine

            from app import migrate_database
            migrate_database()

            mock_migrations.assert_called_once()
            # 2 execute calls: pg_try_advisory_lock, pg_advisory_unlock
            assert mock_conn.execute.call_count == 2
            # Verify the SQL text of the unlock call (second execute)
            unlock_text_arg = mock_conn.execute.call_args_list[1][0][0]
            assert 'pg_advisory_unlock' in unlock_text_arg.text
            mock_conn.close.assert_called_once()


def test_migrate_database_releases_lock_on_migration_failure(app):
    """Advisory lock is released even if migrations raise an exception."""
    with app.app_context():
        mock_conn = MagicMock()
        mock_scalar = MagicMock(return_value=True)
        mock_result = MagicMock()
        mock_result.scalar = mock_scalar
        mock_conn.execute.return_value = mock_result

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        with patch('app._run_all_migrations', side_effect=RuntimeError("migration boom")) as mock_migrations, \
             patch('app.db') as mock_db:
            mock_db.engine = mock_engine

            from app import migrate_database
            # Should not propagate -- exception is caught within the leader block
            migrate_database()

            mock_migrations.assert_called_once()
            # Verify unlock was still called despite the exception
            assert mock_conn.execute.call_count == 2
            unlock_text_arg = mock_conn.execute.call_args_list[1][0][0]
            assert 'pg_advisory_unlock' in unlock_text_arg.text
            mock_conn.close.assert_called_once()


def test_migrate_database_waiter_path(app):
    """When another process holds the lock, we wait then skip migrations."""
    with app.app_context():
        mock_conn = MagicMock()

        call_count = [0]
        def mock_execute(stmt, params=None):
            call_count[0] += 1
            result = MagicMock()
            if call_count[0] == 1:
                # pg_try_advisory_lock returns False (someone else has it)
                result.scalar.return_value = False
            return result

        mock_conn.execute = mock_execute

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        with patch('app._run_all_migrations') as mock_migrations, \
             patch('app.db') as mock_db:
            mock_db.engine = mock_engine

            from app import migrate_database
            migrate_database()

            # Migrations should NOT have been called (we are the waiter)
            mock_migrations.assert_not_called()
            # 3 calls: pg_try_advisory_lock (False), pg_advisory_lock (blocking), pg_advisory_unlock
            assert call_count[0] == 3
