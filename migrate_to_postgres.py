#!/usr/bin/env python3
"""
Migration script from SQLite to PostgreSQL
Handles database schema creation and data migration
"""
import os
import sys
import sqlite3
import psycopg2
from psycopg2.extras import execute_values
import logging
from datetime import datetime
import json
import argparse
from typing import Dict, List, Any, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class PostgreSQLMigrator:
    """Handles migration from SQLite to PostgreSQL"""
    
    def __init__(self, sqlite_path: str, pg_config: Dict[str, Any]):
        self.sqlite_path = sqlite_path
        self.pg_config = pg_config
        self.sqlite_conn = None
        self.pg_conn = None
        self.table_mappings = self._get_table_mappings()
    
    def _get_table_mappings(self) -> Dict[str, Dict[str, str]]:
        """Define column mappings between SQLite and PostgreSQL"""
        return {
            'scan_results': {
                'id': 'INTEGER PRIMARY KEY',
                'file_path': 'VARCHAR(500) NOT NULL UNIQUE',
                'file_size': 'BIGINT',
                'file_type': 'VARCHAR(50)',
                'creation_date': 'TIMESTAMP',
                'is_corrupted': 'BOOLEAN',
                'corruption_details': 'TEXT',
                'scan_date': 'TIMESTAMP',
                'marked_as_good': 'BOOLEAN DEFAULT FALSE',
                'scan_status': 'VARCHAR(20) DEFAULT \'pending\'',
                'discovered_date': 'TIMESTAMP',
                'file_hash': 'VARCHAR(64)',
                'last_modified': 'TIMESTAMP',
                'scan_tool': 'VARCHAR(50)',
                'scan_duration': 'FLOAT',
                'scan_output': 'TEXT',
                'has_warnings': 'BOOLEAN DEFAULT FALSE',
                'warning_details': 'TEXT',
                'deep_scan': 'BOOLEAN DEFAULT FALSE',
                'error_message': 'TEXT',
                'media_info': 'TEXT',
                'file_exists': 'BOOLEAN DEFAULT TRUE'
            },
            'ignored_error_patterns': {
                'id': 'INTEGER PRIMARY KEY',
                'pattern': 'VARCHAR(200) NOT NULL UNIQUE',
                'description': 'VARCHAR(500)',
                'created_at': 'TIMESTAMP WITH TIME ZONE',
                'created_date': 'TIMESTAMP',
                'is_active': 'BOOLEAN DEFAULT TRUE'
            },
            'exclusions': {
                'id': 'INTEGER PRIMARY KEY',
                'exclusion_type': 'VARCHAR(20) NOT NULL',
                'value': 'VARCHAR(500) NOT NULL',
                'created_at': 'TIMESTAMP WITH TIME ZONE',
                'description': 'TEXT',
                'is_active': 'BOOLEAN DEFAULT TRUE'
            },
            'scan_schedules': {
                'id': 'INTEGER PRIMARY KEY',
                'schedule_name': 'VARCHAR(100)',  # Allow NULL for backward compatibility
                'scan_type': 'VARCHAR(50) NOT NULL',
                'cron_expression': 'VARCHAR(100)',
                'enabled': 'BOOLEAN DEFAULT TRUE',
                'last_run': 'TIMESTAMP',
                'next_run': 'TIMESTAMP',
                'created_at': 'TIMESTAMP WITH TIME ZONE',
                'updated_at': 'TIMESTAMP WITH TIME ZONE',
                'scan_paths': 'TEXT',
                'options': 'TEXT'
            },
            'scan_chunks': {
                'chunk_id': 'VARCHAR(100) PRIMARY KEY',
                'scan_id': 'VARCHAR(50)',
                'directory_path': 'TEXT',
                'file_paths': 'TEXT',
                'chunk_index': 'INTEGER',
                'total_chunks': 'INTEGER',
                'processed': 'BOOLEAN DEFAULT FALSE',
                'created_at': 'TIMESTAMP',
                'processed_at': 'TIMESTAMP'
            },
            'scan_state': {
                'id': 'INTEGER PRIMARY KEY',
                'scan_id': 'VARCHAR(50) UNIQUE',
                'scan_type': 'VARCHAR(50)',
                'status': 'VARCHAR(50)',
                'progress': 'FLOAT',
                'total_files': 'INTEGER',
                'scanned_files': 'INTEGER',
                'corrupted_files': 'INTEGER',
                'start_time': 'TIMESTAMP',
                'end_time': 'TIMESTAMP',
                'error_message': 'TEXT',
                'scan_paths': 'TEXT'
            },
            'scan_configurations': {
                'id': 'INTEGER PRIMARY KEY',
                'config_key': 'VARCHAR(100) UNIQUE NOT NULL',
                'config_value': 'TEXT',
                'description': 'TEXT',
                'created_at': 'TIMESTAMP WITH TIME ZONE',
                'updated_at': 'TIMESTAMP WITH TIME ZONE'
            },
            'scan_reports': {
                'id': 'INTEGER PRIMARY KEY',
                'report_id': 'VARCHAR(50) UNIQUE NOT NULL',
                'scan_id': 'VARCHAR(50)',
                'report_type': 'VARCHAR(50)',
                'file_path': 'VARCHAR(500)',
                'created_at': 'TIMESTAMP',
                'metadata': 'TEXT'
            },
            'cleanup_state': {
                'id': 'INTEGER PRIMARY KEY',
                'operation_id': 'VARCHAR(50) UNIQUE',
                'status': 'VARCHAR(50)',
                'total_orphaned': 'INTEGER',
                'processed': 'INTEGER',
                'deleted': 'INTEGER',
                'start_time': 'TIMESTAMP',
                'end_time': 'TIMESTAMP',
                'error_message': 'TEXT'
            },
            'file_changes_state': {
                'id': 'INTEGER PRIMARY KEY',
                'check_id': 'VARCHAR(50) UNIQUE',
                'status': 'VARCHAR(50)',
                'total_files': 'INTEGER',
                'files_checked': 'INTEGER',
                'changes_detected': 'INTEGER',
                'start_time': 'TIMESTAMP',
                'end_time': 'TIMESTAMP',
                'error_message': 'TEXT'
            }
        }
    
    def connect(self):
        """Establish connections to both databases"""
        try:
            # Connect to SQLite
            self.sqlite_conn = sqlite3.connect(self.sqlite_path)
            self.sqlite_conn.row_factory = sqlite3.Row
            logger.info(f"Connected to SQLite database: {self.sqlite_path}")
            
            # Connect to PostgreSQL
            # Use connection parameters directly to avoid URL encoding issues
            self.pg_conn = psycopg2.connect(
                host=self.pg_config['host'],
                port=self.pg_config['port'],
                database=self.pg_config['database'],
                user=self.pg_config['user'],
                password=self.pg_config['password']
            )
            self.pg_conn.autocommit = False
            logger.info(f"Connected to PostgreSQL database: {self.pg_config['database']}")
            
        except Exception as e:
            logger.error(f"Failed to connect to databases: {e}")
            raise
    
    def create_postgres_schema(self):
        """Create PostgreSQL schema with proper indexes"""
        cursor = self.pg_conn.cursor()
        
        try:
            # Create tables
            for table_name, columns in self.table_mappings.items():
                # Build CREATE TABLE statement
                col_definitions = []
                for col_name, col_type in columns.items():
                    if col_name == 'id' and 'PRIMARY KEY' in col_type:
                        col_definitions.append(f"{col_name} SERIAL PRIMARY KEY")
                    else:
                        col_definitions.append(f"{col_name} {col_type}")
                
                create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(col_definitions)})"
                cursor.execute(create_sql)
                logger.info(f"Created table: {table_name}")
            
            # Create indexes for better performance
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_scan_results_file_path ON scan_results(file_path)",
                "CREATE INDEX IF NOT EXISTS idx_scan_results_scan_status ON scan_results(scan_status)",
                "CREATE INDEX IF NOT EXISTS idx_scan_results_is_corrupted ON scan_results(is_corrupted)",
                "CREATE INDEX IF NOT EXISTS idx_scan_results_scan_date ON scan_results(scan_date)",
                "CREATE INDEX IF NOT EXISTS idx_scan_results_discovered_date ON scan_results(discovered_date)",
                "CREATE INDEX IF NOT EXISTS idx_scan_results_file_hash ON scan_results(file_hash)",
                "CREATE INDEX IF NOT EXISTS idx_scan_results_marked_as_good ON scan_results(marked_as_good)",
                "CREATE INDEX IF NOT EXISTS idx_scan_chunks_scan_id ON scan_chunks(scan_id)",
                "CREATE INDEX IF NOT EXISTS idx_scan_chunks_processed ON scan_chunks(processed)",
                "CREATE INDEX IF NOT EXISTS idx_scan_state_scan_id ON scan_state(scan_id)",
                "CREATE INDEX IF NOT EXISTS idx_scan_state_status ON scan_state(status)"
            ]
            
            for index_sql in indexes:
                cursor.execute(index_sql)
                logger.info(f"Created index: {index_sql.split('idx_')[1].split(' ')[0]}")
            
            self.pg_conn.commit()
            logger.info("PostgreSQL schema created successfully")
            
        except Exception as e:
            self.pg_conn.rollback()
            logger.error(f"Failed to create PostgreSQL schema: {e}")
            raise
    
    def migrate_table(self, table_name: str, batch_size: int = 1000):
        """Migrate a single table from SQLite to PostgreSQL"""
        sqlite_cursor = self.sqlite_conn.cursor()
        pg_cursor = self.pg_conn.cursor()
        
        try:
            # Get total count
            sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            total_rows = sqlite_cursor.fetchone()[0]
            logger.info(f"Migrating {total_rows} rows from table: {table_name}")
            
            if total_rows == 0:
                logger.info(f"No data to migrate in table: {table_name}")
                return
            
            # Get column names from SQLite
            sqlite_cursor.execute(f"PRAGMA table_info({table_name})")
            sqlite_columns = [row[1] for row in sqlite_cursor.fetchall()]
            
            # Filter columns that exist in our mapping
            columns_to_migrate = [col for col in sqlite_columns if col in self.table_mappings[table_name]]
            
            # Migrate in batches
            offset = 0
            migrated = 0
            
            while offset < total_rows:
                # Fetch batch from SQLite
                sqlite_cursor.execute(
                    f"SELECT {', '.join(columns_to_migrate)} FROM {table_name} LIMIT ? OFFSET ?",
                    (batch_size, offset)
                )
                rows = sqlite_cursor.fetchall()
                
                if not rows:
                    break
                
                # Prepare data for PostgreSQL
                values = []
                for row in rows:
                    row_values = []
                    for i, col in enumerate(columns_to_migrate):
                        value = row[i]
                        # Convert SQLite boolean (0/1) to PostgreSQL boolean
                        if isinstance(value, int) and col in ['is_corrupted', 'marked_as_good', 
                                                              'has_warnings', 'deep_scan', 
                                                              'file_exists', 'is_active', 
                                                              'enabled', 'processed']:
                            value = bool(value)
                        # Handle NULL values
                        elif value is None:
                            value = None
                        # Handle datetime strings
                        elif col in ['creation_date', 'scan_date', 'discovered_date', 
                                   'last_modified', 'created_at', 'created_date', 
                                   'last_run', 'next_run', 'start_time', 'end_time',
                                   'processed_at', 'updated_at']:
                            if value and isinstance(value, str):
                                try:
                                    # Parse and format datetime
                                    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
                                    value = dt
                                except:
                                    # Keep as string if parsing fails
                                    pass
                        row_values.append(value)
                    values.append(tuple(row_values))
                
                # Insert into PostgreSQL
                if table_name == 'scan_results' and 'id' not in columns_to_migrate:
                    # For scan_results, let PostgreSQL generate the ID
                    insert_cols = [col for col in columns_to_migrate if col != 'id']
                    placeholders = ','.join(['%s'] * len(insert_cols))
                    insert_sql = f"""
                        INSERT INTO {table_name} ({', '.join(insert_cols)})
                        VALUES %s
                        ON CONFLICT (file_path) DO NOTHING
                    """
                else:
                    placeholders = ','.join(['%s'] * len(columns_to_migrate))
                    insert_sql = f"""
                        INSERT INTO {table_name} ({', '.join(columns_to_migrate)})
                        VALUES %s
                        ON CONFLICT DO NOTHING
                    """
                
                execute_values(pg_cursor, insert_sql, values, page_size=batch_size)
                
                migrated += len(rows)
                offset += batch_size
                
                # Progress report
                progress = (migrated / total_rows) * 100
                logger.info(f"  Progress: {migrated}/{total_rows} rows ({progress:.1f}%)")
            
            self.pg_conn.commit()
            logger.info(f"Successfully migrated {migrated} rows from table: {table_name}")
            
        except Exception as e:
            self.pg_conn.rollback()
            logger.error(f"Failed to migrate table {table_name}: {e}")
            raise
    
    def verify_migration(self):
        """Verify data integrity after migration"""
        sqlite_cursor = self.sqlite_conn.cursor()
        pg_cursor = self.pg_conn.cursor()
        
        logger.info("Verifying migration integrity...")
        
        for table_name in self.table_mappings.keys():
            # Get counts from both databases
            sqlite_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            sqlite_count = sqlite_cursor.fetchone()[0]
            
            pg_cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            pg_count = pg_cursor.fetchone()[0]
            
            if sqlite_count == pg_count:
                logger.info(f"✓ Table {table_name}: {pg_count} rows (matches)")
            else:
                logger.warning(f"✗ Table {table_name}: SQLite={sqlite_count}, PostgreSQL={pg_count} (mismatch)")
        
        logger.info("Migration verification complete")
    
    def update_sequences(self):
        """Update PostgreSQL sequences to match the maximum ID values"""
        pg_cursor = self.pg_conn.cursor()
        
        try:
            for table_name in self.table_mappings.keys():
                if 'id' in self.table_mappings[table_name]:
                    # Get max ID from the table
                    pg_cursor.execute(f"SELECT MAX(id) FROM {table_name}")
                    max_id = pg_cursor.fetchone()[0]
                    
                    if max_id:
                        # Update the sequence
                        sequence_name = f"{table_name}_id_seq"
                        pg_cursor.execute(f"SELECT setval('{sequence_name}', %s)", (max_id,))
                        logger.info(f"Updated sequence {sequence_name} to {max_id}")
            
            self.pg_conn.commit()
            
        except Exception as e:
            self.pg_conn.rollback()
            logger.error(f"Failed to update sequences: {e}")
            raise
    
    def close(self):
        """Close database connections"""
        if self.sqlite_conn:
            self.sqlite_conn.close()
        if self.pg_conn:
            self.pg_conn.close()
        logger.info("Database connections closed")
    
    def migrate(self):
        """Execute full migration"""
        try:
            self.connect()
            self.create_postgres_schema()
            
            # Migrate each table
            for table_name in self.table_mappings.keys():
                self.migrate_table(table_name)
            
            self.update_sequences()
            self.verify_migration()
            
            logger.info("Migration completed successfully!")
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            raise
        finally:
            self.close()


def main():
    """Main migration function"""
    parser = argparse.ArgumentParser(description='Migrate PixelProbe from SQLite to PostgreSQL')
    parser.add_argument('--sqlite-path', default='instance/pixelprobe.db',
                       help='Path to SQLite database')
    parser.add_argument('--pg-host', default=os.getenv('POSTGRES_HOST', 'localhost'),
                       help='PostgreSQL host')
    parser.add_argument('--pg-port', default=os.getenv('POSTGRES_PORT', '5432'),
                       help='PostgreSQL port')
    parser.add_argument('--pg-database', default=os.getenv('POSTGRES_DB', 'pixelprobe'),
                       help='PostgreSQL database name')
    parser.add_argument('--pg-user', default=os.getenv('POSTGRES_USER', 'pixelprobe'),
                       help='PostgreSQL user')
    parser.add_argument('--pg-password', default=os.getenv('POSTGRES_PASSWORD', ''),
                       help='PostgreSQL password')
    parser.add_argument('--batch-size', type=int, default=1000,
                       help='Batch size for migration')
    parser.add_argument('--dry-run', action='store_true',
                       help='Perform a dry run without actual migration')
    
    args = parser.parse_args()
    
    # Verify SQLite database exists
    if not os.path.exists(args.sqlite_path):
        logger.error(f"SQLite database not found: {args.sqlite_path}")
        sys.exit(1)
    
    # PostgreSQL configuration
    pg_config = {
        'host': args.pg_host,
        'port': args.pg_port,
        'database': args.pg_database,
        'user': args.pg_user,
        'password': args.pg_password
    }
    
    if args.dry_run:
        logger.info("DRY RUN MODE - No actual migration will be performed")
        logger.info(f"SQLite database: {args.sqlite_path}")
        logger.info(f"PostgreSQL target: {pg_config['host']}:{pg_config['port']}/{pg_config['database']}")
        
        # Test connections only
        migrator = PostgreSQLMigrator(args.sqlite_path, pg_config)
        migrator.connect()
        migrator.close()
        logger.info("Connection test successful")
    else:
        # Perform actual migration
        logger.info("Starting database migration...")
        migrator = PostgreSQLMigrator(args.sqlite_path, pg_config)
        migrator.migrate()


if __name__ == '__main__':
    main()