#!/usr/bin/env python3
"""
Fix ImageMagick UTF-8 decode false positives.
This script updates files marked as corrupted due to UTF-8 decode errors to warnings instead.
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

def fix_utf8_errors(db_path, dry_run=True):
    """Update ImageMagick UTF-8 errors from corrupted to warnings"""
    
    conn = None
    try:
        # Connect with timeout
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        
        # Find affected files
        logger.info("Finding files with ImageMagick UTF-8 decode errors marked as corrupted...")
        cursor = conn.execute("""
            SELECT id, file_path, corruption_details, scan_output 
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND scan_tool = 'imagemagick'
            AND (
                corruption_details LIKE '%utf-8%codec%decode%' 
                OR corruption_details LIKE '%UnicodeDecodeError%'
                OR scan_output LIKE '%utf-8%codec%decode%'
            )
        """)
        
        files = cursor.fetchall()
        logger.info(f"Found {len(files)} files with UTF-8 decode errors")
        
        if len(files) == 0:
            logger.info("No files need to be updated")
            return True
            
        # Show files that will be updated
        logger.info("\nFiles to be updated:")
        for file_id, file_path, details, output in files[:10]:  # Show first 10
            logger.info(f"  - {file_path}")
            if details:
                logger.info(f"    Details: {details[:100]}")
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
                    warning_details = 'ImageMagick metadata contains non-UTF8 characters (not actual corruption)',
                    corruption_details = NULL
                WHERE is_corrupted = 1 
                AND scan_tool = 'imagemagick'
                AND (
                    corruption_details LIKE '%utf-8%codec%decode%' 
                    OR corruption_details LIKE '%UnicodeDecodeError%'
                    OR scan_output LIKE '%utf-8%codec%decode%'
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
    parser = argparse.ArgumentParser(description='Fix ImageMagick UTF-8 decode false positives')
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
    
    success = fix_utf8_errors(str(db_path), dry_run=not args.execute)
    
    if success and not args.execute:
        logger.info("\nTo execute the update, run: python fix_imagemagick_utf8_errors.py --execute")
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()