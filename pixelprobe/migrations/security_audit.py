"""Schema migration for the independent security audit trail."""

from sqlalchemy import inspect, text


def migrate_security_audit_schema(db, connection=None):
    """Create the append-only audit table under the startup migration lock."""
    if 'users' not in inspect(connection or db.engine).get_table_names():
        return
    if connection is not None:
        conn = connection
        close_transaction = False
    else:
        conn = db.engine.connect()
        close_transaction = True
    try:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS security_audit_events (
                id SERIAL PRIMARY KEY,
                actor_id INTEGER,
                action VARCHAR(100) NOT NULL,
                target VARCHAR(300),
                outcome VARCHAR(30) NOT NULL,
                details JSON NOT NULL DEFAULT '{}'::json,
                ip_address VARCHAR(64),
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_security_audit_events_created_at '
            'ON security_audit_events(created_at)'
        ))
        conn.execute(text(
            'CREATE INDEX IF NOT EXISTS idx_security_audit_events_actor_id '
            'ON security_audit_events(actor_id)'
        ))
        conn.execute(text("""
            DO $$
            DECLARE constraint_name TEXT;
            BEGIN
                SELECT conname INTO constraint_name
                FROM pg_constraint
                WHERE conrelid = 'security_audit_events'::regclass
                  AND contype = 'f'
                  AND conkey = ARRAY[
                    (SELECT attnum FROM pg_attribute
                     WHERE attrelid = 'security_audit_events'::regclass
                       AND attname = 'actor_id')
                  ];
                IF constraint_name IS NOT NULL THEN
                    EXECUTE format('ALTER TABLE security_audit_events DROP CONSTRAINT %I',
                                   constraint_name);
                END IF;
            END $$;
        """))
        if close_transaction:
            conn.commit()
    finally:
        if close_transaction:
            conn.close()
