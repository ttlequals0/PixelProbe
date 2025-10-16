#!/usr/bin/env python3
"""
Migration to add authentication tables for v2.4.0
"""

import os
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from config import get_database_url
from models import db, User

logger = logging.getLogger(__name__)

def run_migration():
    """Add user authentication and API token tables"""

    database_url = get_database_url()
    engine = create_engine(database_url)

    try:
        with engine.connect() as conn:
            # Create users table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username VARCHAR(80) UNIQUE NOT NULL,
                    email VARCHAR(120) UNIQUE NOT NULL,
                    password_hash VARCHAR(128) NOT NULL,
                    is_admin BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP WITH TIME ZONE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    first_setup_required BOOLEAN NOT NULL DEFAULT FALSE
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)"))

            # Create API tokens table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token VARCHAR(64) UNIQUE NOT NULL,
                    description VARCHAR(200),
                    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP WITH TIME ZONE,
                    expires_at TIMESTAMP WITH TIME ZONE,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE
                )
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_tokens_token ON api_tokens(token)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id)"))

            # No longer create default admin user automatically
            # Users must use the /api/auth/setup endpoint on first run
            logger.info("Authentication tables created. Use /api/auth/setup to create initial admin user.")

            conn.commit()
            logger.info("Authentication tables created successfully")

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migration()
    print("Authentication tables migration completed successfully")