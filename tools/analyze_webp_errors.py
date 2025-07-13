#!/usr/bin/env python3
"""
Analyze WebP files marked as corrupted to identify false positives.
"""

import os
import sys
import sqlite3
import argparse
import logging
from pathlib import Path
import subprocess

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def analyze_webp_errors(db_path, limit=20):
    """Analyze WebP files marked as corrupted"""
    
    conn = None
    try:
        # Connect to database
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        
        # Find WebP files marked as corrupted
        logger.info("Finding WebP files marked as corrupted...")
        cursor = conn.execute("""
            SELECT id, file_path, corruption_details, scan_output, file_size, scan_tool
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND file_path LIKE '%.webp'
            ORDER BY scan_date DESC
            LIMIT ?
        """, (limit,))
        
        files = cursor.fetchall()
        logger.info(f"Found {len(files)} WebP files marked as corrupted (showing first {limit})")
        
        if len(files) == 0:
            logger.info("No corrupted WebP files found")
            return
        
        # Analyze error patterns
        error_patterns = {}
        exif_errors = 0
        ffmpeg_errors = 0
        
        for file_id, file_path, corruption_details, scan_output, file_size, scan_tool in files:
            logger.info(f"\nFile: {file_path}")
            logger.info(f"  Size: {file_size} bytes")
            logger.info(f"  Tool: {scan_tool}")
            logger.info(f"  Error: {corruption_details[:200] if corruption_details else 'No details'}")
            
            # Count error patterns
            if corruption_details:
                details_lower = corruption_details.lower()
                if 'invalid tiff header' in details_lower or 'exif' in details_lower:
                    exif_errors += 1
                if 'ffmpeg' in details_lower:
                    ffmpeg_errors += 1
                    
                # Extract specific error messages
                if scan_output:
                    for line in scan_output.split('\n'):
                        if 'error' in line.lower() or 'failed' in line.lower():
                            error_msg = line.strip()[:100]
                            error_patterns[error_msg] = error_patterns.get(error_msg, 0) + 1
        
        # Summary
        logger.info(f"\n=== SUMMARY ===")
        logger.info(f"Total corrupted WebP files analyzed: {len(files)}")
        logger.info(f"Files with EXIF/TIFF header errors: {exif_errors}")
        logger.info(f"Files with FFmpeg errors: {ffmpeg_errors}")
        
        if error_patterns:
            logger.info("\n=== COMMON ERROR PATTERNS ===")
            for pattern, count in sorted(error_patterns.items(), key=lambda x: x[1], reverse=True)[:10]:
                logger.info(f"  {count}x: {pattern}")
        
        # Get more detailed patterns
        logger.info("\n=== DETAILED PATTERN ANALYSIS ===")
        cursor = conn.execute("""
            SELECT 
                CASE 
                    WHEN corruption_details LIKE '%invalid TIFF header%' THEN 'Invalid TIFF/EXIF header'
                    WHEN corruption_details LIKE '%FFmpeg image validation failed%' THEN 'FFmpeg validation failed'
                    WHEN corruption_details LIKE '%ImageMagick%' THEN 'ImageMagick error'
                    WHEN corruption_details LIKE '%PIL%' THEN 'PIL error'
                    ELSE 'Other error'
                END as error_type,
                COUNT(*) as count
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND file_path LIKE '%.webp'
            GROUP BY error_type
            ORDER BY count DESC
        """)
        
        patterns = cursor.fetchall()
        for error_type, count in patterns:
            logger.info(f"  {error_type}: {count} files")
            
    except sqlite3.OperationalError as e:
        if "database is locked" in str(e):
            logger.error("Database is locked. Please stop any running scans and try again.")
        else:
            logger.error(f"Database error: {str(e)}")
    except Exception as e:
        logger.error(f"Error analyzing files: {str(e)}")
    finally:
        if conn:
            conn.close()

def main():
    parser = argparse.ArgumentParser(description='Analyze WebP files marked as corrupted')
    parser.add_argument('--db-path', type=str, 
                       default='/app/instance/media_checker.db',
                       help='Path to the database file')
    parser.add_argument('--limit', type=int, default=20,
                       help='Number of files to analyze')
    args = parser.parse_args()
    
    # Check if database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        sys.exit(1)
    
    logger.info(f"Using database: {db_path}")
    analyze_webp_errors(str(db_path), args.limit)

if __name__ == '__main__':
    main()