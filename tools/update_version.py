#!/usr/bin/env python3
"""
Script to update the version number across all version-dependent files.
Updates: version.py, openapi.yaml

Usage: python update_version.py 2.5.17
"""
import sys
import re
import os

# Get the project root directory (parent of tools/)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def update_version_py(new_version):
    """Update the version number in version.py"""
    version_file = os.path.join(PROJECT_ROOT, 'version.py')

    with open(version_file, 'r') as f:
        content = f.read()

    # Update the _DEFAULT_VERSION line
    pattern = r"_DEFAULT_VERSION = '[^']+'"
    replacement = f"_DEFAULT_VERSION = '{new_version}'"

    new_content = re.sub(pattern, replacement, content)

    if new_content == content:
        print("Error: Could not find version string in version.py")
        return False

    with open(version_file, 'w') as f:
        f.write(new_content)

    print(f"Updated version.py to {new_version}")
    return True


def update_openapi_yaml(new_version):
    """Update the version number in openapi.yaml"""
    openapi_file = os.path.join(PROJECT_ROOT, 'openapi.yaml')

    if not os.path.exists(openapi_file):
        print("Warning: openapi.yaml not found, skipping")
        return True

    with open(openapi_file, 'r') as f:
        content = f.read()

    # Update the version line in the info section (line 4 typically)
    # Match "  version: X.Y.Z" pattern
    pattern = r"(info:\s*\n\s*title:[^\n]*\n\s*)version:\s*[\d.]+"
    replacement = rf"\1version: {new_version}"

    new_content = re.sub(pattern, replacement, content)

    if new_content == content:
        # Try alternative pattern for direct version line
        pattern2 = r"(\s+version:\s*)[\d.]+"
        new_content = re.sub(pattern2, rf"\g<1>{new_version}", content, count=1)

    if new_content == content:
        print("Warning: Could not find version string in openapi.yaml")
        return True  # Non-fatal

    with open(openapi_file, 'w') as f:
        f.write(new_content)

    print(f"Updated openapi.yaml to {new_version}")
    return True


def update_version(new_version):
    """Update version across all files"""
    success = True

    # Update version.py (required)
    if not update_version_py(new_version):
        success = False

    # Update openapi.yaml (optional but recommended)
    update_openapi_yaml(new_version)

    return success


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python update_version.py <new_version>")
        print("Example: python update_version.py 2.5.17")
        print("\nThis script updates:")
        print("  - version.py (single source of truth)")
        print("  - openapi.yaml (for static file access)")
        sys.exit(1)

    new_version = sys.argv[1]

    # Basic version format validation
    if not re.match(r'^\d+\.\d+\.\d+$', new_version):
        print(f"Error: Invalid version format '{new_version}'. Use format like '2.5.17'")
        sys.exit(1)

    if update_version(new_version):
        print(f"\nVersion updated to {new_version}")
        print("Don't forget to update CHANGELOG.md!")
    else:
        sys.exit(1)
