#!/usr/bin/env python3
"""
Run database migration to add cancel_requested columns.
This script is designed to be run inside the Docker container.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.add_cancel_requested_columns import add_cancel_requested_columns

if __name__ == "__main__":
    print("Running database migration...")
    add_cancel_requested_columns()
    print("Migration complete!")