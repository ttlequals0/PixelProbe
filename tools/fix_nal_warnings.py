#!/usr/bin/env python3
"""
Fix NAL unit files by updating them to warnings instead of corrupted.
This script updates the database directly without needing to rescan.
"""

import os
import sys
import time
import sqlite3
import argparse
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fix_nal_warnings(db_path, dry_run=True):
    """Update NAL unit errors from corrupted to warnings"""
    
    conn = None
    try:
        # Connect with timeout
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        
        # Find affected files
        logger.info("Finding files with NAL unit errors marked as corrupted...")
        cursor = conn.execute("""
            SELECT id, file_path, corruption_details 
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND scan_tool = 'ffmpeg'
            AND (
                corruption_details LIKE '%Invalid NAL unit%' 
                OR corruption_details LIKE '%NAL unit errors%'
                OR scan_output LIKE '%Invalid NAL unit%'
            )
        """)
        
        files = cursor.fetchall()
        logger.info(f"Found {len(files)} files with NAL unit errors")
        
        if len(files) == 0:
            logger.info("No files need to be updated")
            return True
            
        # Show files that will be updated
        logger.info("\nFiles to be updated:")
        for file_id, file_path, details in files[:10]:  # Show first 10
            logger.info(f"  - {file_path}")
        if len(files) > 10:
            logger.info(f"  ... and {len(files) - 10} more files")
        
        if dry_run:
            logger.info("\nDRY RUN - No changes made. Use --execute to apply changes.")
            return True
        
        # Update the files
        logger.info("\nUpdating files to warning status...")
        
        # Begin transaction
        conn.execute("BEGIN IMMEDIATE")
        
        try:
            # Update to warning instead of corrupted
            result = conn.execute("""
                UPDATE scan_results 
                SET is_corrupted = 0,
                    has_warnings = 1,
                    warning_details = 'NAL unit errors detected (video may have playback issues)',
                    corruption_details = NULL
                WHERE is_corrupted = 1 
                AND scan_tool = 'ffmpeg'
                AND (
                    corruption_details LIKE '%Invalid NAL unit%' 
                    OR corruption_details LIKE '%NAL unit errors%'
                    OR scan_output LIKE '%Invalid NAL unit%'
                )
            """)
            
            updated_count = result.rowcount
            conn.commit()
            logger.info(f"Successfully updated {updated_count} files from corrupted to warning status")
            return True
            
        except Exception as e:
            conn.rollback()
            raise
            
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            logger.error("Database is locked. Please stop any running scans and try again.")
            logger.error("You can also try: systemctl restart pixelprobe (or docker restart <container>)")
        else:
            logger.error(f"Database error: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Error updating files: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def main():
    parser = argparse.ArgumentParser(description='Fix NAL unit false positives by converting to warnings')
    parser.add_argument('--execute', action='store_true', 
                       help='Execute the update (without this flag, runs in dry-run mode)')
    parser.add_argument('--db-path', type=str, 
                       default='/app/instance/media_checker.db',
                       help='Path to the database file')
    args = parser.parse_args()
    
    # Check if database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        sys.exit(1)
    
    logger.info(f"Using database: {db_path}")
    
    success = fix_nal_warnings(str(db_path), dry_run=not args.execute)
    
    if success and not args.execute:
        logger.info("\nTo execute the update, run: python fix_nal_warnings.py --execute")
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()