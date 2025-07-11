#!/usr/bin/env python3
"""
Script to reset files with NAL unit errors for rescanning.
This will allow the updated code to properly set them as warnings instead of corrupted.
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def reset_nal_files_for_rescan(database_url=None, dry_run=True):
    """
    Reset files with NAL unit errors to pending status for rescanning.
    
    Args:
        database_url: Database connection string
        dry_run: If True, only show what would be done
    """
    if not database_url:
        database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
    
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        # Find files with NAL unit errors that are marked as corrupted
        find_sql = """
        SELECT id, file_path, corruption_details
        FROM scan_results 
        WHERE is_corrupted = 1 
        AND scan_tool = 'ffmpeg'
        AND (
            corruption_details LIKE '%Invalid NAL unit%' 
            OR corruption_details LIKE '%NAL unit errors%'
            OR scan_output LIKE '%Invalid NAL unit%'
        );
        """
        
        logger.info("Finding files with NAL unit errors...")
        result = conn.execute(text(find_sql))
        affected_records = result.fetchall()
        
        logger.info(f"Found {len(affected_records)} files with NAL unit errors")
        
        if dry_run:
            logger.info("\nDRY RUN MODE - Showing files that would be reset:")
            for i, record in enumerate(affected_records[:10], 1):
                logger.info(f"\n{i}. ID: {record[0]}")
                logger.info(f"   Path: {record[1]}")
                logger.info(f"   Corruption: {record[2]}")
            if len(affected_records) > 10:
                logger.info(f"\n... and {len(affected_records) - 10} more files")
            logger.info("\nRun with --execute to reset these files for rescanning")
        else:
            # Reset files to pending status so they'll be rescanned
            reset_sql = """
            UPDATE scan_results 
            SET scan_status = 'pending',
                is_corrupted = NULL,
                corruption_details = NULL,
                scan_date = NULL,
                scan_tool = NULL,
                scan_output = NULL,
                scan_duration = NULL
            WHERE is_corrupted = 1 
            AND scan_tool = 'ffmpeg'
            AND (
                corruption_details LIKE '%Invalid NAL unit%' 
                OR corruption_details LIKE '%NAL unit errors%'
                OR scan_output LIKE '%Invalid NAL unit%'
            );
            """
            
            logger.info("\nResetting files to pending status...")
            result = conn.execute(text(reset_sql))
            conn.commit()
            
            logger.info(f"Reset {result.rowcount} files for rescanning")
            logger.info("\nThese files will be rescanned on the next scan run.")
            logger.info("The updated code will properly detect NAL-only errors as warnings.")

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Reset files with NAL unit errors for rescanning"
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Execute the reset (without this flag, runs in dry-run mode)'
    )
    parser.add_argument(
        '--database-url',
        help='Database URL (defaults to sqlite:///media_checker.db or DATABASE_URL env var)'
    )
    
    args = parser.parse_args()
    
    reset_nal_files_for_rescan(
        database_url=args.database_url,
        dry_run=not args.execute
    )

if __name__ == "__main__":
    main()