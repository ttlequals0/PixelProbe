#!/usr/bin/env python3
"""
Test script to verify database connection and display basic statistics
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = "/Users/dkrachtus/Downloads/media_checker.db"

def test_database():
    """Test database connection and display statistics"""
    
    # Check if database exists
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at: {DB_PATH}")
        return
    
    print(f"✅ Database found at: {DB_PATH}")
    print(f"   Size: {os.path.getsize(DB_PATH) / 1024 / 1024:.2f} MB")
    print()
    
    try:
        # Connect to database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Get table list
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print("📊 Database Tables:")
        for table in tables:
            print(f"   - {table[0]}")
        print()
        
        # Get scan results statistics
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN is_corrupted = 1 THEN 1 ELSE 0 END) as corrupted,
                SUM(CASE WHEN is_corrupted = 0 AND (has_warnings IS NULL OR has_warnings = 0) THEN 1 ELSE 0 END) as healthy,
                SUM(CASE WHEN has_warnings = 1 THEN 1 ELSE 0 END) as warnings,
                SUM(CASE WHEN scan_status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN scan_status = 'scanning' THEN 1 ELSE 0 END) as scanning,
                SUM(CASE WHEN marked_as_good = 1 THEN 1 ELSE 0 END) as marked_good
            FROM scan_results
        """)
        
        stats = cursor.fetchone()
        if stats and stats[0] > 0:
            print("📈 Scan Results Statistics:")
            print(f"   Total Files: {stats[0]:,}")
            print(f"   Healthy: {stats[2]:,}")
            print(f"   Corrupted: {stats[1]:,}")
            print(f"   Warnings: {stats[3] or 0:,}")
            print(f"   Pending: {stats[4]:,}")
            print(f"   Scanning: {stats[5]:,}")
            print(f"   Marked as Good: {stats[6]:,}")
            print()
            
            # Get sample of recent scans
            cursor.execute("""
                SELECT file_path, is_corrupted, scan_date 
                FROM scan_results 
                WHERE scan_date IS NOT NULL
                ORDER BY scan_date DESC 
                LIMIT 5
            """)
            recent = cursor.fetchall()
            
            if recent:
                print("🕒 Recent Scans:")
                for path, corrupted, scan_date in recent:
                    status = "❌ Corrupted" if corrupted else "✅ Healthy"
                    print(f"   {status} - {path[:60]}... ({scan_date})")
            
        else:
            print("⚠️  No scan results found in database")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Error accessing database: {e}")

if __name__ == "__main__":
    print("=== PixelProbe Database Test ===")
    print()
    test_database()
    print()