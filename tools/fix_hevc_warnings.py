#!/usr/bin/env python3
"""
Fix to stop flagging 10-bit HEVC files as warnings
These are valid files that just need proper hardware/software support
"""

import os
import sys
from sqlalchemy import create_engine, text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_hevc_warnings():
    """Remove warning status from 10-bit HEVC files"""
    
    # Get database configuration
    db_host = os.environ.get('POSTGRES_HOST', 'postgres')
    db_port = os.environ.get('POSTGRES_PORT', '5432')
    db_name = os.environ.get('POSTGRES_DB', 'pixelprobe')
    db_user = os.environ.get('POSTGRES_USER', 'pixelprobe')
    db_pass = os.environ.get('POSTGRES_PASSWORD', '')
    
    database_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    
    logger.info("=" * 50)
    logger.info("PixelProbe HEVC 10-bit Warning Fix")
    logger.info("=" * 50)
    
    try:
        engine = create_engine(database_url)
        
        with engine.connect() as conn:
            # 1. Find all files marked as warning just for being 10-bit HEVC
            logger.info("\n1. Finding 10-bit HEVC files marked as warnings...")
            trans = conn.begin()
            try:
                result = conn.execute(text("""
                    SELECT COUNT(*) 
                    FROM scan_results 
                    WHERE has_warnings = TRUE 
                    AND is_corrupted = FALSE
                    AND warning_details LIKE '%HEVC Main 10%'
                    AND warning_details LIKE '%10-bit%'
                    AND warning_details LIKE '%requires hardware/software support%'
                """))
                
                count = result.scalar()
                logger.info(f"   Found {count} files with false-positive HEVC warnings")
                
                if count > 0:
                    # Update these files to remove warning status
                    result = conn.execute(text("""
                        UPDATE scan_results 
                        SET has_warnings = FALSE,
                            warning_details = NULL,
                            error_message = CASE 
                                WHEN error_message LIKE '%HEVC Main 10%' THEN NULL
                                ELSE error_message
                            END
                        WHERE has_warnings = TRUE 
                        AND is_corrupted = FALSE
                        AND warning_details LIKE '%HEVC Main 10%'
                        AND warning_details LIKE '%10-bit%'
                        AND warning_details LIKE '%requires hardware/software support%'
                    """))
                    
                    updated = result.rowcount
                    logger.info(f"   ✅ Cleared warnings from {updated} 10-bit HEVC files")
                else:
                    logger.info("   ✅ No false-positive HEVC warnings found")
                    
                trans.commit()
            except Exception as e:
                trans.rollback()
                logger.error(f"   ❌ Failed to fix HEVC warnings: {e}")
            
            # 2. Show sample of remaining warnings to verify they're legitimate
            logger.info("\n2. Checking remaining warning files...")
            result = conn.execute(text("""
                SELECT warning_details, COUNT(*) as count
                FROM scan_results 
                WHERE has_warnings = TRUE 
                AND is_corrupted = FALSE
                GROUP BY warning_details
                ORDER BY count DESC
                LIMIT 5
            """))
            
            warnings = result.fetchall()
            if warnings:
                logger.info("   Top remaining warning types:")
                for warning, count in warnings:
                    # Truncate long warnings
                    warning_text = warning[:100] if warning else "Unknown"
                    logger.info(f"      {count:6d} files: {warning_text}...")
            else:
                logger.info("   ✅ No remaining warnings")
            
            logger.info("\n" + "=" * 50)
            logger.info("✅ HEVC warning fix completed!")
            logger.info("=" * 50)
            
            return True
                
    except Exception as e:
        logger.error(f"\n❌ Could not connect to database: {e}")
        return False

if __name__ == "__main__":
    success = fix_hevc_warnings()
    sys.exit(0 if success else 1)