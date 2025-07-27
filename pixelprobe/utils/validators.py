"""
Validators for PixelProbe
"""

import os
import re

def validate_file_path(file_path):
    """Validate a file path"""
    if not file_path:
        return False, "File path is required"
    
    if not isinstance(file_path, str):
        return False, "File path must be a string"
    
    # Check for path traversal attempts
    if '..' in file_path or file_path.startswith('~'):
        return False, "Invalid file path"
    
    return True, None

def validate_scan_config(config):
    """Validate scan configuration"""
    errors = []
    
    if 'directories' in config:
        if not isinstance(config['directories'], list):
            errors.append("Directories must be a list")
        else:
            for directory in config['directories']:
                if not os.path.isdir(directory):
                    errors.append(f"Directory does not exist: {directory}")
    
    if 'force_rescan' in config:
        if not isinstance(config['force_rescan'], bool):
            errors.append("force_rescan must be a boolean")
    
    if 'num_workers' in config:
        if not isinstance(config['num_workers'], int) or config['num_workers'] < 1:
            errors.append("num_workers must be a positive integer")
    
    return len(errors) == 0, errors

def validate_cron_expression(cron_expr):
    """Validate a cron expression"""
    # Simple validation - just check format
    parts = cron_expr.split()
    if len(parts) != 5:
        return False, "Cron expression must have 5 parts"
    
    # More detailed validation could be added here
    return True, None

def validate_export_format(format_type):
    """Validate export format"""
    valid_formats = ['csv', 'json', 'xml']
    if format_type not in valid_formats:
        return False, f"Invalid format. Must be one of: {', '.join(valid_formats)}"
    return True, None