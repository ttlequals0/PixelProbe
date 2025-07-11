#!/usr/bin/env python3
"""
Add warning state columns to existing database.
Run this to add has_warnings and warning_details columns.
"""

import os
import sys
from sqlalchemy import create_engine, text
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def add_warning_columns(database_url=None):
    """Add warning columns to the database if they don't exist"""
    if not database_url:
        database_url = os.environ.get('DATABASE_URL', 'sqlite:///media_checker.db')
    
    engine = create_engine(database_url)
    
    with engine.connect() as conn:
        # Check if columns already exist
        try:
            result = conn.execute(text("SELECT has_warnings FROM scan_results LIMIT 1"))
            logger.info("Warning columns already exist")
            return
        except Exception:
            pass
        
        # Add the columns
        logger.info("Adding warning columns to database...")
        
        try:
            # Add has_warnings column
            conn.execute(text("""
                ALTER TABLE scan_results 
                ADD COLUMN has_warnings BOOLEAN DEFAULT 0 NOT NULL
            """))
            logger.info("Added has_warnings column")
            
            # Add warning_details column
            conn.execute(text("""
                ALTER TABLE scan_results 
                ADD COLUMN warning_details TEXT
            """))
            logger.info("Added warning_details column")
            
            # Create index on has_warnings
            conn.execute(text("""
                CREATE INDEX idx_scan_results_has_warnings 
                ON scan_results(has_warnings)
            """))
            logger.info("Created index on has_warnings")
            
            conn.commit()
            logger.info("Database schema updated successfully!")
            
        except Exception as e:
            logger.error(f"Error updating database: {e}")
            raise

def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Add warning state columns to MediaChecker database"
    )
    parser.add_argument(
        '--database-url',
        help='Database URL (defaults to sqlite:///media_checker.db or DATABASE_URL env var)'
    )
    
    args = parser.parse_args()
    add_warning_columns(args.database_url)

if __name__ == "__main__":
    main()