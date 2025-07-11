#!/usr/bin/env python3
"""
Script to fix false positive corruptions caused by FFmpeg NAL unit errors.
NAL unit errors alone often don't indicate actual video corruption.
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
    """Generate SQL statements to fix NAL unit false positives"""
    
    # Find records marked corrupted with NAL unit errors
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
    
    # Fix records where NAL unit error is the ONLY corruption indicator
    fix_nal_only_sql = """
    UPDATE scan_results 
    SET is_corrupted = 0, 
        corruption_details = NULL,
        scan_output = REPLACE(scan_output, 
            'Stage 4: Strict error detection\n  Result: FAILED - Strict error detection failed; Invalid NAL unit structure',
            'Stage 4: Strict error detection\n  Result: PASSED (NAL unit warnings only)'
        )
    WHERE is_corrupted = 1 
    AND scan_tool = 'ffmpeg'
    AND (
        corruption_details = 'FFmpeg errors: Invalid NAL unit errors detected'
        OR corruption_details = 'Strict error detection failed; Invalid NAL unit structure'
        OR (corruption_details LIKE '%Invalid NAL unit%' AND corruption_details NOT LIKE '%;%')
    );
    """
    
    # For records with multiple errors including NAL, check if NAL is the only real error
    check_multi_error_sql = """
    SELECT id, file_path, corruption_details
    FROM scan_results 
    WHERE is_corrupted = 1 
    AND scan_tool = 'ffmpeg'
    AND corruption_details LIKE '%;%'
    AND corruption_details LIKE '%NAL unit%';
    """
    
    # Remove NAL unit errors from multi-error records if appropriate
    fix_multi_with_nal_sql = """
    UPDATE scan_results 
    SET corruption_details = 
        CASE
            -- Remove "Strict error detection failed; Invalid NAL unit structure" pattern
            WHEN corruption_details = 'Strict error detection failed; Invalid NAL unit structure; Macroblock decoding error'
            THEN 'Macroblock decoding error'
            
            -- Remove standalone NAL unit error from beginning
            WHEN corruption_details LIKE 'Invalid NAL unit%; %'
            THEN SUBSTR(corruption_details, INSTR(corruption_details, '; ') + 2)
            
            -- Remove from middle
            WHEN corruption_details LIKE '%; Invalid NAL unit%; %'
            THEN REPLACE(corruption_details, '; Invalid NAL unit structure', '')
            
            -- Remove from end
            WHEN corruption_details LIKE '%; Invalid NAL unit%'
            THEN SUBSTR(corruption_details, 1, LENGTH(corruption_details) - LENGTH('; Invalid NAL unit structure'))
            
            ELSE corruption_details
        END
    WHERE is_corrupted = 1 
    AND scan_tool = 'ffmpeg'
    AND corruption_details LIKE '%Invalid NAL unit%'
    AND corruption_details LIKE '%;%';
    """
    
    # Check if any records should be unmarked after NAL removal
    check_empty_sql = """
    UPDATE scan_results 
    SET is_corrupted = 0 
    WHERE is_corrupted = 1 
    AND (corruption_details IS NULL OR corruption_details = '' OR corruption_details = 'Strict error detection failed');
    """
    
    return {
        'find': find_sql,
        'fix_nal_only': fix_nal_only_sql,
        'check_multi': check_multi_error_sql,
        'fix_multi': fix_multi_with_nal_sql,
        'check_empty': check_empty_sql
    }

def fix_nal_false_positives(database_url=None, dry_run=True):
    """
    Fix false positives from NAL unit errors.
    
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
        logger.info("Finding records with NAL unit errors...")
        result = conn.execute(text(sql_queries['find']))
        affected_records = result.fetchall()
        
        logger.info(f"Found {len(affected_records)} records with NAL unit errors")
        
        if dry_run:
            logger.info("\nDRY RUN MODE - Showing affected records:")
            for i, record in enumerate(affected_records[:10], 1):
                logger.info(f"\n{i}. ID: {record[0]}")
                logger.info(f"   Path: {record[1]}")
                logger.info(f"   Corruption: {record[2]}")
            if len(affected_records) > 10:
                logger.info(f"\n... and {len(affected_records) - 10} more records")
                
            # Check multi-error records
            result2 = conn.execute(text(sql_queries['check_multi']))
            multi_records = result2.fetchall()
            if multi_records:
                logger.info(f"\nFound {len(multi_records)} records with multiple errors including NAL")
        else:
            logger.info("\nApplying fixes...")
            
            # Fix NAL-only records
            result1 = conn.execute(text(sql_queries['fix_nal_only']))
            logger.info(f"Fixed {result1.rowcount} records with only NAL unit errors")
            
            # Fix multi-error records
            result2 = conn.execute(text(sql_queries['fix_multi']))
            logger.info(f"Removed NAL errors from {result2.rowcount} multi-error records")
            
            # Clean up empty records
            result3 = conn.execute(text(sql_queries['check_empty']))
            logger.info(f"Marked {result3.rowcount} additional records as not corrupted")
            
            conn.commit()
            logger.info("\nDatabase updated successfully!")

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Fix FFmpeg NAL unit false positives"
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
    
    fix_nal_false_positives(
        database_url=args.database_url,
        dry_run=not args.execute
    )

if __name__ == "__main__":
    main()