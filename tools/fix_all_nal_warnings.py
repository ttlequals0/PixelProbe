#!/usr/bin/env python3
"""
Fix ALL NAL unit files by converting them from corrupted to warnings.
This comprehensive script handles all variations of NAL unit errors.
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

def fix_all_nal_warnings(db_path, dry_run=True):
    """Convert all NAL unit errors from corrupted to warnings"""
    
    conn = None
    try:
        # Connect with timeout
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        
        # Find ALL files with NAL/h264 errors marked as corrupted
        logger.info("Finding all files with NAL/h264 errors marked as corrupted...")
        cursor = conn.execute("""
            SELECT id, file_path, corruption_details, scan_output 
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND scan_tool = 'ffmpeg'
            AND (
                corruption_details LIKE '%NAL unit%' 
                OR corruption_details LIKE '%h264%'
                OR corruption_details LIKE '%Invalid NAL%'
                OR scan_output LIKE '%NAL unit%'
                OR scan_output LIKE '%h264%'
                OR scan_output LIKE '%Invalid NAL%'
                OR corruption_details LIKE '%[h264 @%'
                OR scan_output LIKE '%[h264 @%'
            )
        """)
        
        files = cursor.fetchall()
        logger.info(f"Found {len(files)} files with NAL/h264 errors marked as corrupted")
        
        if len(files) == 0:
            logger.info("No files need to be updated")
            return True
            
        # Show files that will be updated
        logger.info("\nFiles to be converted from corrupted to warning:")
        for file_id, file_path, details, output in files[:10]:  # Show first 10
            logger.info(f"  - {file_path}")
            if details:
                logger.info(f"    Current error: {details[:100]}")
        if len(files) > 10:
            logger.info(f"  ... and {len(files) - 10} more files")
        
        if dry_run:
            logger.info("\nDRY RUN - No changes made. Use --execute to apply changes.")
            logger.info("\nThis will convert these files from corrupted status to warning status.")
            logger.info("They will show the orange Warning badge instead of red Corrupted.")
            return True
        
        # Update the files
        logger.info("\nConverting files from corrupted to warning status...")
        
        # Begin transaction
        conn.execute("BEGIN IMMEDIATE")
        
        try:
            # Update to warning instead of corrupted
            result = conn.execute("""
                UPDATE scan_results 
                SET is_corrupted = 0,
                    has_warnings = 1,
                    warning_details = CASE 
                        WHEN corruption_details LIKE '%NAL unit%' THEN 'NAL unit errors detected (video may have playback issues)'
                        WHEN corruption_details LIKE '%h264%' THEN 'H264 codec warnings detected (video may have playback issues)'
                        ELSE 'Video codec warnings detected'
                    END
                WHERE is_corrupted = 1 
                AND scan_tool = 'ffmpeg'
                AND (
                    corruption_details LIKE '%NAL unit%' 
                    OR corruption_details LIKE '%h264%'
                    OR corruption_details LIKE '%Invalid NAL%'
                    OR scan_output LIKE '%NAL unit%'
                    OR scan_output LIKE '%h264%'
                    OR scan_output LIKE '%Invalid NAL%'
                    OR corruption_details LIKE '%[h264 @%'
                    OR scan_output LIKE '%[h264 @%'
                )
            """)
            
            updated_count = result.rowcount
            conn.commit()
            logger.info(f"Successfully converted {updated_count} files from corrupted to warning status")
            logger.info("\nThese files will now:")
            logger.info("- Show with orange Warning badge instead of red Corrupted")
            logger.info("- NOT appear in the Corrupted Only filter")
            logger.info("- Appear in the Warnings Only filter")
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
    parser = argparse.ArgumentParser(description='Fix ALL NAL/h264 files by converting to warnings')
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
    logger.info("This script will convert NAL/h264 errors from corrupted to warning status")
    
    success = fix_all_nal_warnings(str(db_path), dry_run=not args.execute)
    
    if success and not args.execute:
        logger.info("\nTo execute the conversion, run: python fix_all_nal_warnings.py --execute")
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()