"""Schema migration for authentication hardening."""

import hashlib

from sqlalchemy import inspect, text


def migrate_security_schema(db, connection=None):
    """Atomically replace plaintext API tokens with SHA-256 digests.

    The caller must run this under the application's migration lock before any
    request handlers can authenticate tokens.
    """
    inspector = inspect(connection or db.engine)
    tables = set(inspector.get_table_names())
    if not {'users', 'api_tokens'} <= tables:
        return

    if connection is not None:
        conn = connection
        close_transaction = False
    else:
        conn = db.engine.connect()
        close_transaction = True
    try:
        columns = {column['name'] for column in inspect(conn).get_columns('users')}
        if 'session_generation' not in columns:
            conn.execute(text(
                'ALTER TABLE users ADD COLUMN session_generation INTEGER NOT NULL DEFAULT 0'
            ))

        columns = {column['name'] for column in inspect(conn).get_columns('api_tokens')}
        if 'token_digest' not in columns:
            conn.execute(text('ALTER TABLE api_tokens ADD COLUMN token_digest VARCHAR(64)'))

        columns = {column['name'] for column in inspect(conn).get_columns('api_tokens')}
        if 'token' in columns:
            rows = conn.execute(text(
                'SELECT id, token FROM api_tokens WHERE token_digest IS NULL'
            )).mappings()
            for row in rows:
                if not row['token']:
                    raise RuntimeError('API token row has no plaintext value to migrate')
                digest = hashlib.sha256(row['token'].encode('utf-8')).hexdigest()
                conn.execute(text(
                    'UPDATE api_tokens SET token_digest = :digest WHERE id = :id'
                ), {'digest': digest, 'id': row['id']})
            conn.execute(text('ALTER TABLE api_tokens DROP COLUMN token'))

        missing = conn.execute(text(
            'SELECT COUNT(*) FROM api_tokens WHERE token_digest IS NULL'
        )).scalar_one()
        if missing:
            raise RuntimeError('API token digest migration is incomplete')
        conn.execute(text(
            'ALTER TABLE api_tokens ALTER COLUMN token_digest SET NOT NULL'
        ))
        conn.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_api_tokens_token_digest '
            'ON api_tokens(token_digest)'
        ))
        if close_transaction:
            conn.commit()
    finally:
        if close_transaction:
            conn.close()
