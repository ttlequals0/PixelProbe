"""
Repository for configuration-related database operations
"""

from typing import List, Optional
from datetime import datetime, timezone

from models import ScanConfiguration, IgnoredErrorPattern, ScanSchedule
from .base_repository import BaseRepository

class ConfigurationRepository(BaseRepository[ScanConfiguration]):
    """Repository for scan configurations"""
    
    def __init__(self):
        super().__init__(ScanConfiguration)
    
    def get_active_paths(self) -> List[str]:
        """Get all active scan paths"""
        configs = self.get_by_filter(is_active=True)
        return [config.path for config in configs]
    
    def add_path(self, path: str) -> ScanConfiguration:
        """Add a new scan path or reactivate existing"""
        existing = self.get_one_by_filter(path=path)
        
        if existing:
            existing.is_active = True
            self.commit()
            return existing
        else:
            return self.create(
                path=path,
                is_active=True,
                created_at=datetime.now(timezone.utc)
            )
    
    def deactivate_path(self, path: str) -> bool:
        """Deactivate a scan path"""
        config = self.get_one_by_filter(path=path)
        if config:
            config.is_active = False
            self.commit()
            return True
        return False


class IgnoredPatternRepository(BaseRepository[IgnoredErrorPattern]):
    """Repository for ignored error patterns"""
    
    def __init__(self):
        super().__init__(IgnoredErrorPattern)
    
    def get_active_patterns(self) -> List[IgnoredErrorPattern]:
        """Get all active ignored patterns"""
        return self.get_by_filter(is_active=True)
    
    def add_pattern(self, pattern: str, description: str = '') -> IgnoredErrorPattern:
        """Add a new ignored pattern"""
        return self.create(
            pattern=pattern,
            description=description,
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
    
    def deactivate_pattern(self, pattern_id: int) -> bool:
        """Soft delete a pattern"""
        pattern = self.get_by_id(pattern_id)
        if pattern:
            pattern.is_active = False
            self.commit()
            return True
        return False
    
    def pattern_exists(self, pattern: str) -> bool:
        """Check if pattern already exists"""
        return self.exists(pattern=pattern, is_active=True)


class ScheduleRepository(BaseRepository[ScanSchedule]):
    """Repository for scan schedules"""
    
    def __init__(self):
        super().__init__(ScanSchedule)
    
    def get_active_schedules(self) -> List[ScanSchedule]:
        """Get all active schedules"""
        return self.get_by_filter(is_active=True)
    
    def create_schedule(self, name: str, cron_expression: str, 
                       scan_type: str = 'full', 
                       force_rescan: bool = False) -> ScanSchedule:
        """Create a new scan schedule"""
        return self.create(
            name=name,
            cron_expression=cron_expression,
            scan_type=scan_type,
            force_rescan=force_rescan,
            is_active=True,
            created_at=datetime.now(timezone.utc)
        )
    
    def update_schedule(self, schedule_id: int, **kwargs) -> Optional[ScanSchedule]:
        """Update a schedule"""
        schedule = self.get_by_id(schedule_id)
        if schedule:
            for key, value in kwargs.items():
                if hasattr(schedule, key):
                    setattr(schedule, key, value)
            self.commit()
        return schedule
    
    def deactivate_schedule(self, schedule_id: int) -> bool:
        """Soft delete a schedule"""
        schedule = self.get_by_id(schedule_id)
        if schedule:
            schedule.is_active = False
            self.commit()
            return True
        return False
    
    def get_schedule_by_name(self, name: str) -> Optional[ScanSchedule]:
        """Get schedule by name"""
        return self.get_one_by_filter(name=name, is_active=True)