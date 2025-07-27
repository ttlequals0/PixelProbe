#!/usr/bin/env python3
"""
Check database integrity and attempt recovery
"""

import sqlite3
import os

DB_PATH = os.environ.get('DATABASE_PATH', './instance/media_checker.db')

def check_integrity():
    """Check database integrity"""
    
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at: {DB_PATH}")
        return
    
    print(f"Checking database: {DB_PATH}")
    print(f"Size: {os.path.getsize(DB_PATH) / 1024 / 1024:.2f} MB")
    print()
    
    try:
        # Try to connect with different settings
        conn = sqlite3.connect(DB_PATH)
        conn.isolation_level = None  # Autocommit mode
        cursor = conn.cursor()
        
        # Run integrity check
        print("Running PRAGMA integrity_check...")
        cursor.execute("PRAGMA integrity_check")
        results = cursor.fetchall()
        
        if results and results[0][0] == 'ok':
            print("✅ Database integrity check passed!")
        else:
            print("❌ Database integrity issues found:")
            for result in results[:10]:  # Show first 10 issues
                print(f"   - {result[0]}")
            if len(results) > 10:
                print(f"   ... and {len(results) - 10} more issues")
        
        # Try quick check
        print("\nRunning PRAGMA quick_check...")
        cursor.execute("PRAGMA quick_check")
        quick_results = cursor.fetchall()
        if quick_results and quick_results[0][0] == 'ok':
            print("✅ Quick check passed!")
        
        # Try to read some data with error recovery
        print("\nAttempting to read data...")
        try:
            cursor.execute("SELECT COUNT(*) FROM scan_results")
            count = cursor.fetchone()
            if count:
                print(f"✅ Found {count[0]:,} records in scan_results table")
        except Exception as e:
            print(f"❌ Cannot read scan_results: {e}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print("\n💡 Suggestion: The database appears to be corrupted.")
    print("   You might want to:")
    print("   1. Use a backup database if available")
    print("   2. Create a new test database with sample data")
    print("   3. Try to recover data using specialized SQLite recovery tools")

if __name__ == "__main__":
    print("=== Database Integrity Check ===")
    print()
    check_integrity()