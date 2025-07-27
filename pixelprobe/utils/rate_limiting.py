"""Rate limiting configuration for the application"""
from flask import Flask
from flask_limiter import Limiter
import logging

logger = logging.getLogger(__name__)

def apply_rate_limits(app: Flask, limiter: Limiter):
    """Apply rate limits to blueprint endpoints after registration"""
    try:
        # Get blueprints
        scan_bp = app.blueprints.get('scan')
        admin_bp = app.blueprints.get('admin')
        maintenance_bp = app.blueprints.get('maintenance')
        
        if scan_bp:
            # Apply rate limits to scan endpoints
            if 'scan.scan_file' in app.view_functions:
                limiter.limit("5 per minute")(app.view_functions['scan.scan_file'])
            if 'scan.scan_all' in app.view_functions:
                limiter.limit("2 per minute")(app.view_functions['scan.scan_all'])
            if 'scan.scan_parallel' in app.view_functions:
                limiter.limit("2 per minute")(app.view_functions['scan.scan_parallel'])
        
        if admin_bp:
            # Apply rate limits to admin endpoints
            if 'admin.cleanup_files' in app.view_functions:
                limiter.limit("10 per minute")(app.view_functions['admin.cleanup_files'])
            if 'admin.mark_as_good' in app.view_functions:
                limiter.limit("10 per minute")(app.view_functions['admin.mark_as_good'])
        
        if maintenance_bp:
            # Apply rate limits to maintenance endpoints
            if 'maintenance.vacuum_database' in app.view_functions:
                limiter.limit("5 per minute")(app.view_functions['maintenance.vacuum_database'])
        
        logger.info("Rate limits applied successfully")
    except Exception as e:
        logger.error(f"Error applying rate limits: {e}")
        # Don't fail the app startup if rate limiting setup fails