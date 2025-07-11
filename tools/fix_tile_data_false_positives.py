#!/usr/bin/env python3
"""
Script to fix false positive corruptions caused by "Image has no tile data" error.
This script will:
1. Find all files marked as corrupted with this specific error
2. Re-scan them with the fixed corruption detection
3. Update the database records accordingly
"""

import os
import sys
import logging
from datetime import datetime
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the current directory to the path to import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import db, ScanResult
from media_checker import MediaChecker
from app import create_app

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def fix_tile_data_false_positives(dry_run=True):
    """
    Fix false positives caused by "Image has no tile data" error.
    
    Args:
        dry_run (bool): If True, only show what would be done without making changes
    """
    # Create app context for database access
    app = create_app()
    
    with app.app_context():
        # Initialize MediaChecker
        media_checker = MediaChecker()
        
        # Find all files marked as corrupted with "Image has no tile data"
        affected_files = ScanResult.query.filter(
            ScanResult.is_corrupted == True,
            ScanResult.corruption_details.contains("Image has no tile data")
        ).all()
        
        logger.info(f"Found {len(affected_files)} files with 'Image has no tile data' error")
        
        if dry_run:
            logger.info("DRY RUN MODE - No changes will be made")
        
        fixed_count = 0
        still_corrupted_count = 0
        error_count = 0
        
        for i, scan_result in enumerate(affected_files, 1):
            logger.info(f"Processing {i}/{len(affected_files)}: {scan_result.file_path}")
            
            try:
                # Check if file still exists
                if not os.path.exists(scan_result.file_path):
                    logger.warning(f"  File no longer exists: {scan_result.file_path}")
                    continue
                
                # Re-scan the file with the fixed detection logic
                logger.info(f"  Re-scanning file...")
                
                # Get the file extension to determine if it's an image
                _, ext = os.path.splitext(scan_result.file_path.lower())
                
                if ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.ico', '.heic', '.heif']:
                    # Re-check image corruption
                    is_corrupted, corruption_details, scan_output, scan_tool = media_checker._check_image_corruption(scan_result.file_path)
                    
                    # Log the results
                    logger.info(f"  New scan result: corrupted={is_corrupted}")
                    if corruption_details:
                        logger.info(f"  New corruption details: {'; '.join(corruption_details)}")
                    
                    # Update the database if not in dry run mode
                    if not dry_run:
                        scan_result.is_corrupted = is_corrupted
                        scan_result.corruption_details = '; '.join(corruption_details) if corruption_details else None
                        scan_result.scan_output = '\n'.join(scan_output) if scan_output else None
                        scan_result.scan_tool = scan_tool
                        scan_result.scan_duration = None  # Will be updated on next scan
                        scan_result.last_scanned = datetime.utcnow()
                        
                        db.session.commit()
                        logger.info(f"  Database updated")
                    
                    # Count the results
                    if not is_corrupted:
                        fixed_count += 1
                        logger.info(f"  ✓ File is now marked as NOT corrupted")
                    else:
                        still_corrupted_count += 1
                        logger.info(f"  ✗ File is still corrupted (different reason)")
                else:
                    logger.warning(f"  Skipping non-image file: {scan_result.file_path}")
                    
            except Exception as e:
                error_count += 1
                logger.error(f"  Error processing file: {e}")
                continue
        
        # Summary
        logger.info("\n" + "="*60)
        logger.info("SUMMARY:")
        logger.info(f"Total files processed: {len(affected_files)}")
        logger.info(f"Files fixed (no longer corrupted): {fixed_count}")
        logger.info(f"Files still corrupted (other reasons): {still_corrupted_count}")
        logger.info(f"Errors encountered: {error_count}")
        
        if dry_run:
            logger.info("\nDRY RUN COMPLETE - No changes were made")
            logger.info("Run with --execute to apply changes")

def main():
    """Main entry point for the script"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Fix false positive corruptions caused by 'Image has no tile data' error"
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Execute the fixes (without this flag, runs in dry-run mode)'
    )
    
    args = parser.parse_args()
    
    fix_tile_data_false_positives(dry_run=not args.execute)

if __name__ == "__main__":
    main()