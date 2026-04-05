"""
Configuration module for PixelProbe
PostgreSQL-only database support (v2.2.0+)
"""
import os
import logging

logger = logging.getLogger(__name__)


class Config:
    """Base configuration - PostgreSQL only"""
    
    # Security
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("SECRET_KEY environment variable must be set")

    # Internal API secret for scheduler-to-app authentication
    # If not set, app.py will auto-generate one at startup
    INTERNAL_API_SECRET = os.environ.get('INTERNAL_API_SECRET', '')
    
    # PostgreSQL configuration
    POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'localhost')
    POSTGRES_PORT = os.getenv('POSTGRES_PORT', '5432')
    POSTGRES_DB = os.getenv('POSTGRES_DB', 'pixelprobe')
    POSTGRES_USER = os.getenv('POSTGRES_USER', 'pixelprobe')
    POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', '')
    
    # SQLAlchemy configuration
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # PostgreSQL optimized engine options
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'pool_pre_ping': True,
        'pool_recycle': 3600,
        'max_overflow': 40,
        'pool_timeout': 30,
        'echo': os.getenv('DATABASE_ECHO', 'false').lower() == 'true',
        'connect_args': {}  # Will be populated in init_app if needed
    }
    
    # Build basic PostgreSQL connection string (will be refined in init_app)
    if not POSTGRES_PASSWORD:
        logger.warning("POSTGRES_PASSWORD not set - using PostgreSQL without password")
        SQLALCHEMY_DATABASE_URI = (
            f"postgresql://{POSTGRES_USER}@"
            f"{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
        )
    else:
        # Default URL format - will be adjusted in init_app if password has special chars
        SQLALCHEMY_DATABASE_URI = (
            f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@"
            f"{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
        )
    
    logger.info(f"Using PostgreSQL database: {POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    
    # Application settings
    SCAN_PATHS = [p.strip() for p in os.getenv('SCAN_PATHS', '').split(',') if p.strip()]
    EXCLUDED_PATHS = os.getenv('EXCLUDED_PATHS', '').split(',') if os.getenv('EXCLUDED_PATHS') else []
    EXCLUDED_EXTENSIONS = os.getenv('EXCLUDED_EXTENSIONS', '.txt,.log,.md').split(',')
    
    # Performance settings
    # MAX_WORKERS: Controls ThreadPoolExecutor workers for parallel file corruption checking
    # This is different from Celery workers (controlled by docker-compose concurrency)
    # - MAX_WORKERS applies within each scan task for parallel file validation
    # - Celery workers handle distributed task processing across multiple containers
    MAX_WORKERS = int(os.getenv('MAX_WORKERS', '10'))
    BATCH_SIZE = int(os.getenv('BATCH_SIZE', '100'))
    MAX_OUTPUT_SIZE = int(os.getenv('MAX_OUTPUT_SIZE', '10000'))  # For output rotation
    OUTPUT_ROTATION_ENABLED = os.getenv('OUTPUT_ROTATION_ENABLED', 'true').lower() == 'true'
    FREEZE_DETECTION_ENABLED = os.getenv('FREEZE_DETECTION_ENABLED', 'true').lower() == 'true'
    
    # P1 Celery task queue configuration (now implemented)
    # Celery 5.x requires lowercase config keys - keep both for compatibility
    CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    broker_url = CELERY_BROKER_URL  # New style for Celery 5.x
    result_backend = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    task_serializer = 'json'
    result_serializer = 'json'
    accept_content = ['json']
    timezone = 'UTC'
    enable_utc = True
    
    # Monitoring
    ENABLE_MONITORING = os.getenv('ENABLE_MONITORING', 'false').lower() == 'true'
    METRICS_PORT = int(os.getenv('METRICS_PORT', '9090'))

    # SSRF trusted hosts -- hostnames and/or CIDR ranges that bypass private-IP blocking.
    # Read directly from env by security.py (works outside Flask app context too).
    # Example: "healthcheck.internal.local,192.168.5.0/24"
    TRUSTED_INTERNAL_HOSTS = os.getenv('TRUSTED_INTERNAL_HOSTS', '')

    # P2 Data Retention Configuration
    # Configurable retention periods for automated cleanup
    # Note: scan_output archival is DISABLED - keeps all scan_results data forever
    SCAN_OUTPUT_RETENTION_DAYS = int(os.getenv('SCAN_OUTPUT_RETENTION_DAYS', '30'))  # Not used - scan_output archival disabled
    REPORT_RETENTION_DAYS = int(os.getenv('REPORT_RETENTION_DAYS', '90'))
    SCAN_STATE_RETENTION_DAYS = int(os.getenv('SCAN_STATE_RETENTION_DAYS', '7'))

    @classmethod
    def init_app(cls, app):
        """Initialize application with configuration"""
        # Set all configuration values first
        for key in dir(cls):
            if key.isupper():
                app.config[key] = getattr(cls, key)
        
        # Handle PostgreSQL password - provide complete URI for PixelProbe compatibility
        if cls.POSTGRES_PASSWORD:
            from urllib.parse import quote_plus
            
            logger.info(f"Connecting to PostgreSQL at {cls.POSTGRES_HOST}:{cls.POSTGRES_PORT}/{cls.POSTGRES_DB} as user {cls.POSTGRES_USER}")
            
            # URL-encode the password to handle special characters safely
            encoded_password = quote_plus(cls.POSTGRES_PASSWORD)
            complete_uri = (
                f"postgresql://{cls.POSTGRES_USER}:{encoded_password}@"
                f"{cls.POSTGRES_HOST}:{cls.POSTGRES_PORT}/{cls.POSTGRES_DB}"
            )
            
            # Use the complete URI for both Flask and PixelProbe compatibility
            app.config['SQLALCHEMY_DATABASE_URI'] = complete_uri
            
            # Update engine options for Flask app optimizations
            engine_options = app.config['SQLALCHEMY_ENGINE_OPTIONS'].copy()
            engine_options['connect_args'] = {
                'connect_timeout': 10,
                'application_name': 'pixelprobe'
            }
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options
            logger.debug(f"Complete database URI configured for {cls.POSTGRES_USER}@{cls.POSTGRES_HOST}:{cls.POSTGRES_PORT}/{cls.POSTGRES_DB}")
        
        # Log configuration (without sensitive data)
        logger.info(f"Database Type: PostgreSQL")
        logger.info(f"Max Workers: {cls.MAX_WORKERS}")
        logger.info(f"Batch Size: {cls.BATCH_SIZE}")
        logger.info(f"Output Rotation: {'Enabled' if cls.OUTPUT_ROTATION_ENABLED else 'Disabled'}")


class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    TESTING = False
    
    # More verbose logging in development
    SQLALCHEMY_ENGINE_OPTIONS = Config.SQLALCHEMY_ENGINE_OPTIONS.copy()
    SQLALCHEMY_ENGINE_OPTIONS['echo'] = True


class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    TESTING = False
    
    @classmethod
    def init_app(cls, app):
        """Initialize production app with validation"""
        super().init_app(app)
        
        # Require password in production for PostgreSQL
        if not cls.POSTGRES_PASSWORD:
            raise ValueError("POSTGRES_PASSWORD must be set in production when using PostgreSQL")


class TestingConfig(Config):
    """Testing configuration"""
    TESTING = True
    DEBUG = True
    
    # Use test PostgreSQL database
    POSTGRES_DB = os.getenv('POSTGRES_TEST_DB', 'pixelprobe_test')
    
    # Build test database URI
    if not Config.POSTGRES_PASSWORD:
        SQLALCHEMY_DATABASE_URI = (
            f"postgresql://{Config.POSTGRES_USER}@"
            f"{Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/{POSTGRES_DB}"
        )
    else:
        SQLALCHEMY_DATABASE_URI = (
            f"postgresql://{Config.POSTGRES_USER}:{Config.POSTGRES_PASSWORD}@"
            f"{Config.POSTGRES_HOST}:{Config.POSTGRES_PORT}/{POSTGRES_DB}"
        )
    
    # Smaller pool for testing
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': False,
        'pool_size': 5,
        'max_overflow': 0,
        'pool_timeout': 10,
        'connect_args': {}  # Will be populated in init_app
    }
    
    # Disable output rotation in tests
    OUTPUT_ROTATION_ENABLED = False
    MAX_OUTPUT_SIZE = 1000
    
    @classmethod
    def init_app(cls, app):
        """Initialize testing app with configuration"""
        # First apply parent configuration
        super().init_app(app)
        
        # Override with testing database
        app.config['POSTGRES_DB'] = cls.POSTGRES_DB
        
        # Update connect_args for test database
        if cls.POSTGRES_PASSWORD:
            engine_options = app.config['SQLALCHEMY_ENGINE_OPTIONS'].copy()
            engine_options['connect_args'] = {
                'user': cls.POSTGRES_USER,
                'password': cls.POSTGRES_PASSWORD,
                'host': cls.POSTGRES_HOST,
                'port': int(cls.POSTGRES_PORT),
                'dbname': cls.POSTGRES_DB,  # Use test database
                'connect_timeout': 5,
                'application_name': 'pixelprobe_test'
            }
            app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options


# Configuration dictionary
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}


def get_config(config_name=None):
    """Get configuration class based on environment"""
    if config_name is None:
        config_name = os.getenv('FLASK_ENV', 'development')
    
    return config.get(config_name, DevelopmentConfig)