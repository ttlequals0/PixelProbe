#!/usr/bin/env python3
"""
Simple script to update the version number in version.py
Usage: python update_version.py 2.0.95
"""
import sys
import re

def update_version(new_version):
    """Update the version number in version.py"""
    with open('version.py', 'r') as f:
        content = f.read()
    
    # Update the _DEFAULT_VERSION line
    pattern = r"_DEFAULT_VERSION = '[^']+'"
    replacement = f"_DEFAULT_VERSION = '{new_version}'"
    
    new_content = re.sub(pattern, replacement, content)
    
    if new_content == content:
        print(f"Error: Could not find version string to update")
        return False
    
    with open('version.py', 'w') as f:
        f.write(new_content)
    
    print(f"Updated version to {new_version}")
    return True

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python update_version.py <new_version>")
        print("Example: python update_version.py 2.0.95")
        sys.exit(1)
    
    new_version = sys.argv[1]
    
    # Basic version format validation
    if not re.match(r'^\d+\.\d+\.\d+$', new_version):
        print(f"Error: Invalid version format '{new_version}'. Use format like '2.0.95'")
        sys.exit(1)
    
    if update_version(new_version):
        print(f"Don't forget to update CHANGELOG.MD!")
    else:
        sys.exit(1)