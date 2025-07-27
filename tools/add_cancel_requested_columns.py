#!/usr/bin/env python3
"""
Add cancel_requested columns to CleanupState and FileChangesState tables.
This migration adds the missing fields that were causing crashes in v2.0.88.
"""

import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

def add_cancel_requested_columns():
    """Add cancel_requested columns to existing tables"""
    
    # Get database URL from environment or use default
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
    
    # For SQLite, we need to adjust the path if it's a relative path
    if database_url.startswith('sqlite:///') and not database_url.startswith('sqlite:////'):
        db_path = database_url.replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            # Make it relative to the instance directory
            instance_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'instance')
            database_url = f'sqlite:///{os.path.join(instance_dir, db_path)}'
    
    print(f"Connecting to database: {database_url}")
    
    engine = create_engine(database_url)
    
    # SQL commands to add the columns
    commands = [
        "ALTER TABLE cleanup_state ADD COLUMN cancel_requested BOOLEAN DEFAULT 0",
        "ALTER TABLE file_changes_state ADD COLUMN cancel_requested BOOLEAN DEFAULT 0"
    ]
    
    with engine.connect() as conn:
        for cmd in commands:
            try:
                conn.execute(text(cmd))
                conn.commit()
                print(f"Successfully executed: {cmd}")
            except OperationalError as e:
                if "duplicate column name" in str(e).lower() or "already exists" in str(e).lower():
                    print(f"Column already exists, skipping: {cmd}")
                else:
                    print(f"Error executing {cmd}: {e}")
                    raise
    
    print("\nMigration completed successfully!")
    print("The cancel_requested columns have been added to both tables.")

if __name__ == "__main__":
    add_cancel_requested_columns()