"""
Base repository class for data access layer
"""

from typing import TypeVar, Generic, List, Optional, Dict, Any
from sqlalchemy.orm import Query
from models import db

T = TypeVar('T')

class BaseRepository(Generic[T]):
    """Base repository with common database operations"""
    
    def __init__(self, model_class: type):
        self.model_class = model_class
        self.session = db.session
    
    def get_by_id(self, id: int) -> Optional[T]:
        """Get a single record by ID"""
        return self.session.query(self.model_class).get(id)
    
    def get_all(self) -> List[T]:
        """Get all records"""
        return self.session.query(self.model_class).all()
    
    def get_by_filter(self, **kwargs) -> List[T]:
        """Get records by filter criteria"""
        return self.session.query(self.model_class).filter_by(**kwargs).all()
    
    def get_one_by_filter(self, **kwargs) -> Optional[T]:
        """Get single record by filter criteria"""
        return self.session.query(self.model_class).filter_by(**kwargs).first()
    
    def create(self, **kwargs) -> T:
        """Create a new record"""
        instance = self.model_class(**kwargs)
        self.session.add(instance)
        self.session.commit()
        return instance
    
    def update(self, id: int, **kwargs) -> Optional[T]:
        """Update a record by ID"""
        instance = self.get_by_id(id)
        if instance:
            for key, value in kwargs.items():
                setattr(instance, key, value)
            self.session.commit()
        return instance
    
    def delete(self, id: int) -> bool:
        """Delete a record by ID"""
        instance = self.get_by_id(id)
        if instance:
            self.session.delete(instance)
            self.session.commit()
            return True
        return False
    
    def bulk_create(self, instances: List[T]) -> List[T]:
        """Create multiple records"""
        self.session.bulk_save_objects(instances)
        self.session.commit()
        return instances
    
    def bulk_update(self, mappings: List[Dict[str, Any]]) -> None:
        """Update multiple records"""
        self.session.bulk_update_mappings(self.model_class, mappings)
        self.session.commit()
    
    def count(self, **kwargs) -> int:
        """Count records with optional filters"""
        query = self.session.query(self.model_class)
        if kwargs:
            query = query.filter_by(**kwargs)
        return query.count()
    
    def exists(self, **kwargs) -> bool:
        """Check if record exists"""
        return self.count(**kwargs) > 0
    
    def query(self) -> Query:
        """Get base query for custom operations"""
        return self.session.query(self.model_class)
    
    def commit(self) -> None:
        """Commit current transaction"""
        self.session.commit()
    
    def rollback(self) -> None:
        """Rollback current transaction"""
        self.session.rollback()
    
    def refresh(self, instance: T) -> None:
        """Refresh instance from database"""
        self.session.refresh(instance)