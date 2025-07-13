#!/usr/bin/env python3
"""
Create a test database with sample data for UI testing
"""

import os
import sys
from datetime import datetime, timedelta, timezone
import random
from pathlib import Path

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import ScanResult, ScanState, ScanConfiguration

# Test database path
TEST_DB_PATH = os.path.abspath("test_media_checker.db")

def create_test_database():
    """Create a test database with sample data"""
    
    # Remove existing test database if it exists
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
        print(f"Removed existing test database")
    
    # Update app config to use test database
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{TEST_DB_PATH}'
    
    with app.app_context():
        # Create all tables
        db.create_all()
        print("✅ Created database tables")
        
        # Sample file paths
        media_paths = [
            "/media/photos/vacation/IMG_001.jpg",
            "/media/photos/vacation/IMG_002.jpg",
            "/media/photos/vacation/IMG_003.jpg",
            "/media/photos/family/DSC_0001.jpg",
            "/media/photos/family/DSC_0002.jpg",
            "/media/videos/birthday/video_001.mp4",
            "/media/videos/birthday/video_002.mp4",
            "/media/videos/holiday/christmas_2023.mkv",
            "/media/videos/holiday/newyear_2024.mkv",
            "/media/photos/nature/sunset_001.png",
            "/media/photos/nature/mountain_view.png",
            "/media/videos/travel/paris_trip.avi",
            "/media/videos/travel/tokyo_visit.mov",
            "/media/photos/events/wedding_001.jpg",
            "/media/photos/events/wedding_002.jpg",
            "/media/photos/events/graduation.jpg",
            "/media/videos/concerts/rock_concert.mp4",
            "/media/videos/concerts/jazz_night.mp4",
            "/media/photos/art/painting_001.jpg",
            "/media/photos/art/sculpture_002.jpg",
        ]
        
        # Corruption details samples
        corruption_details = [
            "Invalid JPEG marker",
            "Truncated file - missing end marker",
            "Invalid video codec parameters",
            "Corrupted frame at 00:01:23",
            "Invalid image header",
            "Missing MOOV atom in video file",
            "CRC error in frame data",
            "Invalid EXIF data",
            "Corrupted audio stream",
            "Invalid PNG chunk",
        ]
        
        warning_details = [
            "[mpeg4 @ 0x7f8b8c008c00] Video uses a non-standard and wasteful way to store B-frames",
            "ImageMagick Warning: Corrupt JPEG data: premature end of data segment",
            "[h264 @ 0x7f8b8c008c00] Reference picture missing during reorder",
            "PIL Warning: Possibly corrupt EXIF data",
        ]
        
        # Create sample scan results
        scan_results = []
        base_date = datetime.now(timezone.utc) - timedelta(days=30)
        
        for i, path in enumerate(media_paths):
            file_ext = Path(path).suffix.lower()
            file_type = 'image' if file_ext in ['.jpg', '.jpeg', '.png', '.gif'] else 'video'
            
            # Randomize file status
            rand = random.random()
            if rand < 0.7:  # 70% healthy
                is_corrupted = False
                has_warnings = False
                corruption_detail = None
                scan_tool = "ffmpeg" if file_type == 'video' else "PIL"
            elif rand < 0.85:  # 15% warnings
                is_corrupted = False
                has_warnings = True
                corruption_detail = random.choice(warning_details)
                scan_tool = "ffmpeg" if file_type == 'video' else "imagemagick"
            else:  # 15% corrupted
                is_corrupted = True
                has_warnings = False
                corruption_detail = random.choice(corruption_details)
                scan_tool = "ffmpeg" if file_type == 'video' else "imagemagick"
            
            # Random file size between 100KB and 50MB
            file_size = random.randint(100 * 1024, 50 * 1024 * 1024)
            
            # Random scan date within last 30 days
            scan_date = base_date + timedelta(
                days=random.randint(0, 30),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59)
            )
            
            result = ScanResult(
                file_path=path,
                file_size=file_size,
                file_type=file_type,
                is_corrupted=is_corrupted,
                has_warnings=has_warnings,
                warning_details=corruption_detail if has_warnings else None,
                corruption_details=corruption_detail if is_corrupted else None,
                scan_date=scan_date,
                scan_status='completed',
                scan_tool=scan_tool,
                scan_duration=random.uniform(0.1, 5.0),
                discovered_date=scan_date - timedelta(hours=1),
                marked_as_good=False,
                file_hash=f"hash_{i:04d}",
                last_modified=scan_date - timedelta(days=random.randint(1, 365))
            )
            scan_results.append(result)
        
        # Add some pending and scanning files
        for i in range(5):
            path = f"/media/photos/pending/IMG_{i:03d}.jpg"
            result = ScanResult(
                file_path=path,
                file_size=random.randint(100 * 1024, 10 * 1024 * 1024),
                file_type='image',
                scan_status='pending',
                discovered_date=datetime.now(timezone.utc),
                last_modified=datetime.now(timezone.utc) - timedelta(days=random.randint(1, 100))
            )
            scan_results.append(result)
        
        # Add to database
        db.session.add_all(scan_results)
        db.session.commit()
        
        print(f"✅ Added {len(scan_results)} sample scan results")
        
        # Add scan configuration
        config = ScanConfiguration(
            key='scan_paths',
            value='/media/photos,/media/videos'
        )
        db.session.add(config)
        db.session.commit()
        
        print("✅ Added scan configuration")
        
        # Get statistics
        total = db.session.query(ScanResult).count()
        corrupted = db.session.query(ScanResult).filter_by(is_corrupted=True).count()
        warnings = db.session.query(ScanResult).filter_by(has_warnings=True).count()
        healthy = db.session.query(ScanResult).filter(
            ScanResult.is_corrupted == False,
            (ScanResult.has_warnings == False) | (ScanResult.has_warnings == None)
        ).count()
        pending = db.session.query(ScanResult).filter_by(scan_status='pending').count()
        
        print("\n📊 Test Database Statistics:")
        print(f"   Total Files: {total}")
        print(f"   Healthy: {healthy}")
        print(f"   Corrupted: {corrupted}")
        print(f"   Warnings: {warnings}")
        print(f"   Pending: {pending}")
        
        print(f"\n✅ Test database created: {TEST_DB_PATH}")
        if os.path.exists(TEST_DB_PATH):
            print(f"   Size: {os.path.getsize(TEST_DB_PATH) / 1024:.2f} KB")
        else:
            print("   WARNING: Database file not found!")

if __name__ == "__main__":
    print("=== Creating Test Database ===")
    print()
    create_test_database()
    print("\nTo use this database, update your DATABASE_URL to:")
    print(f"   sqlite:///{os.path.abspath(TEST_DB_PATH)}")