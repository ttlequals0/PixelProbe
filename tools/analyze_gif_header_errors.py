#!/usr/bin/env python3
"""
Analyze GIF files marked as corrupted due to "improper image header" errors.
This script helps identify false positives where GIFs work fine but are marked corrupted.
"""

import os
import sys
import sqlite3
import argparse
import logging
from pathlib import Path
import subprocess
import tempfile

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_gif_file(file_path):
    """Test if a GIF file is actually valid using multiple methods"""
    results = {
        'exists': False,
        'file_size': 0,
        'file_type': None,
        'ffmpeg_valid': False,
        'python_readable': False,
        'opens_in_viewer': None
    }
    
    try:
        # Check if file exists
        if os.path.exists(file_path):
            results['exists'] = True
            results['file_size'] = os.path.getsize(file_path)
            
            # Check file type using 'file' command
            try:
                file_output = subprocess.run(['file', '-b', file_path], 
                                           capture_output=True, text=True, timeout=5)
                results['file_type'] = file_output.stdout.strip()
            except:
                pass
            
            # Test with FFmpeg
            try:
                ffmpeg_cmd = ['ffmpeg', '-v', 'error', '-i', file_path, '-f', 'null', '-']
                ffmpeg_result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=10)
                results['ffmpeg_valid'] = ffmpeg_result.returncode == 0
                if ffmpeg_result.stderr:
                    results['ffmpeg_error'] = ffmpeg_result.stderr.strip()
            except:
                pass
            
            # Try to read with Python
            try:
                from PIL import Image
                with Image.open(file_path) as img:
                    results['python_readable'] = True
                    results['format'] = img.format
                    results['size'] = img.size
                    results['n_frames'] = getattr(img, 'n_frames', 1)
            except Exception as e:
                results['python_error'] = str(e)
    
    except Exception as e:
        logger.error(f"Error testing {file_path}: {str(e)}")
    
    return results

def analyze_gif_errors(db_path, limit=10):
    """Analyze GIF files with improper header errors"""
    
    conn = None
    try:
        # Connect to database
        conn = sqlite3.connect(db_path, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000")
        
        # Find GIF files with improper header errors
        logger.info("Finding GIF files with 'improper image header' errors...")
        cursor = conn.execute("""
            SELECT id, file_path, corruption_details, scan_output, file_size
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND file_path LIKE '%.gif'
            AND (
                corruption_details LIKE '%improper image header%ReadGIFImage%'
                OR scan_output LIKE '%improper image header%ReadGIFImage%'
            )
            LIMIT ?
        """, (limit,))
        
        files = cursor.fetchall()
        logger.info(f"Found {len(files)} GIF files with improper header errors (showing first {limit})")
        
        if len(files) == 0:
            logger.info("No GIF files found with this specific error")
            return
        
        # Analyze each file
        false_positives = []
        true_corrupted = []
        
        for file_id, file_path, corruption_details, scan_output, file_size in files:
            logger.info(f"\nAnalyzing: {file_path}")
            logger.info(f"  File size: {file_size} bytes")
            logger.info(f"  Error: {corruption_details[:200] if corruption_details else 'No details'}")
            
            # Test the file
            test_results = test_gif_file(file_path)
            
            if test_results['exists']:
                logger.info(f"  File type: {test_results.get('file_type', 'Unknown')}")
                logger.info(f"  FFmpeg valid: {test_results['ffmpeg_valid']}")
                logger.info(f"  Python readable: {test_results['python_readable']}")
                
                if test_results.get('format'):
                    logger.info(f"  Format: {test_results['format']}, Size: {test_results['size']}, Frames: {test_results.get('n_frames', 1)}")
                
                # Determine if it's a false positive
                if test_results['ffmpeg_valid'] or test_results['python_readable']:
                    false_positives.append((file_id, file_path))
                    logger.info("  ✓ LIKELY FALSE POSITIVE - File appears to be valid")
                else:
                    true_corrupted.append((file_id, file_path))
                    logger.info("  ✗ LIKELY CORRUPTED - File validation failed")
            else:
                logger.info("  File does not exist")
        
        # Summary
        logger.info(f"\n=== SUMMARY ===")
        logger.info(f"Total analyzed: {len(files)}")
        logger.info(f"Likely false positives: {len(false_positives)}")
        logger.info(f"Likely corrupted: {len(true_corrupted)}")
        
        if false_positives:
            logger.info("\nFalse positive candidates:")
            for file_id, file_path in false_positives[:5]:
                logger.info(f"  ID: {file_id}, Path: {file_path}")
        
        # Check for common patterns
        logger.info("\n=== PATTERN ANALYSIS ===")
        
        # Get all GIF improper header errors for pattern analysis
        cursor = conn.execute("""
            SELECT COUNT(*), scan_output
            FROM scan_results 
            WHERE is_corrupted = 1 
            AND file_path LIKE '%.gif'
            AND (
                corruption_details LIKE '%improper image header%ReadGIFImage%'
                OR scan_output LIKE '%improper image header%ReadGIFImage%'
            )
            GROUP BY scan_output
            ORDER BY COUNT(*) DESC
            LIMIT 5
        """)
        
        patterns = cursor.fetchall()
        logger.info("Most common error patterns:")
        for count, pattern in patterns:
            if pattern:
                logger.info(f"  {count} files: {pattern[:100]}...")
        
        return false_positives
            
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
    parser = argparse.ArgumentParser(description='Analyze GIF files with improper header errors')
    parser.add_argument('--db-path', type=str, 
                       default='/app/instance/media_checker.db',
                       help='Path to the database file')
    parser.add_argument('--limit', type=int, default=10,
                       help='Number of files to analyze in detail')
    args = parser.parse_args()
    
    # Check if database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error(f"Database not found at {db_path}")
        sys.exit(1)
    
    logger.info(f"Using database: {db_path}")
    analyze_gif_errors(str(db_path), args.limit)

if __name__ == '__main__':
    main()