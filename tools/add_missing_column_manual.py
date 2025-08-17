#!/usr/bin/env python3
"""
Manual script to add the missing output_rotation_enabled column to production database
Run this directly on the production server
"""
import os
import psycopg2
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_missing_column():
    """Add the output_rotation_enabled column if it doesn't exist"""
    
    # Get database connection info from environment
    pg_config = {
        'host': os.getenv('POSTGRES_HOST', 'postgres'),
        'port': os.getenv('POSTGRES_PORT', '5432'),
        'database': os.getenv('POSTGRES_DB', 'pixelprobe'),
        'user': os.getenv('POSTGRES_USER', 'pixelprobe'),
        'password': os.getenv('POSTGRES_PASSWORD', '')
    }
    
    try:
        # Connect to PostgreSQL
        logger.info(f"Connecting to PostgreSQL at {pg_config['host']}:{pg_config['port']}/{pg_config['database']} as user {pg_config['user']}")
        conn = psycopg2.connect(
            host=pg_config['host'],
            port=pg_config['port'],
            database=pg_config['database'],
            user=pg_config['user'],
            password=pg_config['password']
        )
        cursor = conn.cursor()
        
        # Check if column exists
        logger.info("Checking if output_rotation_enabled column exists...")
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'scan_results' 
            AND column_name = 'output_rotation_enabled'
        """)
        
        if cursor.fetchone() is None:
            logger.info("Column does not exist. Adding output_rotation_enabled column...")
            cursor.execute("""
                ALTER TABLE scan_results 
                ADD COLUMN output_rotation_enabled BOOLEAN
            """)
            conn.commit()
            logger.info("✅ Column added successfully!")
        else:
            logger.info("✅ Column output_rotation_enabled already exists")
        
        # Verify the column was added
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = 'scan_results' 
            AND column_name = 'output_rotation_enabled'
        """)
        
        if cursor.fetchone():
            logger.info("✅ Verification successful: output_rotation_enabled column exists")
        else:
            logger.error("❌ Verification failed: column still doesn't exist")
        
        cursor.close()
        conn.close()
        logger.info("Database connection closed")
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        raise

if __name__ == '__main__':
    add_missing_column()