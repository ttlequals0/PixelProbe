#!/usr/bin/env python3
"""
Script to fix false positive corruptions caused by ImageMagick profile warnings.
These warnings (like CorruptImageProfile for XMP) don't indicate actual image corruption.
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

def generate_fix_sql():
    """Generate SQL statements to fix the false positives from profile warnings"""
    
    # SQL to find affected records - looking for CorruptImageProfile warnings
    find_sql = """
    SELECT id, file_path, corruption_details, scan_output
    FROM scan_results 
    WHERE is_corrupted = 1 
    AND scan_tool = 'imagemagick'
    AND (
        corruption_details LIKE '%CorruptImageProfile%' 
        OR scan_output LIKE '%CorruptImageProfile%@warning/profile.c%'
    );
    """
    
    # SQL to update records where profile warning is the ONLY error
    fix_only_profile_sql = """
    UPDATE scan_results 
    SET is_corrupted = 0, 
        corruption_details = NULL,
        scan_output = REPLACE(
            REPLACE(scan_output, 
                'ImageMagick warnings:', 
                'ImageMagick identify: PASSED (with profile warnings)'
            ),
            'Tile data: MISSING',
            'Tile data: OK'
        )
    WHERE is_corrupted = 1 
    AND scan_tool = 'imagemagick'
    AND corruption_details LIKE 'ImageMagick warnings: %CorruptImageProfile%'
    AND corruption_details NOT LIKE '%;%';
    """
    
    # For records with multiple errors, just remove the ImageMagick profile warning
    fix_multiple_errors_sql = """
    UPDATE scan_results 
    SET corruption_details = 
        CASE
            -- When ImageMagick warning is at the beginning
            WHEN corruption_details LIKE 'ImageMagick warnings: %CorruptImageProfile%; %' 
            THEN SUBSTR(corruption_details, INSTR(corruption_details, '; ') + 2)
            
            -- When ImageMagick warning is in the middle
            WHEN corruption_details LIKE '%; ImageMagick warnings: %CorruptImageProfile%; %'
            THEN REPLACE(corruption_details, 
                SUBSTR(corruption_details, 
                    INSTR(corruption_details, '; ImageMagick warnings:'),
                    INSTR(SUBSTR(corruption_details, INSTR(corruption_details, '; ImageMagick warnings:') + 2), '; ') + 2
                ), 
                ''
            )
            
            -- When ImageMagick warning is at the end
            WHEN corruption_details LIKE '%; ImageMagick warnings: %CorruptImageProfile%'
            THEN SUBSTR(corruption_details, 1, INSTR(corruption_details, '; ImageMagick warnings:') - 1)
            
            ELSE corruption_details
        END,
        scan_output = REPLACE(scan_output, 
            'ImageMagick warnings:', 
            'ImageMagick identify: PASSED (with profile warnings)'
        )
    WHERE is_corrupted = 1 
    AND (
        corruption_details LIKE '%ImageMagick warnings: %CorruptImageProfile%' 
        OR scan_output LIKE '%CorruptImageProfile%@warning/profile.c%'
    )
    AND corruption_details LIKE '%;%';
    """
    
    # SQL to check if any records should be marked as not corrupted after removal
    check_empty_corruption_sql = """
    UPDATE scan_results 
    SET is_corrupted = 0 
    WHERE is_corrupted = 1 
    AND (corruption_details IS NULL OR corruption_details = '');
    """
    
    # Also update scan_tool if it was only marked by imagemagick
    update_scan_tool_sql = """
    UPDATE scan_results
    SET scan_tool = 'pil'
    WHERE is_corrupted = 0
    AND scan_tool = 'imagemagick'
    AND scan_output LIKE '%PIL verification: PASSED%';
    """
    
    return {
        'find': find_sql,
        'fix_only': fix_only_profile_sql,
        'fix_multiple': fix_multiple_errors_sql,
        'check_empty': check_empty_corruption_sql,
        'update_tool': update_scan_tool_sql
    }

def fix_profile_warnings(database_url=None, dry_run=True):
    """
    Fix false positives from ImageMagick profile warnings.
    
    Args:
        database_url: Database connection string
        dry_run: If True, only show what would be done
    """
    if not database_url:
        database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
    
    engine = create_engine(database_url)
    sql_queries = generate_fix_sql()
    
    with engine.connect() as conn:
        # Find affected records
        logger.info("Finding records with ImageMagick profile warnings...")
        result = conn.execute(text(sql_queries['find']))
        affected_records = result.fetchall()
        
        logger.info(f"Found {len(affected_records)} records with ImageMagick profile warnings")
        
        if dry_run:
            logger.info("\nDRY RUN MODE - Showing what would be updated:")
            for record in affected_records[:10]:  # Show first 10
                logger.info(f"\n  ID: {record[0]}")
                logger.info(f"  Path: {record[1]}")
                logger.info(f"  Corruption: {record[2]}")
                if 'CorruptImageProfile' in str(record[3]):
                    logger.info(f"  Contains CorruptImageProfile warning")
            if len(affected_records) > 10:
                logger.info(f"\n  ... and {len(affected_records) - 10} more records")
        else:
            logger.info("\nApplying fixes...")
            
            # Fix records where profile warning is the only error
            result1 = conn.execute(text(sql_queries['fix_only']))
            logger.info(f"Fixed {result1.rowcount} records with only profile warnings")
            
            # Fix records with multiple errors
            result2 = conn.execute(text(sql_queries['fix_multiple']))
            logger.info(f"Removed profile warnings from {result2.rowcount} records with multiple errors")
            
            # Clean up any empty corruption details
            result3 = conn.execute(text(sql_queries['check_empty']))
            logger.info(f"Marked {result3.rowcount} additional records as not corrupted")
            
            # Update scan tool
            result4 = conn.execute(text(sql_queries['update_tool']))
            logger.info(f"Updated scan tool for {result4.rowcount} records")
            
            conn.commit()
            logger.info("\nDatabase updated successfully!")

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Fix ImageMagick profile warning false positives"
    )
    parser.add_argument(
        '--execute',
        action='store_true',
        help='Execute the SQL updates (without this flag, runs in dry-run mode)'
    )
    parser.add_argument(
        '--database-url',
        help='Database URL (defaults to sqlite:///media_checker.db or DATABASE_URL env var)'
    )
    
    args = parser.parse_args()
    
    fix_profile_warnings(
        database_url=args.database_url,
        dry_run=not args.execute
    )

if __name__ == "__main__":
    main()