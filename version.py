"""Version information for PixelProbe"""
import os

# Get version from environment variable (set during Docker build) or use default
__version__ = os.environ.get('APP_VERSION', '2.0.73')
__github_url__ = "https://github.com/ttlequals0/PixelProbe"