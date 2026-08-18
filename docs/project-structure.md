# PixelProbe project structure

## Directory structure

```
PixelProbe/
+-- app.py                    # Flask WSGI entry point (Gunicorn: app:app)
+-- celery_worker.py          # Celery worker entry point
+-- openapi.yaml              # OpenAPI spec (served by app)
|
+-- pixelprobe/               # Main application package
|   +-- __init__.py
|   +-- auth.py               # Authentication (session + Bearer token)
|   +-- celery_config.py      # Celery setup with _make_context_task() factory
|   +-- config.py             # Config classes selected via FLASK_ENV
|   +-- constants.py          # Canonical extension lists, scan phase constants
|   +-- media_checker.py      # Core FFmpeg/ImageMagick/PIL validation logic
|   +-- models.py             # All SQLAlchemy models
|   +-- scheduler.py          # APScheduler for periodic/scheduled scans
|   +-- scheduler_lock.py     # Redis distributed lock for scheduler
|   +-- startup.py            # Startup cleanup routines
|   +-- version.py            # Single source of truth for version string
|   +-- tasks.py              # Celery task definitions
|   +-- tasks_parallel.py     # Parallel scan Celery tasks
|   +-- progress_utils.py     # Progress tracking utilities
|   |
|   +-- api/                  # Route blueprints
|   |   +-- admin_routes.py       # Configurations, schedules, exclusions, ignored patterns
|   |   +-- auth_decorator.py     # Authentication decorator
|   |   +-- auth_routes.py        # Login, logout, users, tokens
|   |   +-- export_routes.py      # CSV/data export, file viewer
|   |   +-- healthcheck_routes.py # Healthcheck integration
|   |   +-- log_routes.py          # Log viewing, filtering, download
|   |   +-- maintenance_routes.py # Cleanup, file-changes, vacuum
|   |   +-- notification_routes.py # Notification providers and rules
|   |   +-- reports_routes.py     # Scan reports, PDF generation
|   |   +-- scan_launch.py        # Shared scan-launch helper
|   |   +-- scan_routes.py        # Core scan operations
|   |   +-- scan_routes_parallel.py # Parallel scan endpoint
|   |   `-- stats_routes.py       # Statistics, trends, system info
|   |
|   +-- services/             # Business logic layer
|   |   +-- db_optimization.py
|   |   +-- export_service.py
|   |   +-- healthcheck_service.py
|   |   +-- maintenance_service.py
|   |   +-- notification_service.py
|   |   +-- scan_engine.py        # Chunk building, scan finalization
|   |   +-- scan_executor.py
|   |   +-- scan_reporting.py     # Scan reports, batch file inserts
|   |   +-- scan_service.py
|   |   `-- stats_service.py
|   |
|   +-- repositories/         # Data access layer
|   |   +-- base_repository.py
|   |   +-- config_repository.py
|   |   `-- scan_repository.py
|   |
|   +-- utils/                # Shared utilities
|   |   +-- celery_utils.py       # check_celery_available, safe_check_task_state
|   |   +-- decorators.py
|   |   +-- helpers.py            # ProgressTracker, batch_process, state utilities
|   |   +-- integrity.py          # File change classification (bitrot detection)
|   |   +-- paths.py              # Path prefix/containment helpers
|   |   +-- rate_limiting.py      # rate_limit, exempt_from_rate_limit
|   |   +-- log_context.py          # ContextVar-based scan/task log tagging
|   |   +-- log_handler.py          # Database log handler (background batch writer)
|   |   +-- security.py           # Path validation, SSRF protection, safe subprocess
|   |   +-- timezone.py           # Timezone conversion utilities
|   |   `-- validators.py         # Input validation helpers
|   |
|   `-- migrations/
|       `-- startup.py            # DB migrations run at startup
|
+-- static/                   # Frontend assets
|   +-- css/                  # Stylesheets (desktop, mobile, logo)
|   +-- js/                   # Webpack source (app.js, auth.js, state.js)
|   +-- images/
|   `-- dist/                 # Webpack build output (gitignored)
|
+-- templates/                # Jinja2 templates
|   +-- index.html
|   +-- login.html
|   `-- api_docs.html
|
+-- tests/                    # Test suite
|
+-- tools/                    # Maintenance and utility scripts
|
+-- scripts/                  # Release and dev helper scripts (Python, shell, SQL)
|
+-- docs/                     # Documentation (flat topic files + glossary.md)
|   +-- examples/             # Client examples (Python, Node.js, Bash)
|   +-- screenshots/          # UI screenshots
|   `-- ...
|
+-- Dockerfile                # Production Docker image (linux/amd64)
+-- docker-compose.yml        # Production Docker Compose stack
+-- gunicorn.conf.py          # Gunicorn worker/timeout config
+-- package.json              # Node.js dependencies (webpack build)
+-- webpack.config.js         # Webpack configuration
+-- requirements.txt          # Python production dependencies
+-- requirements-test.txt     # Python test dependencies
+-- pytest.ini                # Pytest configuration
+-- .env.example              # Environment variable template
+-- README.md                 # Project README
`-- CHANGELOG.md              # Version changelog
```

## Key architecture notes

- PostgreSQL only since v2.2.0 (no SQLite support)
- Celery handles all scan tasks via Redis broker
- APScheduler runs in the celery-worker container for scheduled scans
- Extension lists are canonical in `pixelprobe/constants.py`
- DB migrations run automatically at startup via `pixelprobe/migrations/startup.py`
- Multi-worker startup uses PostgreSQL advisory locks for migration coordination
- Root contains only entry points (`app.py`, `celery_worker.py`) and build/config files
- All application code lives inside the `pixelprobe/` package
