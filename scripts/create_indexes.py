#!/usr/bin/env python3
"""
Database migration script to add performance indexes.
Run this once to improve query performance.
"""

import sqlite3
import os
import sys

def create_indexes():
    """Create indexes for better query performance"""
    db_path = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
    
    # Handle SQLite URL format
    if db_path.startswith('sqlite:///'):
        db_path = db_path.replace('sqlite:///', '')
    
    if not os.path.exists(db_path):
        print(f"Database file not found: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # List of indexes to create
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_scan_status ON scan_results(scan_status)",
            "CREATE INDEX IF NOT EXISTS idx_scan_date ON scan_results(scan_date)",
            "CREATE INDEX IF NOT EXISTS idx_is_corrupted ON scan_results(is_corrupted)",
            "CREATE INDEX IF NOT EXISTS idx_marked_as_good ON scan_results(marked_as_good)",
            "CREATE INDEX IF NOT EXISTS idx_discovered_date ON scan_results(discovered_date)",
            "CREATE INDEX IF NOT EXISTS idx_file_hash ON scan_results(file_hash)",
            "CREATE INDEX IF NOT EXISTS idx_last_modified ON scan_results(last_modified)",
            "CREATE INDEX IF NOT EXISTS idx_file_path ON scan_results(file_path)",
            # Composite indexes for common queries
            "CREATE INDEX IF NOT EXISTS idx_status_date ON scan_results(scan_status, scan_date)",
            "CREATE INDEX IF NOT EXISTS idx_corrupted_good ON scan_results(is_corrupted, marked_as_good)"
        ]
        
        print("Creating performance indexes...")
        for index_sql in indexes:
            print(f"Creating index: {index_sql.split('idx_')[1].split(' ')[0]}")
            cursor.execute(index_sql)
        
        conn.commit()
        print("All indexes created successfully!")
        
        # Show index info
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='scan_results'")
        indexes = cursor.fetchall()
        print(f"\nTotal indexes on scan_results table: {len(indexes)}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"Error creating indexes: {str(e)}")
        return False

if __name__ == "__main__":
    success = create_indexes()
    sys.exit(0 if success else 1)