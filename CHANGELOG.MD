# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **🎨 PIXELPROBE LOGO INTEGRATION**: Added new PixelProbe logo and favicon
  - Created favicon files (16x16, 32x32, ICO format) from the PixelProbe logo
  - Updated Flask routes to serve the new favicon files
  - Added logo image to main header section of web interface
  - Maintained fallback to dynamically generated favicons if files are missing
  - Logo positioned above the main title with responsive design and drop shadow effects

### Fixed
- **🔧 DATABASE LOCKING AND SCAN STATE ISSUES**: Fixed SQLite database locking and scan state management
  - Added SQLAlchemy engine options for better SQLite handling (timeout, connection pooling)
  - Fixed issue where 1M+ files were stuck in "scanning" state after database lock error
  - Added `/api/reset-stuck-scans` endpoint to reset files stuck in scanning status
  - Added `/api/reset-for-rescan` endpoint with options to reset files for rescanning:
    - `reset_type: "unscanned"` - Reset files that were never actually scanned
    - `reset_type: "errors"` - Reset files with errors
    - `reset_type: "all"` - Reset all files for complete rescan
  - Optimized `/api/stats` endpoint with single SQL query to reduce database load
  - Removed upfront marking of all files as "scanning" to prevent mass stuck states
  - Implemented batch processing (50 files per commit during scan, 1000 files per batch for resets)
  - Added proper error handling and rollback for database operations
  - Fixed UI showing incorrect statistics (0 pending files when 1M+ were stuck)

## [1.0] - 2025-07-08

### MAJOR RELEASE - PUBLIC REPOSITORY PREPARATION

### Added
- **📸 COMPREHENSIVE DOCUMENTATION**: Added complete UI screenshots demonstrating all features
  - Light mode and dark mode interface examples
  - Filtering and search functionality examples
  - System statistics modal and detailed views
  - Media viewer and scan output examples
- **🐳 DOCKER HUB RELEASE**: Published version 1.0 and latest tags to Docker Hub (ttlequals0/pixelprobe)
- **📋 DOCKER DOCUMENTATION**: Added comprehensive Docker version information and deployment examples
- **📋 ENHANCED README**: Complete rewrite with modern formatting, emojis, and comprehensive feature descriptions
- **🔧 IMPROVED CONFIGURATION**: Enhanced .env.example with all available configuration options
- **🔒 SECURITY HARDENING**: Comprehensive security review and sensitive data removal

### Changed
- **🏷️ VERSION BUMP**: Updated application version from 0.43 to 1.0 for stable public release
- **🌍 PUBLIC READY**: Repository prepared for public GitHub release with proper documentation
- **🔐 SECRET MANAGEMENT**: Changed default secret key to placeholder requiring production update
- **📁 REPOSITORY CLEANUP**: Removed all test data, logs, and sensitive information

### Security
- **🛡️ SENSITIVE DATA REMOVAL**: Comprehensive scan and removal of any sensitive information
- **🚫 ENHANCED GITIGNORE**: Updated .gitignore to prevent future sensitive data commits
- **🔑 SECRET KEY PLACEHOLDER**: Replaced development secret with secure placeholder
- **🧹 FILE CLEANUP**: Removed database files, logs, and any personal data from repository

### Documentation
- **📖 COMPLETE REWRITE**: Modern README with proper formatting and comprehensive usage examples
- **🖼️ VISUAL EXAMPLES**: Added 6 screenshots showcasing all major UI features and modes
- **🚀 QUICK START**: Enhanced quick start guide with step-by-step instructions
- **⚙️ CONFIGURATION**: Detailed configuration examples for various deployment scenarios
- **🐳 DOCKER DEPLOYMENT**: Updated Docker documentation with proper image references

### Technical
- **🏗️ BUILD OPTIMIZATION**: Docker images built for linux/amd64 platform
- **🧼 CLEANUP**: Removed all local test artifacts and temporary files
- **📝 CHANGELOG**: Updated with complete release history and proper versioning

## [0.45] - 2025-07-08

### Changed
- **MARKED GOOD DISPLAY**: Removed row highlighting for files marked as good
- **CORRUPTION DETAILS**: Files marked as good now show "✓ Marked as Good" in the Corruption Details column instead of row highlighting
- **CLEANER UI**: Eliminated distracting row backgrounds for better visual consistency

### Improved
- **VISUAL CLARITY**: Marked files are now indicated inline rather than with row backgrounds
- **USER EXPERIENCE**: Cleaner table appearance with consistent row styling

## [0.44] - 2025-07-08

### Added
- **PAGINATION OPTIONS**: Added per-page selection (20/50/100/500/All items per page)
- **RESULTS INDICATOR**: Added results count showing "Showing X-Y of Z results"
- **DEBOUNCING**: Added debouncing to prevent multiple rapid rescan clicks
- **FAVICON SUPPORT**: Added favicon routes to prevent 404 errors and provide proper branding
- **COMPREHENSIVE VISUAL FEEDBACK**: Added loading states and status messages for ALL clickable buttons:
  - Download button: Shows "Downloading..." with disabled state
  - View button: Shows "Loading..." when opening media viewer
  - Mark Good (single & bulk): Shows "Marking..." with visual feedback
  - Deep Scan: Shows "Deep Scanning..." with progress indication
  - Rescan Selected: Shows "Rescanning..." with debouncing
  - Cleanup Orphaned: Shows "Cleaning..." with extended feedback
  - Check File Changes: Shows "Checking..." with loading state
  - System Stats: Shows "Loading..." when fetching statistics
  - Refresh: Shows "Refreshing..." with temporary disable
  - Search/Filter: Shows contextual feedback messages

### Fixed
- **RESCAN FUNCTIONALITY**: Fixed rescan selected button causing multiple simultaneous requests
- **USER FEEDBACK**: Added comprehensive visual feedback and status messages for all operations
- **BUTTON STATES**: All buttons now properly show loading states and prevent multiple clicks

### Changed
- **DOWNLOAD BUTTON**: Changed download button color from green (#2ecc71) to orange (#e67e22) for better distinction
- **PAGINATION UI**: Enhanced pagination with better layout and responsive design
- **USER EXPERIENCE**: Dramatically improved user experience with clear feedback for every action

## [0.43] - 2025-07-08

### Fixed
- **RESCAN BUTTON**: Fixed rescan selected button not being clickable in dropdown
- **STATUS DISPLAY**: Files marked as good now show "Healthy" status instead of "Marked Good"
- **BUTTON COLORS**: Download button now green (#2ecc71) to distinguish from Deep Scan purple (#9b59b6)

### Changed
- **UI CONSISTENCY**: Improved visual distinction between action buttons
- **STATUS CLARITY**: Simplified status labels for better user understanding

## [0.42] - 2025-07-08

### Fixed
- **PATH COUNTING**: Removed filesystem scanning that caused performance issues
- **DATABASE CONSISTENCY**: Path counts now show ALL database entries regardless of scan status
- **PERFORMANCE**: Eliminated filesystem I/O from system-info endpoint

### Changed
- **PATH DISPLAY**: Monitored paths now show count of all discovered files in database
- **SYSTEM INFO**: Faster loading by using database-only queries

## [0.41] - 2025-07-08

### Fixed
- **RESCAN BUTTON**: Fixed rescan button not being selectable due to duplicate function definition
- **HEALTHY FILES COUNT**: Files marked as good now properly count as healthy in statistics
- **PERCENTAGE CALCULATION**: Fixed NaN% error in "Percentage Checked" stat
- **MOUNTED PATHS**: Show filesystem file counts even for unscanned paths
- **STATS ACCURACY**: Improved calculation accuracy for file statistics

### Changed
- **FILE COUNTING**: Paths now show database counts with filesystem fallback for empty paths

## [0.40] - 2025-07-08

### Changed
- **REBRANDING**: Updated all references from MediaChecker to PixelProbe
- **DOCKER IMAGE**: Changed image name from mediachecker to pixelprobe
- **DOCUMENTATION**: Updated README and all documentation with new branding

### Added
- **OPTIMIZED SYSTEM INFO**: System info endpoint now reads from database instead of scanning filesystem
- **IMPROVED PERFORMANCE**: Eliminated expensive filesystem walking that was causing timeouts

### Changed
- **ACTIONS DROPDOWN MOVED**: Moved Actions dropdown from top controls to be next to "Select All" button
- **MONITORED PATHS DISPLAY**: Updated to show file counts from database with error handling

### Fixed
- **SYSTEM STATS LOADING**: Fixed system stats not populating properly without page refresh
- **ORPHAN CLEANUP**: Fixed 400 Bad Request error by adding proper JSON body to cleanup requests
- **STATS COLLECTION**: Fixed monitored paths showing as empty and missing file system stats

## [0.39] - 2025-07-08

### Added
- **ACTION DROPDOWN**: Consolidated bulk actions (Mark Good, Deep Scan, Rescan) into a dropdown menu
- **TIMEOUT MECHANISM**: Added 30-second timeout for system-info endpoint to prevent hanging
- **RESCAN FUNCTIONALITY**: Implemented missing rescan selected files functionality
- **IMPROVED UI LAYOUT**: Reorganized button layout with System Stats moved to header

### Changed
- **SYSTEM STATS BUTTON**: Moved from action buttons to top-left of header next to Dark Mode toggle
- **BUTTON REORGANIZATION**: Consolidated Mark Good, Deep Scan, and Rescan into Actions dropdown
- **RESULTS CONTROLS**: Moved Select All and Export CSV buttons to scan results section
- **TIMEOUT HANDLING**: Added graceful handling of system-info timeouts with fallback

### Fixed
- **SYSTEM STATS HANGING**: Fixed system-info endpoint hanging on large directories with timeout mechanism
- **DROPDOWN FUNCTIONALITY**: Added proper dropdown toggle, outside click handling, and state management
- **BUTTON STATES**: Improved button state management for dropdown items based on selection

### Technical
- **TIMEOUT CONFIGURATION**: Added SYSTEM_INFO_TIMEOUT environment variable (default: 30 seconds)
- **SIGNAL HANDLING**: Implemented timeout context manager using SIGALRM for path scanning
- **ERROR HANDLING**: Enhanced error handling for system-info endpoint with fallback responses
- **DOCKER**: Built and pushed to Docker Hub as ttlequals0/pixelprobe:0.39

## [0.38] - 2025-07-08

### Added
- **NEW API ENDPOINT**: Added `/api/system-info` endpoint for comprehensive system information
- **FILESYSTEM MONITORING**: Real-time file counts directly from filesystem for all monitored paths
- **PATH DETAILS**: Detailed information about each monitored path including existence, file counts, and file types
- **ENHANCED METRICS**: Added scan performance metrics including average scan time and scan duration tracking
- **SYSTEM OVERVIEW**: Comprehensive system status including current scan state, configuration, and database statistics
- **STATS MODAL**: Added new "System Stats" button and modal popup in UI showing detailed system information
- **UI ENHANCEMENT**: Modal displays database statistics, monitored paths, file counts, and check percentages
- **PARALLEL PATH SCANNING**: Implemented parallel processing for multiple scan paths with dedicated workers per path
- **PARALLEL FILE DISCOVERY**: Enhanced file discovery to process multiple paths concurrently using ThreadPoolExecutor
- **OPTIMIZED SCANNING**: Files in each path are processed from oldest to newest with path-specific worker pools
- **SCAN TOOL FIX**: Fixed missing scan_tool and scan_output information for unsupported file types

### Enhanced
- **API EXPANSION**: Extended API with detailed system information beyond basic stats
- **FILE MONITORING**: Added real-time filesystem file counting vs database records comparison
- **PATH VALIDATION**: Enhanced path monitoring with detailed error reporting and file type breakdown
- **USER INTERFACE**: Enhanced stats display with comprehensive system information popup
- **SCANNING PERFORMANCE**: Major improvement in scanning performance for multi-path configurations
- **WORKER ALLOCATION**: Dynamic worker allocation across paths for better resource utilization
- **PROGRESS TRACKING**: Enhanced progress tracking with per-path scanning information

### Fixed
- **MISSING TOOL INFO**: Files with unsupported extensions now show "unsupported" instead of "N/A" in Tool column
- **SCAN OUTPUT**: All scan results now include proper scan_tool and scan_output information
- **RESCAN FUNCTIONALITY**: Rescanning any file now properly populates missing tool/output information

## [0.37] - 2025-07-08

### Changed
- **DOCKER**: Built and pushed new Docker image version 0.37 to Docker Hub
- **DOCKER**: Updated both versioned tag (0.37) and latest tag on ttlequals0/pixelprobe
- **DOCKER**: Image built for linux/amd64 platform as per requirements

## [0.36] - 2025-07-08

### Performance
- **MAJOR PERFORMANCE IMPROVEMENT**: Implemented lazy initialization to eliminate duplicate startup work across Gunicorn workers
- **WORKER EFFICIENCY**: Fixed issue where each worker was independently scanning and counting files during startup (4x duplicate I/O operations)
- **STARTUP TIME**: Dramatically reduced startup time by moving expensive operations (path validation, file counting) to only run when needed
- **MEMORY EFFICIENCY**: Reduced memory usage by initializing PixelProbe instances only when actually needed for scans

### Fixed
- **WORKER DUPLICATION**: Fixed duplicate file counting and path validation across multiple worker processes
- **STARTUP OVERHEAD**: Eliminated unnecessary `os.walk()` operations during container startup
- **LOG SPAM**: Removed duplicate startup messages from multiple workers

### Changed
- **INITIALIZATION**: Moved from eager to lazy initialization for media checker and path validation
- **RESOURCE USAGE**: Optimized resource usage by deferring expensive operations until first scan request

## [0.35] - 2025-07-08

### Fixed
- **FILTER FIX**: Fixed corruption-only filter not working - now properly filters across all pages, not just current page
- **FILTER FIX**: Moved filtering logic from client-side to server-side for proper pagination support
- **API ENHANCEMENT**: Added `filter` parameter to `/api/scan-results` endpoint to support server-side filtering

### Removed
- **UI CLEANUP**: Removed "Sort by:" dropdown and toggle button since column header sorting is now available
- **CODE CLEANUP**: Removed unused `sortResults()` and `toggleSortOrder()` functions
- **CSS CLEANUP**: Removed unused CSS classes for removed sort controls

### Changed
- **UI IMPROVEMENT**: Streamlined interface by removing redundant sorting controls
- **API IMPROVEMENT**: Enhanced `/api/scan-results` endpoint with proper filtering support

## [0.34] - 2025-07-08

### Added
- **UI ENHANCEMENT**: Implemented clickable column header sorting for all sortable columns
- **UI ENHANCEMENT**: Added visual sort indicators (↑/↓) to show current sort column and direction
- **API ENHANCEMENT**: Added sorting parameters support (`sort_field`, `sort_order`) to `/api/scan-results` endpoint

### Fixed
- **UI FIX**: Fixed buttons overlapping text in table rows by adjusting column widths and button sizing
- **UI FIX**: Improved table responsiveness with better column width distribution
- **UI FIX**: Made action buttons more compact to prevent text overlap
- **SORTING FIX**: Fixed sorting functionality that was not working properly - now supports sorting by clicking column headers
- **SORTING FIX**: Fixed filtering functionality to work properly with the new sorting system

### Changed
- **UI IMPROVEMENT**: Reduced action button sizes and improved spacing for better table layout
- **UI IMPROVEMENT**: Added responsive design for different screen sizes
- **UI IMPROVEMENT**: Enhanced table overflow handling for better text display

## [0.32] - 2025-07-07

### Changed
- **MAJOR REBRAND**: Application renamed from "MediaChecker" to "PixelProbe"
- Updated all branding, UI text, and documentation to reflect new name
- Docker images now published under `ttlequals0/pixelprobe`
- CSV exports now use "pixelprobe" filename prefix
- Updated application class name from `MediaChecker` to `PixelProbe`

## [0.32] - 2025-07-07

### Added
- **UI ENHANCEMENT**: Moved dark mode toggle to top right of the page for better accessibility
- **UI ENHANCEMENT**: Added file path search functionality to filter results by file path
- **UI ENHANCEMENT**: Reorganized action buttons into a single row for better layout
- **UI ENHANCEMENT**: Moved sort controls above results table, inline with filter buttons
- **NEW**: CSV export now supports exporting either selected files or all results
- **ENHANCEMENT**: Improved video player with multiple format support and error handling
- **ENHANCEMENT**: Enhanced dark mode text readability with better contrast and styling

### Fixed
- Improved video player reliability with fallback format support
- Enhanced error handling for media files that fail to load
- Better text contrast and readability in dark mode across all UI elements

### Changed
- Reorganized UI layout for better user experience and workflow efficiency
- Enhanced search and filtering capabilities with real-time file path search

## [0.31] - 2025-07-07

### Added
- **MAJOR**: Dark mode toggle with persistent user preference storage
- **MAJOR**: Parallel file processing with configurable worker threads (MAX_SCAN_WORKERS environment variable)
- **MAJOR**: File hash tracking (SHA-256) for change detection and data integrity
- **MAJOR**: Enhanced scan output tracking with detailed tool information and execution logs
- **NEW**: Inline media viewer with modal dialogs - view videos and images without leaving the page
- **NEW**: Variable speed video playback controls (0.5x - 4x speed) for quick file verification
- **NEW**: Deep scan functionality with enhanced corruption detection on selected files
- **NEW**: File change detection system comparing stored vs current file hashes
- **NEW**: Orphaned record cleanup - automatically remove database entries for deleted files
- **NEW**: Full scan output viewer showing detailed tool execution logs for debugging
- **NEW**: Scan tool tracking - displays which tool (FFmpeg, ImageMagick, PIL) detected issues
- **NEW**: Scan duration tracking for performance monitoring
- **NEW**: Sort and filter enhancements with sortable columns
- **NEW**: Bulk deep scan operations for selected files
- **NEW**: Enhanced database schema with new fields: file_hash, last_modified, scan_tool, scan_duration, scan_output
- **NEW**: Configuration management system for storing application settings
- **NEW**: Ignored error pattern management for customizable error filtering
- **NEW**: API endpoints for parallel scanning, file change detection, and cleanup operations

### Enhanced
- **UI/UX**: Complete responsive design overhaul with improved mobile experience
- **UI/UX**: Enhanced action buttons with distinct colors and improved functionality
- **UI/UX**: Modal-based media viewing replacing separate browser windows
- **UI/UX**: Real-time theme switching with smooth transitions
- **Performance**: Multi-threaded scanning for significantly faster processing
- **Performance**: Enhanced corruption detection with adaptive analysis strategies
- **Database**: Automatic migration system for new schema fields
- **API**: Extended REST endpoints for advanced functionality
- **Logging**: Comprehensive scan output capture for detailed debugging

### Technical Improvements
- Added ThreadPoolExecutor for concurrent file processing
- Implemented SHA-256 file hashing for change detection
- Enhanced MediaChecker class with parallel processing capabilities
- Added new database models: IgnoredErrorPattern, ScanSchedule, ScanConfiguration
- Improved error handling with detailed tool output capture
- Enhanced file info collection with modification time tracking
- Added real-time scan progress tracking with current file information

## [0.30] - 2025-07-07

### Fixed
- **BREAKING**: Removed mandatory MEDIA_PATH environment variable check
- Fixed confusing error message when MEDIA_PATH not set but SCAN_PATHS configured
- Clarified that MEDIA_PATH is only needed for Docker volume mounting, not app functionality
- Updated error messages to reference SCAN_PATHS instead of MEDIA_PATH
- Improved configuration documentation for multiple scan paths

### Changed
- Application now validates SCAN_PATHS directly instead of requiring MEDIA_PATH
- Better error messages when scan paths don't exist or contain no files
- Clearer separation between Docker mounting (MEDIA_PATH) and app configuration (SCAN_PATHS)

## [0.29] - 2025-07-07

### Added
- **MAJOR**: Adaptive enhanced corruption detection strategy
- **NEW**: Frame count integrity verification (detects missing/dropped frames)
- **NEW**: Temporal outlier detection using signalstats filter (detects visual corruption)
- **NEW**: Multi-point sampling for large files (checks beginning/middle/end)
- **NEW**: Strict error detection with enhanced pattern recognition
- **NEW**: Deep scan API support for user-requested thorough analysis
- Enhanced corruption detection automatically triggered when basic scan fails
- Comprehensive error pattern recognition for various corruption types

### Technical Details
- Enhanced checks run adaptively: only when basic scan fails or deep_scan requested
- Frame integrity: Compares expected vs actual frame count (>5% loss = corruption)
- Temporal outliers: Detects visual corruption like static, noise, glitches
- Multi-point sampling: For files >5GB, checks 3 points instead of just beginning
- Strict error detection: Uses `-err_detect crccheck+bitstream+buffer+explode`
- Performance optimized: Enhanced checks add 2-3x time only when needed

## [0.28] - 2025-07-07

### Fixed
- **MAJOR**: Fixed FFmpeg hanging on files with invalid duration metadata
- **MAJOR**: Invalid video duration no longer incorrectly marks files as corrupted
- Improved FFmpeg timeout: reduced from 30min to 5min max, shorter timeouts for large files
- Added `-t 30` flag to only check first 30 seconds of large files to prevent hanging
- Improved error filtering to ignore metadata issues and focus on actual corruption
- Added `-err_detect ignore_err` to get comprehensive error reports
- Better distinction between metadata issues vs actual file corruption

## [0.27] - 2025-07-07

### Fixed
- **HOTFIX**: Fixed application context error that prevented startup
- **HOTFIX**: Fixed incorrect model name (MediaFile -> ScanResult) in cleanup function
- App now properly starts with cleanup_stuck_scans() wrapped in app.app_context()
- Fixed 'name MediaFile is not defined' error

## [0.26] - 2025-07-07

### Fixed
- Fixed scanner getting stuck with files in 'scanning' status
- Added cleanup_stuck_scans() function that resets stuck files on startup
- Added proper exception handling around individual file scanning to prevent status inconsistencies
- Files that fail to scan are now properly marked as 'error' instead of staying stuck in 'scanning'
- Improved error recovery and database transaction handling in the scanning loop

## [0.25] - 2025-01-07

### Changed
- COMPLETE RESPONSIVE REDESIGN: Replaced all hardcoded pixel widths with modern responsive design
- Container now uses viewport-relative sizing (90vw) instead of fixed pixels
- Implemented fluid typography with clamp() for dynamic font scaling
- Replaced all pixel units with rem/em for better accessibility and scaling
- Table columns now use percentage-based widths for proper responsiveness
- Added mobile-first responsive breakpoints:
  - Mobile (<768px): Hides less important columns, adjusts layout for small screens
  - Tablet (769px-1200px): Balanced column visibility and widths
  - Large screens (>1800px): Expands to use available space efficiently
- Removed all minimum width constraints that prevented proper mobile viewing
- Typography now scales smoothly between devices using clamp() function
- Button and padding sizes use relative units for consistent scaling
- Table now properly responds to viewport changes without horizontal scrolling

## [0.24] - 2025-01-07

### Changed
- Built and pushed new Docker image version 0.24 to Docker Hub
- Image built for linux/amd64 platform as per requirements
- FIXED UI WIDTH: Reduced container max-width from 1800px to 1200px for better screen compatibility
- Reduced minimum width from 1400px to 800px for better responsive behavior
- Optimized table column widths: File Path (250px), Corruption Details (200px), Actions (240px)
- Reduced table minimum width from 1400px to 1000px
- Made buttons more compact with smaller padding and font size
- Improved overall UI to fit standard screen sizes without horizontal scrolling

### Added
- Initial project setup for MediaChecker
- CHANGELOG.MD file created as per project requirements
- Python Flask backend with REST API endpoints
- SQLAlchemy database models for scan results persistence
- Comprehensive media file corruption detection using FFmpeg, ImageMagick, and PIL
- Support for video formats: MP4, MKV, AVI, MOV, WMV, FLV, WebM, M4V
- Support for image formats: JPEG, PNG, GIF, BMP, TIFF, WebP
- Web interface with responsive design and modern UI
- File scanning with oldest-first ordering and subdirectory traversal
- File download functionality via web interface
- Docker and docker-compose configuration for easy deployment
- Environment variable configuration for scan paths
- Simplified configuration using internal SQLite database only
- Added MAX_FILES_TO_SCAN environment variable for testing with limited file counts
- Updated docker-compose.yml to use published Docker Hub image
- Added docker-compose.dev.yml for development builds
- Added GitHub Actions workflow for automated Docker image publishing
- Enhanced logging with detailed scan progress and web request tracking
- Fixed HTTP 400 error on scan requests by adding Flask application context to background threads
- Fixed button text visibility issues (white text on white background)
- Improved error handling and messaging for scan operations
- Added detailed debugging logs for troubleshooting
- Removed fake sample_media directory per CLAUDE.md rules - users must provide actual media paths
- Made MEDIA_PATH environment variable mandatory (no default fake data)
- Comprehensive documentation and README
- Multi-layered corruption detection:
  - Video: FFmpeg stream analysis, frame validation, quick scan
  - Images: PIL verification, ImageMagick analysis, format validation
- Statistics dashboard showing total, corrupted, and healthy files
- Pagination and filtering for scan results
- Real-time scan status updates
- Individual file rescanning capability
- Smart scanning feature to skip already processed files on subsequent scans
- Fixed progress indicator calculation to show only new files being scanned
- Improved file path display with better text wrapping and hover tooltips
- Mark as Good functionality for single files and bulk operations
- Added marked_as_good database field to track user-validated files
- CSV export functionality with timestamped filenames
- Export includes all scan data: file paths, sizes, corruption status, dates, etc.
- Database migration support to handle existing databases without marked_as_good column
- Automatic schema updates for backward compatibility
- Fixed UI button layout with responsive design and better wrapping
- Added scan status tracking API endpoint to check if scans are in progress
- Prevent multiple simultaneous scans with proper state management
- Scan progress persists across page refreshes
- Better scan monitoring with real-time status updates
- Fixed UI button layout with shorter text and better spacing
- Added horizontal scroll for button overflow on small screens
- Fixed scan logic to properly handle case when no new files found
- Added debugging logs to track skip paths and file scanning
- Implemented two-phase scanning system for better reliability and parallel processing support:
  - Phase 1: File discovery - finds all supported files and adds basic info to database
  - Phase 2: Corruption scanning - processes each pending file and updates scan results
  - Added scan_status field to track file processing state (pending, scanning, completed, error)
  - Added discovered_date field to track when files were first found
  - Improved database schema to support nullable fields during discovery phase
  - Better progress tracking with accurate file counts
  - Prevents duplicate scanning of same files
  - Foundation for future parallel scanning implementation
- Fixed database migration error for new columns (scan_status, discovered_date)
- Improved migration logic to handle existing databases gracefully
- Updated CSV export to include new fields with backward compatibility
- Fixed SQLite migration errors with CURRENT_TIMESTAMP defaults
- Made models backward compatible with getattr() for new fields
- Improved migration logic to use PRAGMA table_info for column detection
- Fixed ORM queries failing due to missing columns during migration
- Version 0.15 with robust database migration support
- Fixed missing action buttons (View, Rescan, Download) in results table - now shows for all scanned files
- Improved corruption details display with wider column and hover expansion
- Version 0.16 with UI action button fixes
- Fixed scan discovery to exclude already-scanned files from database - now finds next batch of unscanned files
- Improved FFmpeg timeout logic - dynamic timeout based on file size (60s + 1s per 10MB, max 300s)
- Added better logging for FFmpeg timeouts including file size information
- Version 0.17 with scan discovery and timeout fixes
- Researched and implemented FFmpeg best practices for video validation
- Added optimized FFmpeg command with `-c copy` flag for faster processing without re-encoding
- Improved timeout calculation: 60s base + 30s per GB, max 30 minutes for thorough validation
- Fixed scan status detection across multiple gunicorn workers using database state
- Fixed progress bar disappearing while scan is still running
- Improved UI table layout with fixed column widths and better action button sizing
- Added proper timezone handling using UTC timestamps
- Version 0.18 with comprehensive FFmpeg optimization and UI fixes
- Added configurable timezone support via TZ environment variable in docker-compose
- Application now respects system timezone for display purposes
- Version 0.19 with timezone configuration support
- Fixed Docker health check by adding curl to the container image
- Health checks now work properly and prevent container being marked as unhealthy
- Version 0.20 with fixed health check support
- MAJOR UI improvements: increased container width from 1200px to 1600px for better table layout
- Significantly widened table columns: File Path (400px), Actions (280px), Corruption Details (300px)
- Fixed rescan functionality - now properly updates existing records instead of trying to insert duplicates
- Added comprehensive user feedback for rescan operations with loading states and success/error messages
- Improved action button sizing and spacing for better usability
- Enhanced corruption details hover display with better positioning
- Version 0.21 with major UI improvements and rescan fixes
- COMPLETE UI OVERHAUL: Modern responsive design with proper table scrolling
- Increased container width to 1800px with minimum width of 1400px for wide displays
- Added proper table container with horizontal scrolling and 20px padding
- Fixed action buttons with proper spacing (320px width) and no more cut-offs
- Changed table layout from fixed to auto for dynamic sizing
- Added distinct button colors: View (blue), Rescan (orange), Mark Good (green), Download (purple)
- Enhanced button styling with hover effects and proper sizing (65px min-width)
- Version 0.22 with complete modern responsive UI
- ENHANCED PROGRESS TRACKING: Detailed real-time scanning progress with current file information
- Added comprehensive scan state tracking with current file being scanned, elapsed time, and ETA
- Implemented dynamic timeout calculation for FFmpeg: 60s base + 30s per GB with 30-minute maximum
- Enhanced /api/scan-status endpoint to return detailed progress information including:
  - Current file being scanned with elapsed time (e.g., "Scanning: filename.mkv (45s)")
  - Estimated time to completion (ETA) based on average scan times
  - Rolling average scan time per file for accurate predictions
  - Progress phase tracking (discovering, scanning, idle)
- Frontend now displays rich progress information: "Scanning: movie.mkv (12s) | ETA: 2m 30s | Avg: 15.2s/file"
- Added formatETA() JavaScript utility function for human-readable time formatting
- Version 0.23 with enhanced detailed progress tracking and ETA calculations

### Technical Features
- Flask web framework with CORS support
- SQLite database for persistent storage
- Threaded scanning for non-blocking operations
- Comprehensive error handling and logging
- Docker multi-stage builds for optimized containers
- Health checks and restart policies
- Configurable timeouts and batch processing