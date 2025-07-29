"""Version information for PixelProbe"""
import os

# Default version - this is the single source of truth
_DEFAULT_VERSION = '2.1.20'

# Allow override via environment variable for CI/CD, but default to the hardcoded version
__version__ = os.environ.get('APP_VERSION', _DEFAULT_VERSION)
__github_url__ = "https://github.com/ttlequals0/PixelProbe"