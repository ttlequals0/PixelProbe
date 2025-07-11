#!/usr/bin/env python3
"""
Alternative approach using direct SQL to fix the tile data false positives.
This is faster for large datasets but doesn't re-scan the files.
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
    """Generate SQL statements to fix the false positives"""
    
    # SQL to find affected records
    find_sql = """
    SELECT id, file_path, corruption_details 
    FROM scan_results 
    WHERE is_corrupted = 1 
    AND corruption_details LIKE '%Image has no tile data%';
    """
    
    # SQL to update records where "Image has no tile data" is the ONLY error
    fix_only_tile_error_sql = """
    UPDATE scan_results 
    SET is_corrupted = 0, 
        corruption_details = NULL,
        scan_output = REPLACE(scan_output, 'Tile data: MISSING', 'Tile data: OK')
    WHERE is_corrupted = 1 
    AND corruption_details = 'Image has no tile data';
    """
    
    # SQL to remove "Image has no tile data" from records with multiple errors
    fix_multiple_errors_sql = """
    UPDATE scan_results 
    SET corruption_details = 
        CASE
            -- When it's at the beginning
            WHEN corruption_details LIKE 'Image has no tile data; %' 
            THEN SUBSTR(corruption_details, LENGTH('Image has no tile data; ') + 1)
            
            -- When it's in the middle
            WHEN corruption_details LIKE '%; Image has no tile data; %'
            THEN REPLACE(corruption_details, '; Image has no tile data', '')
            
            -- When it's at the end
            WHEN corruption_details LIKE '%; Image has no tile data'
            THEN SUBSTR(corruption_details, 1, LENGTH(corruption_details) - LENGTH('; Image has no tile data'))
            
            ELSE corruption_details
        END,
        scan_output = REPLACE(scan_output, 'Tile data: MISSING', 'Tile data: OK')
    WHERE is_corrupted = 1 
    AND corruption_details LIKE '%Image has no tile data%'
    AND corruption_details != 'Image has no tile data';
    """
    
    # SQL to check if any records should be marked as not corrupted after removal
    check_empty_corruption_sql = """
    UPDATE scan_results 
    SET is_corrupted = 0 
    WHERE is_corrupted = 1 
    AND (corruption_details IS NULL OR corruption_details = '');
    """
    
    return {
        'find': find_sql,
        'fix_only': fix_only_tile_error_sql,
        'fix_multiple': fix_multiple_errors_sql,
        'check_empty': check_empty_corruption_sql
    }

def fix_via_sql(database_url=None, dry_run=True):
    """
    Fix false positives using direct SQL updates.
    
    Args:
        database_url: Database connection string (defaults to sqlite:///media_checker.db)
        dry_run: If True, only show what would be done
    """
    if not database_url:
        database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
    
    engine = create_engine(database_url)
    sql_queries = generate_fix_sql()
    
    with engine.connect() as conn:
        # Find affected records
        logger.info("Finding affected records...")
        result = conn.execute(text(sql_queries['find']))
        affected_records = result.fetchall()
        
        logger.info(f"Found {len(affected_records)} records with 'Image has no tile data' error")
        
        if dry_run:
            logger.info("\nDRY RUN MODE - Showing what would be updated:")
            for record in affected_records[:10]:  # Show first 10
                logger.info(f"  ID: {record[0]}, Path: {record[1]}")
                logger.info(f"    Current corruption: {record[2]}")
            if len(affected_records) > 10:
                logger.info(f"  ... and {len(affected_records) - 10} more records")
        else:
            logger.info("\nApplying fixes...")
            
            # Fix records where tile error is the only error
            result1 = conn.execute(text(sql_queries['fix_only']))
            logger.info(f"Fixed {result1.rowcount} records with only 'Image has no tile data' error")
            
            # Fix records with multiple errors
            result2 = conn.execute(text(sql_queries['fix_multiple']))
            logger.info(f"Removed tile error from {result2.rowcount} records with multiple errors")
            
            # Clean up any empty corruption details
            result3 = conn.execute(text(sql_queries['check_empty']))
            logger.info(f"Marked {result3.rowcount} additional records as not corrupted")
            
            conn.commit()
            logger.info("\nDatabase updated successfully!")

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Fix tile data false positives using direct SQL"
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
    
    fix_via_sql(
        database_url=args.database_url,
        dry_run=not args.execute
    )

if __name__ == "__main__":
    main()