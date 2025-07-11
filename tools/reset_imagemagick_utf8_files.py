#!/usr/bin/env python3
"""
Reset ImageMagick UTF-8 decode files for rescanning.
This script resets files that were incorrectly marked as corrupted due to UTF-8 decode errors.
"""

import os
import sys
import sqlite3
import argparse
import logging
from pathlib import Path

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def reset_utf8_files(db_path, dry_run=True):
    """Reset files with UTF-8 errors for rescanning"""
    
    conn = None
    try:
        # Connect with timeout
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        
        # Find affected files
        logger.info("Finding files with ImageMagick UTF-8 decode errors...")
        cursor = conn.execute("""
            SELECT id, file_path, corruption_details, scan_output 
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND scan_tool = 'imagemagick'
            AND (
                corruption_details LIKE '%utf-8%codec%decode%' 
                OR corruption_details LIKE '%UnicodeDecodeError%'
                OR scan_output LIKE '%utf-8%codec%decode%'
                OR corruption_details LIKE '%ImageMagick error:%utf-8%'
            )
        """)
        
        files = cursor.fetchall()
        logger.info(f"Found {len(files)} files with UTF-8 decode errors")
        
        if len(files) == 0:
            logger.info("No files need to be reset")
            return True
            
        # Show files that will be reset
        logger.info("\nFiles to be reset for rescanning:")
        for file_id, file_path, details, output in files[:10]:  # Show first 10
            logger.info(f"  - {file_path}")
            if details:
                logger.info(f"    Current error: {details[:100]}")
        if len(files) > 10:
            logger.info(f"  ... and {len(files) - 10} more files")
        
        if dry_run:
            logger.info("\nDRY RUN - No changes made. Use --execute to apply changes.")
            return True
        
        # Reset the files
        logger.info("\nResetting files to pending status...")
        
        # Begin transaction
        conn.execute("BEGIN IMMEDIATE")
        
        try:
            # Reset to pending for rescanning
            result = conn.execute("""
                UPDATE scan_results 
                SET scan_status = 'pending',
                    is_corrupted = NULL,
                    corruption_details = NULL,
                    scan_date = NULL,
                    scan_tool = NULL,
                    scan_output = NULL,
                    scan_duration = NULL,
                    has_warnings = 0,
                    warning_details = NULL
                WHERE is_corrupted = 1 
                AND scan_tool = 'imagemagick'
                AND (
                    corruption_details LIKE '%utf-8%codec%decode%' 
                    OR corruption_details LIKE '%UnicodeDecodeError%'
                    OR scan_output LIKE '%utf-8%codec%decode%'
                    OR corruption_details LIKE '%ImageMagick error:%utf-8%'
                )
            """)
            
            updated_count = result.rowcount
            conn.commit()
            logger.info(f"Successfully reset {updated_count} files to pending status")
            logger.info("These files will be rescanned in the next scan run")
            return True
            
        except Exception as e:
            conn.rollback()
            raise
            
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            logger.error("Database is locked. Please stop any running scans and try again.")
        else:
            logger.error(f"Database error: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Error resetting files: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def main():
    parser = argparse.ArgumentParser(description='Reset ImageMagick UTF-8 decode files for rescanning')
    parser.add_argument('--execute', action='store_true', 
                       help='Execute the reset (without this flag, runs in dry-run mode)')
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
    logger.info("Note: The current code already handles UTF-8 decode errors properly,")
    logger.info("so these files should not be marked as corrupted after rescanning.")
    
    success = reset_utf8_files(str(db_path), dry_run=not args.execute)
    
    if success and not args.execute:
        logger.info("\nTo execute the reset, run: python reset_imagemagick_utf8_files.py --execute")
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()