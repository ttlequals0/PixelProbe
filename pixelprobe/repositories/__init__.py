"""
Repository layer for PixelProbe
"""

from .base_repository import BaseRepository
from .scan_repository import ScanRepository
from .config_repository import (
    ConfigurationRepository, 
    IgnoredPatternRepository,
    ScheduleRepository
)

# For backward compatibility
ConfigRepository = ConfigurationRepository

__all__ = [
    'BaseRepository',
    'ScanRepository',
    'ConfigRepository',
    'ConfigurationRepository',
    'IgnoredPatternRepository',
    'ScheduleRepository'
]