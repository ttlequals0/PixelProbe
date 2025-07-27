#!/usr/bin/env python3
"""
Fix database schema issues by checking and adding missing columns.
This handles cases where columns exist but SQLAlchemy doesn't recognize them.
"""

import os
import sys
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import OperationalError

def fix_database_schema():
    """Check and fix database schema issues"""
    
    # Get database URL
    database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
    
    # For SQLite, adjust the path
    if database_url.startswith('sqlite:///') and not database_url.startswith('sqlite:////'):
        db_path = database_url.replace('sqlite:///', '')
        if not os.path.isabs(db_path):
            instance_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'instance')
            database_url = f'sqlite:///{os.path.join(instance_dir, db_path)}'
    
    print(f"Connecting to database: {database_url}")
    
    engine = create_engine(database_url)
    inspector = inspect(engine)
    
    # Check what tables exist
    tables = inspector.get_table_names()
    print(f"\nFound tables: {tables}")
    
    # Check cleanup_state columns
    if 'cleanup_state' in tables:
        print("\nChecking cleanup_state table...")
        columns = [col['name'] for col in inspector.get_columns('cleanup_state')]
        print(f"Columns: {columns}")
        
        if 'cancel_requested' not in columns:
            print("Adding cancel_requested column to cleanup_state...")
            try:
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE cleanup_state ADD COLUMN cancel_requested BOOLEAN DEFAULT 0"))
                    conn.commit()
                print("Successfully added cancel_requested to cleanup_state")
            except Exception as e:
                print(f"Error adding column: {e}")
        else:
            print("cancel_requested column already exists in cleanup_state")
    
    # Check file_changes_state columns
    if 'file_changes_state' in tables:
        print("\nChecking file_changes_state table...")
        columns = [col['name'] for col in inspector.get_columns('file_changes_state')]
        print(f"Columns: {columns}")
        
        if 'cancel_requested' not in columns:
            print("Adding cancel_requested column to file_changes_state...")
            try:
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE file_changes_state ADD COLUMN cancel_requested BOOLEAN DEFAULT 0"))
                    conn.commit()
                print("Successfully added cancel_requested to file_changes_state")
            except Exception as e:
                print(f"Error adding column: {e}")
        else:
            print("cancel_requested column already exists in file_changes_state")
    
    # Force SQLAlchemy to refresh its metadata
    print("\nForcing metadata refresh...")
    engine.dispose()
    
    print("\nDatabase schema check complete!")

if __name__ == "__main__":
    fix_database_schema()