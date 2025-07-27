#!/usr/bin/env python3
"""
Database Schema Fix Tool for PixelProbe

This tool repairs database initialization issues that can occur during version upgrades
or when the database tables weren't properly created on container startup.

Usage:
    # Inside container:
    python tools/fix_database_schema.py
    
    # Outside container (if environment is set up):
    SECRET_KEY=your-key python tools/fix_database_schema.py
    
    # Docker exec (one-liner):
    docker exec pixelprobe python tools/fix_database_schema.py

This script will:
1. Initialize missing database tables
2. Run necessary migrations  
3. Create performance indexes
4. Verify database accessibility
"""

import os
import sys
import logging
from pathlib import Path

# Add the app directory to Python path for imports
app_dir = Path(__file__).parent.parent
sys.path.insert(0, str(app_dir))

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fix_database():
    """Fix database schema and initialization issues"""
    try:
        # Ensure SECRET_KEY is set (required by Flask app)
        if not os.environ.get('SECRET_KEY'):
            # Try to use a temporary key for database operations
            temp_key = os.environ.get('TEMP_SECRET_KEY', 'temp-database-fix-key-change-in-production')
            os.environ['SECRET_KEY'] = temp_key
            logger.info("Using temporary SECRET_KEY for database repair")
        
        logger.info("🔧 PixelProbe Database Schema Fix Tool")
        logger.info("=" * 50)
        
        # Import after setting environment variables
        from models import db, ScanResult, IgnoredErrorPattern, ScanSchedule, ScanConfiguration
        from app import app
        
        logger.info(f"Database URL: {app.config.get('SQLALCHEMY_DATABASE_URI', 'NOT SET')}")
        
        # Create application context
        with app.app_context():
            logger.info("📋 Creating database tables...")
            
            # Create all tables
            db.create_all()
            logger.info("✅ Database tables created/verified successfully")
            
            # Test table access
            test_tables = [
                (ScanResult, 'scan_results'),
                (IgnoredErrorPattern, 'ignored_error_patterns'),
                (ScanSchedule, 'scan_schedules'),
                (ScanConfiguration, 'scan_configurations')
            ]
            
            logger.info("🧪 Testing table accessibility...")
            all_accessible = True
            
            for model_class, table_name in test_tables:
                try:
                    count = model_class.query.count()
                    logger.info(f"✅ {table_name}: {count:,} records")
                except Exception as e:
                    logger.error(f"❌ {table_name}: {e}")
                    all_accessible = False
            
            if not all_accessible:
                logger.error("❌ Some tables are not accessible. Manual intervention may be required.")
                return False
            
            # Run migrations (from app.py)
            try:
                from app import migrate_database, create_performance_indexes
                
                logger.info("🔄 Running database migrations...")
                migrate_database()
                logger.info("✅ Database migrations completed")
                
                logger.info("⚡ Creating performance indexes...")
                create_performance_indexes()
                logger.info("✅ Performance indexes created")
                
            except Exception as e:
                logger.warning(f"⚠️ Migration/Index creation warning: {e}")
                # Don't fail the entire operation for migration warnings
            
            # Final verification
            scan_count = ScanResult.query.count()
            logger.info(f"🎯 Final verification: {scan_count:,} scan results accessible")
            
            logger.info("=" * 50)
            logger.info("🎉 Database schema fix completed successfully!")
            logger.info("✅ Your PixelProbe installation should now work normally")
            
            return True
            
    except ImportError as e:
        logger.error(f"❌ Import error: {e}")
        logger.error("Make sure you're running this from the PixelProbe app directory")
        return False
    except Exception as e:
        logger.error(f"❌ Database fix failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def print_usage():
    """Print usage instructions"""
    print(__doc__)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ['--help', '-h']:
        print_usage()
        sys.exit(0)
    
    success = fix_database()
    sys.exit(0 if success else 1)