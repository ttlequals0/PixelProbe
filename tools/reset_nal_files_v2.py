#!/usr/bin/env python3
"""
Reset NAL unit false positives for rescanning with retry logic and better error handling.
This script resets files that were incorrectly marked as corrupted due to NAL unit errors.
Version 2 with database lock handling.
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path

# Add the parent directory to the path to import models
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import db, ScanResult
from flask import Flask
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def wait_for_database(max_attempts=10, delay=2):
    """Wait for database to be available"""
    for attempt in range(max_attempts):
        try:
            # Try a simple query
            db.session.execute(text("SELECT 1"))
            db.session.commit()
            return True
        except OperationalError as e:
            if "database is locked" in str(e):
                logger.warning(f"Database is locked, waiting {delay} seconds... (attempt {attempt + 1}/{max_attempts})")
                time.sleep(delay)
                db.session.rollback()
            else:
                raise
    return False

def reset_nal_files_for_rescan(dry_run=True, max_retries=5):
    """Reset files with NAL unit errors for rescanning with retry logic"""
    
    # SQL to find files with NAL unit errors
    find_sql = """
    SELECT id, file_path, corruption_details, scan_output 
    FROM scan_results 
    WHERE is_corrupted = 1 
    AND scan_tool = 'ffmpeg'
    AND (
        corruption_details LIKE '%Invalid NAL unit%' 
        OR corruption_details LIKE '%NAL unit errors%'
        OR scan_output LIKE '%Invalid NAL unit%'
    );
    """
    
    # SQL to reset these files
    reset_sql = """
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
    AND scan_tool = 'ffmpeg'
    AND (
        corruption_details LIKE '%Invalid NAL unit%' 
        OR corruption_details LIKE '%NAL unit errors%'
        OR scan_output LIKE '%Invalid NAL unit%'
    );
    """
    
    try:
        # Wait for database to be available
        if not wait_for_database():
            logger.error("Could not access database after multiple attempts")
            return False
            
        # Find affected files
        logger.info("Finding files with NAL unit errors...")
        
        for attempt in range(max_retries):
            try:
                result = db.session.execute(text(find_sql))
                files = result.fetchall()
                break
            except OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"Database locked, retrying in 2 seconds... (attempt {attempt + 1}/{max_retries})")
                    db.session.rollback()
                    time.sleep(2)
                else:
                    raise
        
        logger.info(f"Found {len(files)} files with NAL unit errors")
        
        if len(files) == 0:
            logger.info("No files need to be reset")
            return True
            
        # Show files that will be reset
        logger.info("\nFiles to be reset:")
        for file in files:
            logger.info(f"  - {file.file_path}")
            if file.corruption_details:
                logger.info(f"    Details: {file.corruption_details[:100]}...")
        
        if dry_run:
            logger.info("\nDRY RUN - No changes made. Use --execute to apply changes.")
            return True
        
        # Reset the files with retry logic
        logger.info("\nResetting files to pending status...")
        
        for attempt in range(max_retries):
            try:
                result = db.session.execute(text(reset_sql))
                affected_rows = result.rowcount
                db.session.commit()
                logger.info(f"Successfully reset {affected_rows} files")
                return True
            except OperationalError as e:
                if "database is locked" in str(e) and attempt < max_retries - 1:
                    logger.warning(f"Database locked during update, retrying in 3 seconds... (attempt {attempt + 1}/{max_retries})")
                    db.session.rollback()
                    time.sleep(3)
                else:
                    db.session.rollback()
                    raise
                    
    except Exception as e:
        logger.error(f"Error resetting files: {str(e)}")
        db.session.rollback()
        return False

def main():
    parser = argparse.ArgumentParser(description='Reset NAL unit false positives for rescanning')
    parser.add_argument('--execute', action='store_true', 
                       help='Execute the reset (without this flag, runs in dry-run mode)')
    parser.add_argument('--retries', type=int, default=5,
                       help='Maximum number of retries for database operations (default: 5)')
    args = parser.parse_args()
    
    # Create Flask app and initialize database
    app = Flask(__name__)
    
    # Look for database in the instance folder
    instance_path = Path(__file__).parent
    db_path = instance_path / 'media_checker.db'
    
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        sys.exit(1)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'timeout': 30,  # 30 second timeout
            'check_same_thread': False,
            'isolation_level': 'DEFERRED',
        }
    }
    
    db.init_app(app)
    
    with app.app_context():
        # Enable WAL mode for better concurrency
        try:
            db.session.execute(text("PRAGMA journal_mode=WAL"))
            db.session.execute(text("PRAGMA busy_timeout=30000"))
            db.session.commit()
        except Exception as e:
            logger.warning(f"Could not set WAL mode: {str(e)}")
            db.session.rollback()
            
        success = reset_nal_files_for_rescan(
            dry_run=not args.execute,
            max_retries=args.retries
        )
        
        if success and not args.execute:
            logger.info("\nTo execute the reset, run: python reset_nal_files_v2.py --execute")
        
        sys.exit(0 if success else 1)

if __name__ == '__main__':
    main()