#!/usr/bin/env python3
"""
Reset files that were marked as completed but have incomplete scan data.

These are files that show 'N/A' for Tool Details and Scan Date in the UI
due to the v2.2.59 chunk query bug that prevented actual scanning.

Usage:
    python reset_incomplete_scans.py [--dry-run]
"""

import os
import sys
import argparse

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from pixelprobe.models import db, ScanResult
from sqlalchemy import or_

def reset_incomplete_scans(dry_run=False):
    """Find and reset files with incomplete scan data"""
    
    app = create_app()
    with app.app_context():
        # Find files marked as completed but missing scan details
        # OR files marked as healthy/not corrupted but never actually scanned
        from sqlalchemy import and_
        incomplete_files = ScanResult.query.filter(
            or_(
                # Case 1: Marked as completed but no scan data
                and_(
                    ScanResult.scan_status == 'completed',
                    or_(
                        ScanResult.scan_date.is_(None),
                        ScanResult.scan_output.is_(None),
                        ScanResult.scan_output == '',
                        ScanResult.scan_output == 'N/A'
                    )
                ),
                # Case 2: Marked as healthy (is_corrupted=False) but no scan date
                and_(
                    ScanResult.is_corrupted == False,
                    ScanResult.scan_date.is_(None)
                )
            )
        ).all()
        
        count = len(incomplete_files)
        
        if count == 0:
            print("No incomplete scans found.")
            return 0
        
        print(f"Found {count} files with incomplete scan data:")
        
        # Show first 10 examples
        for i, result in enumerate(incomplete_files[:10]):
            print(f"  - {result.file_path}")
            if i == 9 and count > 10:
                print(f"  ... and {count - 10} more")
        
        if dry_run:
            print("\nDRY RUN - No changes made")
            print(f"Would reset {count} files to 'pending' status")
            return count
        
        # Confirm before making changes
        response = input(f"\nReset {count} files to 'pending' status? (y/N): ")
        if response.lower() != 'y':
            print("Cancelled")
            return 0
        
        # Reset these files to pending
        for result in incomplete_files:
            result.scan_status = 'pending'
            result.is_corrupted = None  # Reset to unknown
            result.marked_as_good = False
            result.error_message = 'Reset due to incomplete scan data (v2.2.59 fix)'
            result.scan_output = None
            # Keep discovered_date as is
        
        db.session.commit()
        
        print(f"\nSuccessfully reset {count} files to 'pending' status")
        print("These files will be rescanned in the next scan operation")
        
        return count

def main():
    parser = argparse.ArgumentParser(description='Reset files with incomplete scan data')
    parser.add_argument('--dry-run', action='store_true', 
                        help='Show what would be reset without making changes')
    args = parser.parse_args()
    
    try:
        count = reset_incomplete_scans(dry_run=args.dry_run)
        sys.exit(0 if count >= 0 else 1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()