#!/usr/bin/env python3
"""Initialize database tables for PixelProbe v2.4.0"""

from app import app
from models import db

with app.app_context():
    # Create all tables
    db.create_all()
    print("Database tables created successfully")