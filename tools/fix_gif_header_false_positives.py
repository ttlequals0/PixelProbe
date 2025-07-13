#!/usr/bin/env python3
"""
Fix GIF files incorrectly marked as corrupted due to "improper image header" errors.
This converts them to warnings since many of these GIFs are actually playable.
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

def fix_gif_header_warnings(db_path, dry_run=True):
    """Convert GIF improper header errors from corrupted to warnings"""
    
    conn = None
    try:
        # Connect with timeout
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        
        # Find GIF files with improper header errors marked as corrupted
        logger.info("Finding GIF files with 'improper image header' errors marked as corrupted...")
        cursor = conn.execute("""
            SELECT id, file_path, corruption_details, scan_output 
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND scan_tool IN ('imagemagick', 'pil')
            AND file_path LIKE '%.gif'
            AND (
                corruption_details LIKE '%improper image header%ReadGIFImage%'
                OR scan_output LIKE '%improper image header%ReadGIFImage%'
                OR corruption_details LIKE '%cannot identify image file%'
            )
        """)
        
        files = cursor.fetchall()
        logger.info(f"Found {len(files)} GIF files with header errors marked as corrupted")
        
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
            logger.info("\nNote: These GIFs likely have non-standard headers but may still be playable.")
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
                        WHEN corruption_details LIKE '%improper image header%' THEN 'GIF header warning: Non-standard header detected (file may still be playable)'
                        WHEN corruption_details LIKE '%cannot identify image file%' THEN 'GIF format warning: PIL cannot identify format (file may still be playable)'
                        ELSE 'GIF format warning detected'
                    END
                WHERE is_corrupted = 1 
                AND scan_tool IN ('imagemagick', 'pil')
                AND file_path LIKE '%.gif'
                AND (
                    corruption_details LIKE '%improper image header%ReadGIFImage%'
                    OR scan_output LIKE '%improper image header%ReadGIFImage%'
                    OR corruption_details LIKE '%cannot identify image file%'
                )
            """)
            
            updated_count = result.rowcount
            conn.commit()
            logger.info(f"Successfully converted {updated_count} files from corrupted to warning status")
            logger.info("\nThese files will now:")
            logger.info("- Show with orange Warning badge instead of red Corrupted")
            logger.info("- NOT appear in the Corrupted Only filter")
            logger.info("- Appear in the Warnings Only filter")
            logger.info("\nNote: Many GIFs with header warnings are still playable in browsers/viewers")
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
    parser = argparse.ArgumentParser(description='Fix GIF files with improper header false positives')
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
    logger.info("This script will convert GIF 'improper image header' errors to warning status")
    logger.info("These GIFs often work fine despite the header warning")
    
    success = fix_gif_header_warnings(str(db_path), dry_run=not args.execute)
    
    if success and not args.execute:
        logger.info("\nTo execute the conversion, run: python fix_gif_header_false_positives.py --execute")
    
    sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()