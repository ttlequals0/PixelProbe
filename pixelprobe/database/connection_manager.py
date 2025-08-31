"""
Database connection management utilities for PixelProbe
Handles connection pooling, recovery, and transaction management
"""

import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, event, pool
from sqlalchemy.exc import DBAPIError, OperationalError, InvalidRequestError
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import NullPool, QueuePool

logger = logging.getLogger(__name__)


class DatabaseConnectionManager:
    """Manages database connections with automatic recovery and pooling"""
    
    def __init__(self, database_uri, app=None):
        self.database_uri = database_uri
        self.app = app
        self._engine = None
        self._session_factory = None
        self._scoped_session = None
        
    def init_app(self, app):
        """Initialize with Flask app"""
        self.app = app
        self.setup_engine()
        
    def setup_engine(self):
        """Create database engine with proper pooling and recovery"""
        # Determine if we're using PostgreSQL or SQLite
        is_postgres = 'postgresql' in self.database_uri or 'postgres' in self.database_uri
        
        if is_postgres:
            # PostgreSQL with connection pooling
            self._engine = create_engine(
                self.database_uri,
                poolclass=QueuePool,
                pool_size=10,
                max_overflow=20,
                pool_timeout=30,
                pool_recycle=3600,  # Recycle connections after 1 hour
                pool_pre_ping=True,  # Test connections before using
                echo=False,
                connect_args={
                    'connect_timeout': 10,
                    'options': '-c statement_timeout=300000'  # 5 minute statement timeout
                }
            )
        else:
            # SQLite with simpler configuration
            self._engine = create_engine(
                self.database_uri,
                poolclass=NullPool,  # No pooling for SQLite
                echo=False
            )
        
        # Add event listeners for connection recovery
        @event.listens_for(self._engine, "connect")
        def receive_connect(dbapi_conn, connection_record):
            """Configure connection on checkout"""
            if is_postgres:
                # Set PostgreSQL specific options
                with dbapi_conn.cursor() as cursor:
                    cursor.execute("SET TIME ZONE 'UTC'")
                    cursor.execute("SET statement_timeout = '5min'")
                    
        @event.listens_for(self._engine, "checkout")
        def receive_checkout(dbapi_conn, connection_record, connection_proxy):
            """Test connection on checkout"""
            if is_postgres:
                try:
                    # Test with a simple query
                    with dbapi_conn.cursor() as cursor:
                        cursor.execute("SELECT 1")
                except Exception:
                    # Connection is bad, raise DisconnectionError to trigger reconnect
                    raise pool.exc.DisconnectionError()
        
        # Create session factory
        self._session_factory = sessionmaker(bind=self._engine)
        self._scoped_session = scoped_session(self._session_factory)
        
    @contextmanager
    def get_session(self):
        """Get a database session with automatic cleanup and recovery"""
        session = self._scoped_session()
        try:
            yield session
            session.commit()
        except OperationalError as e:
            session.rollback()
            if "lost synchronization" in str(e) or "server closed the connection" in str(e):
                # Connection is corrupted, remove it
                logger.warning("Database connection lost, removing from pool")
                session.close()
                self._scoped_session.remove()
                # Get a new session
                session = self._scoped_session()
                yield session
                session.commit()
            else:
                raise
        except InvalidRequestError as e:
            session.rollback()
            if "transaction is aborted" in str(e):
                # Transaction aborted, rollback and retry
                logger.warning("Transaction aborted, rolling back")
                session.rollback()
                yield session
                session.commit()
            else:
                raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._scoped_session.remove()
    
    def execute_with_retry(self, func, max_retries=3):
        """Execute a database operation with automatic retry on connection errors"""
        for attempt in range(max_retries):
            try:
                with self.get_session() as session:
                    return func(session)
            except (OperationalError, DBAPIError) as e:
                if attempt == max_retries - 1:
                    raise
                logger.warning(f"Database operation failed (attempt {attempt + 1}/{max_retries}): {e}")
                if "lost synchronization" in str(e):
                    # Force new connection
                    self._engine.dispose()
                    self.setup_engine()
        
    def dispose(self):
        """Dispose of all connections"""
        if self._engine:
            self._engine.dispose()
            
    def get_count_safe(self, query):
        """Safely get count from a query, handling connection issues"""
        try:
            # Use a fresh session for count queries
            with self.get_session() as session:
                # For SQLAlchemy queries, use .count() properly
                if hasattr(query, 'count'):
                    return query.with_session(session).count()
                else:
                    # For raw SQL, ensure proper execution
                    result = session.execute(query)
                    return result.scalar()
        except Exception as e:
            logger.error(f"Count query failed: {e}")
            return 0


# Global connection manager instance
db_connection_manager = None


def init_connection_manager(database_uri):
    """Initialize the global connection manager"""
    global db_connection_manager
    db_connection_manager = DatabaseConnectionManager(database_uri)
    return db_connection_manager


def get_connection_manager():
    """Get the global connection manager"""
    return db_connection_manager