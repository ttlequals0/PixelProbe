# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.31] - 2025-01-17

### Fixed
- Force version update to ensure proper deployment
- Same fixes as 2.0.30 but with clean rebuild

## [2.0.30] - 2025-01-17

### Fixed
- **Cleanup Orphans Progress Bar**:
  - Fixed progress bar jumping to 100% and disappearing while scan continues in background
  - Added proper handling for cases where no orphaned files are found
  - Progress bar now accurately reflects Phase 1 (file checking) completion
  - Added detailed batch processing logs to track actual scan progress
  - Progress bar remains visible until the entire operation completes, not just the deletion phase

## [2.0.29] - 2025-01-17

### Enhanced
- **File Change Detection Improvements**:
  - Now calculates hashes for ALL files, not just those with changed timestamps
  - Detects silent file corruption and any modifications regardless of timestamp changes
  - Processes files in batches of 1000 records to handle databases with millions of files
  - Phase-based progress tracking with persistent progress bar throughout entire process
  - Memory efficient processing that can handle large databases without issues

- **Orphaned Files Cleanup Optimization**:
  - Completely rewritten to use batched queries instead of loading entire database into memory
  - Processes files in batches of 1000 records for checking file existence
  - Stores only file IDs during detection phase to minimize memory usage
  - Bulk deletion operations in batches of 1000 for efficient database cleanup
  - Two-phase progress tracking: Phase 1 for detection, Phase 2 for deletion
  - Can handle databases with millions of files without memory issues

- **Unified Progress Display**:
  - All operations (scan, file changes, cleanup) now use consistent phase-based progress tracking
  - Progress bars persist throughout entire operation with clear phase indicators
  - Real-time file processing updates showing current file and counts
  - Progress percentage calculated based on current phase completion
  - Consistent UI experience across all long-running operations

## [2.0.28] - 2025-07-16

### Fixed
- **Marked as Good Files Properly Treated as Healthy**:
  - Files marked as good no longer appear in corrupted or warning filters
  - Backend filtering logic updated to exclude marked_as_good files from corrupted/warning counts
  - Stats calculations now properly exclude marked_as_good files from corrupted and warning totals
  - Frontend UI now displays "Healthy" status for all files marked as good
  - Fixed both desktop table view and mobile card view to show correct status
  - Updated scan output modal to display correct status for marked_as_good files
  - Ensures consistency across all views and filters

## [2.0.27] - 2025-07-15

### Fixed
- **Mobile UI Duplicate Elements**:
  - Fixed duplicate sidebar showing on mobile devices
  - Added proper CSS overrides to ensure desktop sidebar is completely hidden on mobile
  - Used !important declarations strategically to override desktop styles
  - Added visibility and pointer-events controls to prevent interaction with hidden sidebar
  - Ensured main content has no left margin on mobile (was inheriting desktop sidebar margin)
  - Fixed sidebar transform and position properties to work correctly on mobile
  - Added cache-busting version parameters to force CSS reload
  - Mobile sidebar now properly slides in from left only when activated
  - Desktop sidebar collapse functionality no longer interferes with mobile behavior

## [2.0.26] - 2025-07-15

### Fixed
- **Completely Refactored Mobile/Desktop Toggle**:
  - Removed separate mobile and desktop toggle buttons from HTML
  - Created single unified toggle button that works for both mobile and desktop
  - Updated JavaScript to detect screen size and use appropriate toggle behavior
  - Removed all CSS attempting to hide desktop button on mobile
  - Fixed the root cause of double hamburger menu on mobile
  - Added cache busting query parameters to CSS files to force reload

## [2.0.25] - 2025-07-15

### Fixed
- **Mobile UI Desktop Toggle Still Visible**:
  - Added more aggressive CSS rules to force hide desktop-sidebar-toggle on mobile
  - Used multiple CSS properties including visibility, dimensions, and positioning
  - Added explicit display rule for mobile-menu-btn to ensure it's the only visible toggle
  - Placed rules at end of mobile.css to ensure they take precedence

## [2.0.24] - 2025-07-15

### Fixed
- **Mobile UI Double Toggle Button**:
  - Fixed issue where desktop sidebar toggle button was visible on mobile
  - Added CSS rule to hide desktop-sidebar-toggle on screens smaller than 768px
  - Mobile now correctly shows only the hamburger menu button

## [2.0.23] - 2025-07-15

### Added
- **Enhanced Debugging for Progress Operations**:
  - Added extensive logging to cleanup orphaned endpoint and async function
  - Added console logging in JavaScript to track operation flow
  - Added automatic status check after 1 second to debug progress monitoring
  - Added test endpoint `/api/test-cleanup` to inspect cleanup state
  - Added exception logging with full stack traces
  - Added try-catch around thread creation to catch startup errors

## [2.0.22] - 2025-07-15

### Fixed
- **Progress Tracking App Context Issue**:
  - Fixed missing Flask app context in async cleanup and file changes functions
  - Both async functions now properly wrap database operations in app.app_context()
  - This was preventing the async operations from executing properly
  - Added debug logging to help diagnose issues with operation responses

## [2.0.21] - 2025-07-15

### Added
- **Progress Bars for Cleanup and File Changes Operations**:
  - Added real-time progress tracking for cleanup orphaned records operation
  - Added real-time progress tracking for file changes check operation
  - Both operations now run asynchronously with visual progress indicators
  - Progress bars show current file being processed, percentage complete, and items found
  - Cleanup orphans shows count of orphaned files found during scan
  - File changes check shows count of modified/deleted files found
  - Operations can run without blocking the UI
  - Added `/api/cleanup-status` and `/api/file-changes-status` endpoints for progress monitoring
  - Reused existing progress UI components for consistent user experience
  - Progress titles dynamically update based on operation type
  - Automatic UI refresh after cleanup operation completes
  - File changes results displayed in alert dialog when changes are found

### Fixed
- **Modern UI Progress Monitoring Bug**:
  - Fixed missing interval parameter in setInterval call that caused excessive server polling
  - Was polling server potentially hundreds of times per second instead of once per second
  - Added explicit 1000ms interval to match legacy UI behavior

## [2.0.20] - 2025-07-14

### Fixed
- **UI Not Updating After Rescan**:
  - Fixed issue where rescanned items that changed status (e.g., corrupted to warning) weren't updating in the UI
  - Modified ProgressManager to accept app instance reference
  - Added automatic table and stats refresh when scan completes
  - UI now properly reflects status changes immediately after rescan without requiring page refresh

## [2.0.19] - 2025-07-14

### Fixed
- **Simplified Scanning Logic - Fixed Pagination Issues**:
  - Completely rewrote scanning logic to use pre-loaded ID lists instead of pagination
  - Previous offset-based pagination would skip files as their status changed
  - Now loads all pending file IDs upfront into memory (minimal overhead)
  - Processes files in batches from the ID list - guaranteed to scan all files
  - Much simpler and more reliable than complex pagination logic
  - Eliminates all edge cases where files could be skipped
  - Affects both main scan and rescan operations

## [2.0.18] - 2025-07-14

### Fixed
- **Premature Scan Termination Bug**:
  - Fixed critical bug where scans would terminate after processing only 3000 files
  - Added ORDER BY clause to all paginated queries to ensure stable pagination
  - Without ORDER BY, offset queries can skip records when data changes during iteration
  - Affected queries: main scan loop, batch discovery, and rescan operations
  
### Added
- **Scan Completion Verification**:
  - Added check after scan loop to verify all pending files were processed
  - Logs warning if files remain pending after scan completion
  - Reports exact count of remaining pending files in completion summary

## [2.0.17] - 2025-07-14

### Fixed
- **Scan Deadlock Prevention**:
  - Fixed critical issue where scan gets stuck when phase_current exceeds pending_count
  - Added safeguard in scan loop to detect and break when files_processed >= pending_count
  - Added logging to identify when no more pending files are found at offset
  - Prevents infinite loop condition that left scan in stuck "idle" state
  
### Added
- **Emergency Scan Recovery Endpoint**:
  - Added `/api/recover-stuck-scan` POST endpoint for manual recovery
  - Resets scan state and marks any "scanning" files back to "pending"
  - Provides clean recovery path without restarting the application
  
- **Stuck Scan Detection**:
  - Added automatic detection in `/api/scan-status` endpoint
  - Logs error when phase_current >= phase_total indicating stuck state
  - Helps identify stuck scans before they impact user experience

## [2.0.16] - 2025-07-14

### Fixed
- **Targeted Rescan Implementation**:
  - Fixed rescan getting stuck in discovery phase for over 1 million files
  - Rescan now skips directly to Phase 3 (scanning) without discovery
  - Only processes the specific files marked as pending
  - Significantly faster rescan startup (immediate vs minutes of loading)
  - Proper progress tracking shows "Phase 3 of 3: Scanning"
  - Memory efficient - doesn't load all existing files

### Added
- **Collapsible Sidebar on Desktop**:
  - Added toggle button in header to collapse/expand sidebar
  - Collapsed state shows only icons with tooltips on hover
  - Main content area expands when sidebar is collapsed for more table space
  - Sidebar state is saved in localStorage and restored on page load
  - Smooth transitions for width changes
  - Toggle icon changes to indicate state (bars vs angles-right)

## [2.0.15] - 2025-07-14

### Fixed
- **Rescan Progress Tracking & Memory Optimization**:
  - Simplified rescan approach: marks files as pending and starts normal scan
  - Rescan now shows same progress bar and UI elements as normal scan
  - Added `deep_scan` column to database to track deep scan requests
  - Deep scan and regular rescan both use the same infrastructure
  - Fixed memory issues:
    - Removed accumulation of results in memory during batch processing
    - Process pending files in batches instead of loading all at once
    - Load existing file paths in batches (10k at a time) instead of all at once
    - Clear database session periodically to free memory
    - Use bulk updates for better performance
  - Prevents multiple concurrent scans during rescan operations
  - Frontend properly handles scan status checks before starting rescans

## [2.0.14] - 2025-07-14

### Fixed
- **"All" Pagination Option**:
  - Fixed backend to properly handle per_page=-1 for showing all results
  - Added special handling to bypass pagination when "All" is selected
  - Returns all results in a single response without pagination limits

## [2.0.13] - 2025-07-14

### Fixed
- **Mobile Pagination Next Button Cutoff**:
  - Reduced number of visible pages on mobile (only 1, current, last)
  - Made buttons even more compact
  - Reduced gaps between buttons
  - Added responsive sizing for very small screens (375px and below)
  - Ensured container stays within viewport width
  - Fixed padding to prevent cutoff

## [2.0.12] - 2025-07-14

### Fixed
- **Mobile Pagination - No More Horizontal Scroll**:
  - Removed horizontal scrolling - pagination now fits on screen
  - Smart page display: shows 1, prev, current, next, last pages only
  - Made buttons more compact with smaller padding and font
  - Previous/Next buttons always visible
  - Ellipsis used intelligently to save space
  - Different logic for mobile vs desktop pagination

## [2.0.11] - 2025-07-14

### Fixed
- **Dynamic Version Display**:
  - Version now reads from APP_VERSION environment variable
  - Dockerfile updated to set version during build with ARG
  - Version can be passed during docker build: --build-arg VERSION=x.x.x
  
- **Mobile Pagination Final Fix**:
  - Increased z-index for Previous/Next buttons to stay on top
  - Added margins to prevent button overlap
  - Added background extensions to ensure clickability
  - Increased padding space for sticky buttons
  - Enhanced box shadows for better visibility

## [2.0.10] - 2025-07-14

### Fixed
- **Mobile Pagination Button Overlap**:
  - Fixed page number buttons overlapping and getting cut off
  - Improved sticky Previous/Next button implementation
  - Added proper spacing between buttons
  - Reduced ellipsis width for better space usage
  - Enhanced button backgrounds and borders for sticky elements

## [2.0.9] - 2025-07-14

### Fixed
- **Pagination Issues**:
  - Fixed items per page selector not working (was always showing 20)
  - Fixed initialization to read selected value from dropdown
  - Fixed pagination calculation for "All" option
  - Hide pagination when "All" is selected
  
- **Mobile Pagination Display**:
  - Made Previous/Next buttons sticky on mobile to prevent cutoff
  - Added horizontal scrolling for page numbers
  - Auto-scroll to center active page on mobile
  - Fixed button overlap and cutoff issues
  - Reduced page range on mobile (shows fewer page numbers)
  - Improved button sizing and spacing for touch targets
  - Added proper ellipsis styling
  - Fixed Previous/Next button backgrounds and borders
  - Added padding to prevent page numbers from going under sticky buttons

## [2.0.8] - 2025-07-14

### Fixed
- **Mobile Video Playback CSS**:
  - Added mobile-specific CSS styles matching v1.x approach
  - Set video width to 100% on mobile devices
  - Added smooth scrolling for modal content
  - Improved modal sizing for mobile viewports

## [2.0.7] - 2025-07-14

### Fixed
- **Video Playback - Reverted to v1.x Implementation**:
  - Removed playsinline and webkit-playsinline attributes that may cause issues
  - Removed muted attribute to match v1.x behavior
  - Set preload="metadata" consistently (not "auto")
  - Added multiple video format sources (mp4, webm, ogg) like v1.x
  - Simplified video player to match proven v1.x implementation
  - Removed mobile-specific JavaScript handling that was causing issues
  - Let browser handle video playback natively without interference

## [2.0.6] - 2025-07-14

### Fixed
- **Video Playback on Mobile (Additional Fixes)**:
  - Enhanced byte-range request handling for better mobile compatibility
  - Improved CORS configuration for video streaming
  - Added proper OPTIONS preflight handling
  - Fixed Response import in Flask app
  - Ensured proper content-type headers for video responses
  - Removed muted attribute for mobile devices to allow user control
  - Added preload="metadata" for mobile to reduce data usage
  - Added "Open in new tab" option for mobile users
  - Enhanced error logging for video playback issues
  - Added video load event listeners for better debugging

## [2.0.5] - 2025-07-13

### Fixed
- **Video Playback on Mobile (Enhanced)**:
  - Added byte-range request support for video streaming (required by mobile Safari)
  - Implemented proper 206 Partial Content responses
  - Added playsinline and webkit-playsinline attributes for iOS
  - Added muted attribute for autoplay compatibility
  - Changed preload to "auto" for better mobile loading
  - Added CORS headers for cross-origin video requests
  - Added OPTIONS preflight handler for mobile browsers
  - Added fallback direct link if video fails to load
  - Added error handling with user-friendly message
  - Limited to essential video formats (original + mp4)

### Changed
- **Project Organization**:
  - Moved Docker development files to `docker/` directory
  - Moved utility scripts to `scripts/` directory
  - Consolidated screenshots in `docs/screenshots/`
  - Removed duplicate directories and files
  - Added PROJECT_STRUCTURE.md documentation

## [2.0.4] - 2025-07-13

### Fixed
- **Modal Improvements**:
  - Fixed close button (X) color to match theme (now uses text-primary color)
  - Close button turns red on hover for better UX
  - Fixed image/video scaling in media viewer to avoid scroll bars (max-height: 60vh)
  - Media content is now centered in the modal

- **Table Header Fixes**:
  - All table headers now stay on one line (added white-space: nowrap)
  - Scan Date sort arrow is now on the same line as the text
  - No more wrapped headers

- **File Path Column**:
  - Set consistent width (350px) for cleaner wrapping
  - Uses word-break: break-word for better line breaking
  - No more awkward mid-word breaks

### Added
- **Scan Button State Management**:
  - Start Scan button is now disabled (grayed out) when a scan is in progress
  - Prevents accidental double-scanning
  - Button automatically re-enables when scan completes
  
- **Mobile UI Enhancements**:
  - Added missing View and Download buttons to mobile card view
  - Mobile now has feature parity with desktop interface

## [2.0.3] - 2025-07-13

### Fixed
- **Table Display Issues**:
  - Fixed Scan Date column header to show sort arrows on a new line
  - Fixed file path column to wrap long paths instead of squishing other columns
  - Added max-width and word-break to file path cells
  
- **Details Button**: Fixed "Failed to load scan output" error by properly handling null values

- **Progress Bar Enhancement**: 
  - Implemented proper 3-phase progress tracking as per version 1.30
  - Phase 1: Discovery (0-33%)
  - Phase 2: Adding to Database (33-66%)  
  - Phase 3: Scanning (66-100%)
  - Shows current file being scanned in each phase
  - Uses phase_number from backend for accurate tracking

## [2.0.2] - 2025-07-13

### Fixed
- **Progress Bar Display**: 
  - Fixed progress bar to show percentage in the bar (like original UI)
  - Shows current file being scanned below the progress bar
  - Better progress calculation including files currently being scanned
  - Initial display shows 0% instead of "Initializing..."

### Changed
- **UI Improvements**: Made Scan Date column wider (150px minimum) for better display

## [2.0.1] - 2025-07-13

### Fixed
- **Critical Production Issues**:
  - Fixed Start Scan button not working (added empty JSON body to request)
  - Fixed Details popup showing 400 error (corrected API endpoint)
  - Fixed View button not opening media player (implemented showMediaViewerModal)
  - Added missing Download buttons for individual items
  - Added bulk download functionality
  
### Changed
- **Export CSV Enhancement**: Export now includes all items in current filter when nothing is selected
- **UI Improvements**: Made Size column wider to accommodate sort arrows on the same line

### Enhanced
- **CSV Export Feedback**: Added loading notification to indicate CSV generation is in progress
- **Bug Fixes**: Fixed escapeHtml function scope issue for View and Details buttons

## [2.0] - 2025-07-13

### Added
- **MAJOR UI OVERHAUL**: Complete frontend redesign with modern, professional interface
  - Hulu-inspired design language with green accent color (#1ce783)
  - Pi-hole style sidebar navigation with collapsible menu
  - Separate CSS files for desktop and mobile to avoid conflicts
  - Modern responsive design with mobile-first approach
  - iPhone optimization with notch awareness using safe-area-inset
  
- **New UI Components**:
  - Professional sidebar with navigation sections (Main, Tools, System, Settings)
  - Integrated dark/light mode toggle in sidebar settings
  - System stats and build info in sidebar navigation
  - Modern card-based layout with proper shadows and spacing
  - Responsive stats grid with hover effects
  - Enhanced table design with sortable columns
  - Professional button styles with Hulu-inspired colors
  
- **API Documentation Page**: 
  - Comprehensive REST API documentation at /api-docs
  - Organized by endpoint categories (Core, Scanning, File Management, etc.)
  - Request/response examples for all endpoints
  - Parameter tables with types and descriptions
  - Professional documentation layout matching main UI theme
  
- **Mobile Optimizations**:
  - Touch-friendly interface with 44px minimum touch targets
  - Collapsible sidebar with overlay for mobile
  - Responsive table that converts to cards on small screens
  - Optimized for iPhone with notch awareness
  - Landscape orientation adjustments
  
- **Modern JavaScript Architecture**:
  - Modular ES6+ class-based architecture
  - ThemeManager for dark/light mode with localStorage persistence
  - SidebarManager for mobile navigation
  - APIClient for centralized API communication
  - StatsDashboard with auto-refresh capabilities
  - ProgressManager for scan progress tracking
  - TableManager for advanced data display
  
- **Enhanced User Experience**:
  - Smooth theme transitions between light and dark modes
  - Real-time stats updates every 5 seconds
  - Improved progress bar with detailed status messages
  - Better error handling and user notifications
  - Responsive design that works on all screen sizes

### Changed
- **Database Configuration**: Updated to use provided database for local testing
- **Template System**: Added USE_MODERN_UI environment variable to toggle between classic and modern UI
- **Color Scheme**: Adopted Hulu green (#1ce783) as primary accent color
- **Layout**: Moved from single-page layout to sidebar navigation pattern

### Technical Improvements
- Separated mobile and desktop CSS for better maintainability
- Implemented CSS variables for easy theming
- Added proper viewport meta tags for mobile devices
- Used modern flexbox and grid layouts
- Added Font Awesome 6.4.0 for icons
- Implemented proper modal system for dialogs

### Docker Support
- Created `Dockerfile.modern` for easy testing of the new UI
- Added `docker-compose.modern.yml` for local development
- Included sample test data generation in Docker image
- Created `docker-run-modern.sh` script for one-command startup

### Final Refinements
- Fixed warnings filter to properly show only files with warnings
- Added pagination UI with page numbers and navigation
- Removed redundant "Scan Results" menu item from sidebar
- Added "Details" button to view full scan output in modal
- Implemented File System Statistics showing completion percentages
- Fixed mobile layout issues including horizontal scroll
- Changed action buttons to vertical stack layout
- Removed unnecessary "Scanning Files" stat card
- Fixed mixed mobile/desktop view rendering
- Ensured consistent button styling throughout the UI

## [1.47] - 2025-07-13

### Fixed
- **Mobile Vertical Text Display Issue**:
  - Fixed "Details:" label appearing character by character vertically
  - Changed from data-label attribute selector to nth-child selector
  - Added proper display properties to ensure inline text rendering
  - Removed webkit-line-clamp and box-orient from mobile corruption details
  
- **Desktop Table Layout**:
  - Fixed horizontal scrolling issue while maintaining all column visibility
  - Adjusted column widths: status (85px), type (60px), tool (60px), path (20%)
  - Changed table layout to fixed for better column control
  - Allowed auto scroll on desktop table container when absolutely needed
  
- **Corruption Details Hover**:
  - Restored hover expansion functionality with 2-line initial display
  - Added span wrapper to corruption details content for proper styling
  - Maintained hover tooltip effect on desktop
  - Disabled hover effect on mobile for better touch experience
  
- **General Improvements**:
  - Changed default Flask port to 5001 to avoid macOS AirPlay conflict
  - Fixed mobile card layout corruption details display
  - Ensured proper text wrapping and overflow handling

## [1.46] - 2025-07-13

### Fixed
- **Mobile Display Issues**:
  - Removed problematic writing-mode CSS that was causing vertical text
  - Simplified mobile corruption details display
  - Fixed "Details:" label to display inline properly
  - Removed unnecessary CSS overrides
  
- **Desktop Table Layout**:
  - Changed table layout to fixed for better column control
  - Increased minimum table width to 1400px
  - Adjusted corruption column to prevent content overflow

## [1.45] - 2025-07-13

### Fixed
- **Desktop Display Issues**:
  - Fixed corruption details column being cut off
  - Increased actions column width to 360px minimum
  - Increased corruption column width to 25%
  - Fixed action buttons being cut off on right side
  
- **Mobile Corruption Details Display**:
  - Fixed vertical text display issue
  - Kept block display for mobile
  - Removed conflicting flex styles
  - Ensured "Details:" label displays inline
  
- **Responsive Design Improvements**:
  - Added proper breakpoints for tablets (992px)
  - Implemented horizontal scrolling for smaller screens
  - Adjusted column widths for different screen sizes
  - Made action buttons responsive with smaller text

## [1.44] - 2025-07-13

### Fixed
- **Mobile Details Display**:
  - Fixed "Details:" label displaying vertically character by character
  - Changed label display from block to inline with margin
  - Added writing-mode reset to ensure horizontal text display
  - Fixed issue affecting healthy, warning, and corrupted files
  - Ensured proper text orientation for all mobile card views

## [1.43] - 2025-07-13

### Fixed
- **Corruption Details Display**:
  - Changed from multi-line to single-line with ellipsis by default
  - Implemented tooltip-style hover expansion with proper styling
  - Added white background, border, and shadow for better readability
  - Fixed text wrapping and alignment issues
  - Increased font size to 0.85rem for better legibility
  
- **Hover Expansion Improvements**:
  - Changed to absolute positioning to prevent layout shifts
  - Added max-width of 400px to control expansion size
  - Added proper z-index to ensure hover appears above other content
  - Fixed dark mode hover styling
  
- **Mobile Corruption Details**:
  - Disabled hover effects on mobile devices
  - Kept normal text wrapping for touch interfaces
  - Prevented tooltip positioning issues on small screens

## [1.42] - 2025-07-13

### Fixed
- **Column Header Truncation**: 
  - Increased scan status column width to 140px
  - Increased corruption details column width to 280px
  - Added `white-space: nowrap` to headers to prevent truncation
  - Removed overflow ellipsis from headers
  
- **Column Content Display**:
  - Fixed status badge truncation with nowrap and inline-block
  - Increased status column width to 120px
  - Increased type column width to 150px
  - Increased tool column width to 120px
  - Added specific styles to prevent type/tool/status truncation
  
- **Corruption Details Enhancement**:
  - Changed to multi-line display (up to 2 lines)
  - Increased column width to 280px
  - Added line clamping for better text visibility
  - Set minimum height for consistent row heights
  
- **Desktop Responsiveness**:
  - Changed table layout from fixed to auto
  - Reduced minimum table width to 1200px
  - Added flexible column sizing with percentages
  - Added min-width constraints for all columns
  - Made path and corruption columns percentage-based

## [1.41] - 2025-07-13

### Fixed
- **Desktop Table Column Issues**:
  - Fixed size column wrapping by adding `white-space: nowrap`
  - Increased size column width from 80px to 100px
  - Increased type column width from 80px to 120px  
  - Increased tool column width from 80px to 100px
  - Added proper column classes to table cells for consistent styling
  - Increased table minimum width to 1400px to accommodate wider columns
  - Ensured file size never wraps to multiple lines

## [1.40] - 2025-07-13

### Fixed
- **Mobile Status Badge Padding**: Added proper padding (6px 12px) to status badges
- **File Path Word Wrapping**: Implemented aggressive word breaking for long file paths
  - Added `word-break: break-all` for better path splitting
  - Added `hyphens: auto` for better line breaks
  - Ensured full width display for paths
  
- **Action Buttons Layout**: Changed to 2x2 grid layout
  - Buttons now display in clean 2-column grid
  - Increased padding for better touch targets
  - Fixed width to prevent layout issues
  
- **Horizontal Scroll Removal**: Eliminated unwanted horizontal scrolling
  - Added `overflow-x: hidden` to body and containers
  - Fixed table width and layout issues
  - Removed all min-width constraints causing overflow
  
- **Details Word Wrapping**: Added proper text wrapping for corruption details
  - Long error messages now wrap properly
  - Better readability on small screens

## [1.39] - 2025-07-13

### Fixed
- **Mobile Scan Results Table**: Complete redesign to address user feedback
  - File paths now display prominently at the top of each card
  - Fixed missing file path issue from v1.38
  - Added !important declarations to ensure mobile styles override desktop
  - Removed checkbox column on mobile for cleaner layout
  - Improved text wrapping and readability
  
### Changed
- **Mobile Card Layout Improvements**:
  - File path shown first with clear visual separation
  - Status and size displayed inline for better space usage
  - Corruption details in dedicated background section
  - Actions clearly separated with top border
  - Better contrast and spacing throughout
  - Fixed overflow issues preventing content visibility

## [1.38] - 2025-07-13

### Fixed
- **iPhone UI Overhaul**: Complete redesign for mobile/iPhone devices
  - Fixed header buttons overlapping with logo
  - Buttons now properly contained in horizontal layout
  - Logo properly sized and centered
  - Eliminated awkward button positioning

- **Mobile Table Redesign**: Replaced broken table with card-based layout
  - Each scan result now displayed as a card on mobile
  - Key information (status, path, size, actions) clearly visible
  - No more truncated column headers
  - Touch-friendly action buttons in 2x2 grid
  - Proper spacing and readability on small screens

- **Stats Cards Mobile Layout**: Optimized for iPhone screens
  - 2x2 grid layout prevents text wrapping
  - Smaller font sizes for better fit
  - Reduced padding for space efficiency
  - Numbers no longer wrap to multiple lines

### Added
- **iPhone-Specific Optimizations** (screens ≤480px):
  - Compact header with smaller logo (60px)
  - Smaller fonts throughout (h1: 1.25rem, body: 0.75rem)
  - Optimized button sizes (32px height)
  - Touch-friendly tap targets (minimum 44px)
  - Efficient use of vertical space

- **Card-Based Mobile Table**:
  - Each result displayed as individual card
  - Status badge prominently displayed
  - Full file path visible with word wrapping
  - Key data (size, corruption details) with labels
  - Action buttons in responsive grid

### Changed
- **Responsive Breakpoints**:
  - Mobile: ≤768px (tablets and phones)
  - iPhone: ≤480px (small phones)
  - Tablet: 769px-1200px
  - Desktop: >1200px
  
- **Mobile-First Architecture**:
  - Tables transform to cards on mobile
  - Progressive enhancement for larger screens
  - Touch-optimized interactions
  - Efficient vertical scrolling

## [1.37] - 2025-07-13

### Fixed
- **Real-time Stats Updates**: Removed batching to enable immediate count updates
  - Each scan result is now sent immediately to the write queue
  - Stats update in real-time as each file completes scanning
  - No more waiting for 50 files to complete before seeing progress
  - Fixes issue where counts were stuck during slow scans of large files

### Removed
- **Batch Processing**: Eliminated redundant batching layer
  - The write queue already handles database serialization
  - Batching on top of the queue was delaying real-time updates
  - Removed `BATCH_COMMIT_SIZE` environment variable (no longer needed)

## [1.36] - 2025-07-13

### Fixed
- **UI Button Consistency**: Fixed System Stats and Dark/Light Mode buttons having different sizes
  - Unified button styling with consistent height (40px) and min-width (140px)
  - Added flexbox centering for proper text alignment
  - Both buttons now use shared `.header-button` class

- **Stat Card Number Wrapping**: Fixed file totals wrapping to multiple lines
  - Added `white-space: nowrap` to prevent number wrapping
  - Reduced font size with better responsive scaling
  - Set minimum card height to ensure consistent layout

- **Table Column Truncation**: Fixed table headers being cut off ("Sta...", "Corrupt...", etc.)
  - Increased column widths for better content visibility
  - Improved table layout with proper min-widths
  - Added horizontal scrolling for better mobile experience

### Added
- **Comprehensive Responsive Design**: Implemented modern mobile-first approach
  - Mobile (≤768px): Simplified table view, stacked controls, single column stats
  - Tablet (769px-1200px): Optimized column visibility, 3-column stats grid
  - Desktop (>1200px): Full feature set with all columns visible
  - Added touch-friendly scrolling for mobile devices

- **Mobile UI Optimizations**:
  - Vertically stacked header buttons on small screens
  - Responsive stats grid (2 columns on mobile, 1 column on very small screens)
  - Collapsible table columns based on screen size
  - Touch-optimized action buttons with proper sizing
  - Full-width search and filter controls on mobile

### Changed
- **Table Architecture**: Updated to use modern CSS techniques
  - Fixed table layout for better column control
  - Proper overflow handling with horizontal scroll
  - Responsive column hiding based on importance
  - Improved text truncation with ellipsis

## [1.35] - 2025-07-13

### Fixed
- **Database Locking Issues**: Implemented database write queue to fix SQLite locking errors
  - Added centralized write queue to serialize all database write operations
  - Workers now submit results to queue instead of writing directly
  - Dedicated write thread processes queue to avoid concurrent write conflicts
  - Fixes "database is locked" errors during high-volume scanning

### Added
- **Database Write Queue System**: New architecture for handling database writes
  - `db_write_queue` for queuing all database operations
  - `db_write_thread` for processing writes sequentially
  - Support for batch updates to improve performance
  - Automatic thread startup on application initialization

### Changed
- **Scan Result Processing**: Refactored to use write queue
  - `process_scan_result` now queues writes instead of direct DB access
  - Batch processing of scan results for better performance
  - Scan state updates routed through write queue
  - Final batch commits handled via queue system

## [1.34] - 2025-07-12

### Fixed
- **UI Scaling Issues**: Fixed various UI elements not displaying properly
  - Fixed table column headers being truncated ("Sta...", "Ty...", "Corrupt...")
  - Made System Stats and Dark/Light Mode buttons the same size (140px min-width)
  - Fixed action buttons text being cut off by increasing padding and min-width
  - Improved stat card number display to prevent truncation of large numbers
  - Adjusted column widths for better content visibility

### Changed
- **Table Layout**: Improved table column width distribution
  - Status: 90px (was 80px)
  - Scan Status: 110px (was 100px)  
  - Corruption Details: 140px (was truncated)
  - Date: 150px (was 140px)
  - Actions: auto-width with 300px minimum
  - Path: 30% with proper text truncation

- **Responsive Design**: Enhanced responsive behavior
  - Better scaling at 1200px breakpoint
  - Improved container max-width at different screen sizes
  - Action buttons scale better on smaller screens
  - Font sizes adjust more appropriately

## [1.33] - 2025-07-12


### Added
- **Database Scan State Tracking**: Implemented persistent scan state tracking in database
  - Added new `ScanState` model to track scan progress across app restarts
  - Scan state persists in database with unique scan_id for each scan
  - Prevents multiple concurrent scans by checking both memory and database state
  - Automatically cleans up stale active scans on app startup

- **Stats Update Thread**: Added dedicated background thread for UI updates
  - Separate thread reads scan progress from database every second
  - Workers update database directly without UI concerns
  - UI/API endpoints read from synchronized in-memory state
  - Ensures real-time progress updates even during slow file processing

### Fixed
- **Scan Progress Not Updating**: Fixed scan counts not updating until app restart
  - Stats are now read from database by dedicated thread
  - Progress updates show immediately in UI during scans
  - File counts update in real-time as workers process files

- **"Scan Completed" False Positive**: Fixed completion message showing at wrong times
  - Only shows completion when files were actually processed
  - Checks actual processed file count before showing message
  - No false completion on page refresh or when no scan is running

- **Concurrent Scan Prevention**: Enhanced scan concurrency checking
  - Checks both in-memory state and database state
  - Prevents scan button spam from starting multiple scans
  - Shows proper error when scan is already in progress

### Changed
- **Scan State Architecture**: Refactored to use database-backed state management
  - Scan progress persists across page refreshes
  - State survives app restarts (marked as interrupted)
  - More reliable progress tracking for long-running scans

## [1.32] - 2025-07-12

### Fixed
- **Progress Tracking During Slow Scans**: Fixed progress bar not updating during hash calculation
  - Reduced batch size from 10,000 to 100 files for more frequent progress updates
  - Added logging when each file scan completes
  - Fixed state tracking to properly maintain phase information
  - Added completion logging to track scan progress
  - Smaller batches ensure progress updates even when individual files take long to scan

### Changed
- **Batch Processing**: Reduced scan batch size for better responsiveness
  - Large video files (20-40GB) can take minutes to hash
  - Smaller batches allow progress updates between batches
  - Improves UI responsiveness during scans

## [1.31] - 2025-07-12

### Fixed
- **Scan State Persistence**: Fixed critical issues with scan state management
  - Fixed early returns in scan thread that left state inconsistent
  - Added proper state updates when Phase 2 is skipped (no new files)
  - Fixed thread_active flag flipping between true/false during scans
  - Added scan duration tracking for ETA calculations
  - Added better exception handling with full traceback logging
  - Added debug logging to track state updates
  - Fixed scan state not being maintained when no new files are discovered

## [1.30] - 2025-07-12

### Fixed
- **Progress Bar Tracking**: Fixed progress bar not updating during scans
  - Implemented proper 3-phase progress tracking (33% per phase)
  - Phase 1: Discovery - Shows files being checked and new media files found
  - Phase 2: Adding to Database - Shows files being added with percentage
  - Phase 3: Scanning - Shows individual file scan progress from 66% to 100%
  - Fixed thread-safe state updates using update_scan_state function
  - Added proper phase_number tracking for accurate progress calculation
  - Enhanced logging for debugging scan state issues

## [1.29] - 2025-07-12

### Added
- **Page Number Navigation**: Added numbered page links to pagination for quicker navigation
  - Shows up to 7 page numbers at a time with smart ellipsis for large page counts
  - Current page is highlighted in blue
  - First and last page always visible with ellipsis when needed
  - Responsive design adjusts page link size on mobile devices
  - Page links only show when there are multiple pages and not in "All" view

### Fixed
- **WebP False Positives**: Fixed WebP files being incorrectly marked as corrupted
  - WebP files with "invalid TIFF header in Exif data" are now treated as warnings
  - These are metadata issues that don't affect image display
  - FFmpeg EXIF warnings no longer mark WebP files as corrupted
  - Created fix script for existing false positives: `fix_webp_exif_false_positives.py`

## [1.28] - 2025-07-12

### Fixed
- **GIF False Positives**: Fixed GIF files being incorrectly marked as corrupted
  - Added warning support to image scanning (previously only videos had warnings)
  - GIF files with "improper image header" errors are now marked as warnings instead of corrupted
  - GIF files with "cannot identify image file" errors from PIL are marked as warnings if FFmpeg validates them
  - These GIFs often have non-standard headers but still play correctly in browsers
  - Matches the pattern of NAL unit warnings for videos - technical issues that don't prevent playback

### Changed
- **Image Scanning Enhancement**: Image scanning now returns warning details like video scanning
  - Enables consistent warning handling across all media types
  - Future false positive patterns can be converted to warnings without database changes

## [1.27] - 2025-07-12

### Fixed
- **Corrupted Filter UI Issue**: Fixed corrupted filter showing files with warnings
  - Updated filter logic to exclude files where `has_warnings = True`
  - Only files that are corrupted without warnings now appear in "Corrupted Only" filter
  - Fixed stats calculation to exclude warning files from corrupted count

- **System Stats Performance**: Fixed system-info endpoint timeout issues
  - Replaced individual database queries with single aggregated query
  - Fixed loading ALL completed scans (497,977 records) into memory
  - Used SQL AVG() function instead of Python calculation for average scan time
  - Added execution time logging and timeout protection

- **Path Counting Performance**: Fixed expensive filesystem operations in system-info
  - Replaced filesystem path checking with database aggregation
  - Used CASE statements to group files by base path efficiently
  - All file counts now come from database as requested by user

### Added
- **NAL Warning Conversion Script**: Created comprehensive script to convert FFmpeg NAL errors to warnings
  - `tools/fix_all_nal_warnings.py` handles all variations of NAL unit errors
  - Supports dry run mode to preview changes before execution
  - Converts existing corrupted NAL errors to warning status

## [1.26] - 2025-07-11

### Fixed
- **UI Column Display Issues**:
  - Increased Tool column width from 7% to 10% to fully display "imagemagick" without truncation
  - Adjusted Corruption Details column width from 15% to 12% to compensate
  - Made Corruption Details column sortable - users can now sort by corruption details text
  
### Added
- **ImageMagick UTF-8 Utility Scripts**:
  - `tools/fix_imagemagick_utf8_errors.py` - Converts UTF-8 decode false positives to warnings
  - `tools/reset_imagemagick_utf8_files.py` - Resets UTF-8 error files for rescanning
  - These handle cases where images contain binary metadata that can't be decoded as UTF-8

### Fixed
- **ImageMagick UTF-8 Decode Error**: Fixed "'utf-8' codec can't decode byte" error when processing images with binary metadata
  - Added `encoding='utf-8'` and `errors='replace'` parameters to subprocess.run for ImageMagick identify command
  - Binary metadata in images no longer causes crashes during scanning
  - Invalid UTF-8 bytes are replaced with � character, allowing scan to continue
  - This is a warning-level issue, not actual file corruption

## [1.25] - 2025-07-11

### Fixed
- **Database Resilience for Long-Running Scans**:
  - Added automatic database connection refresh every 500 files to prevent stale connections
  - Implemented connection recovery on commit failures with automatic retry
  - Added detailed error logging to diagnose database issues
  - Fixed "database is locked" errors during extended scanning sessions

### Improved
- **SQLite Configuration Enhancements**:
  - Enabled WAL (Write-Ahead Logging) mode for better concurrent access
  - Increased busy timeout to 30 seconds to handle lock contention
  - Set PRAGMA optimizations for improved performance
  - Better isolation level settings for concurrent operations

### Added
- **New Utility Scripts**:
  - `tools/fix_nal_warnings.py` - Directly converts NAL corrupted files to warnings without rescanning
  - `tools/reset_nal_files_v2.py` - Improved reset script with retry logic and lock handling
  - `tools/reset_nal_files_direct.sh` - Shell script for direct SQLite operations when Python fails

## [1.24] - 2025-07-11

### Fixed
- **UI Color Visibility Issues**:
  - Fixed pending items count showing white text on white background in light mode
    - Changed pending items color from white (#ffffff) to purple (#9b59b6) for better visibility
    - Avoided orange color which is already used for warning files
  - Fixed progress bar text being hard to read in dark mode
    - Added white text color (#ecf0f1) for progress bar text in dark mode
    - Added dark text shadow for better contrast against varying progress bar backgrounds

## [1.23] - 2025-07-11

### Fixed
- **Long-Running Scan Database Issues**: Fixed database commits failing after hours of scanning
  - Added periodic database connection refresh every 500 files
  - Implemented automatic connection recovery on commit failures
  - Increased database timeout from 15 to 30 seconds
  - Added better error logging with error types and details
  
### Improved
- **SQLite Configuration**: Enhanced SQLite settings for better concurrency
  - Enabled WAL (Write-Ahead Logging) mode for better concurrent access
  - Set busy timeout to 30 seconds to handle lock contention
  - Changed isolation level to DEFERRED for improved concurrency
  - Added PRAGMA optimizations for better performance
  
### Technical
- **Database Resilience**: Better handling of long-running operations
  - Connection pool now tests connections before use (pool_pre_ping)
  - Automatic session cleanup and engine disposal on errors
  - Connection recovery attempts with verification queries
  - More detailed error logging for debugging database issues

## [1.22] - 2025-07-11

### Fixed
- **Critical Database Commit Issue**: Fixed scan results not being saved to database
  - Added explicit `db.session.flush()` calls after updating records to ensure changes are persisted
  - Added verification logging to confirm database updates are working
  - Enhanced debugging to track database state during scan processing
  - Files that were being scanned but not showing in counts are now properly saved
  
### Technical
- **Database Session Management**: Improved SQLAlchemy session handling
  - Explicit flushes ensure pending changes are written to database
  - Better logging to track commit operations and verify data persistence
  - Added database state verification after commits

## [1.21] - 2025-07-10

### Fixed
- **Thread-Safe Scan State Updates**: Fixed scan progress not showing during parallel scanning
  - Added thread lock for scan_state dictionary to prevent race conditions
  - Created thread-safe update_scan_state() and get_scan_state_copy() functions
  - Progress updates from parallel scan threads now properly persist
  - Scan status endpoint now reads state safely without data corruption
  
### Technical
- **Concurrency Improvements**: Better handling of shared state in multi-threaded scanning
  - Scan state updates are now atomic operations
  - Progress callbacks use proper locking mechanism
  - Prevents lost updates when multiple threads modify scan state simultaneously

## [1.20] - 2025-07-10

### Fixed
- **Progress Bar False Positives**: Fixed progress bar showing when no scan is actually running
  - Progress bar only shows when `is_scanning` is true from backend
  - No longer shows progress just because there are pending files
  - Backend only reports `is_scanning: true` when scan thread is active
  
### Improved  
- **Progress Bar Text Size**: Increased font size from 14px to 18px for better readability
- **Cleaner Progress States**: Replaced multiple "Initializing" states with clearer phase names
  - Changed generic "Initializing" to "Phase 1 of 3: Starting"
  - More descriptive phase indicators throughout scan process
  - Consistent messaging when scan is preparing

## [1.19] - 2025-07-10

### Fixed
- **Progress Bar Persistence**: Fixed progress bar disappearing during active scans
  - Progress bar now stays visible throughout ALL phases until completely done
  - Added double-check in `hideProgress()` to verify scan is truly complete
  - Progress bar only hides when `is_scanning=false` AND `pending_files=0` AND `scanning_files=0`
  - Better error handling - progress bar won't hide if status check fails
  
### Improved
- **Scan Detection Logic**: More robust scan detection
  - Considers a scan active if `is_scanning` OR there are pending/scanning files
  - Prevents false "not scanning" detection between phases
  - Increased monitoring frequency from 3 seconds to 1 second for more responsive updates
  - Only clears monitoring if progress bar was actually visible (prevents false completions)
  - Extended completion display time from 10 to 30 seconds

## [1.18] - 2025-07-10

### Enhanced
- **Phase-Specific Progress Tracking**: Completely redesigned scan progress display
  - Now shows three distinct phases: Discovery, Adding to Database, and Scanning
  - Each phase has its own progress tracking (e.g., "Phase 1 of 3: Discovery")
  - Discovery phase shows files checked and media files found
  - Database addition phase shows exact progress (e.g., "Added 450/1000 files")
  - Scanning phase shows file-by-file progress with percentage
- **Clearer Progress Messages**: Users now understand exactly what's happening
  - No more confusing "100% complete" when scan is just starting
  - Progress bar percentage reflects current phase progress, not overall
  - Phase indicators clearly show which step of the process is running
- **Better Progress Calculation**: 
  - Discovery phase uses indeterminate progress since total is unknown
  - Database addition and scanning phases show accurate percentages
  - Progress messages include specific details for each phase

### Fixed
- **Misleading Progress Display**: Fixed issue where progress showed "100% complete" during discovery
  - Progress bar now accurately reflects the current phase status
  - Discovery phase no longer shows 100% when no files have been scanned yet

## [1.17] - 2025-07-10

### Fixed
- **Progress Bar 100/100 Bug**: Fixed incorrect "100/100 completed" display when scan is just starting
  - Progress bar no longer shows 100% when there are 0 files discovered
  - When total files is 0, progress shows "Calculating..." instead of 100/100
  - Progress bar text shows actual status message when initializing
  - Only shows "Scan completed!" if files were actually scanned
- **False Completion Messages**: Prevented "Scan completed!" from showing on page load
  - Completion message only shows if total files > 0
  - Progress monitoring no longer triggers false completion when no scan is running

### Improved
- **Progress Calculation**: Better handling of edge cases
  - Completed files calculation now properly handles 0 total files
  - Progress bar width correctly shows 0% when initializing
  - More accurate progress display during scan initialization phase

## [1.16] - 2025-07-10

### Fixed
- **Persistent Progress Bar**: Progress bar now properly persists across page reloads
  - When you close and reopen the page, if a scan is running, the progress bar shows immediately
  - `hideProgress()` now checks if scan is still running before hiding
  - Progress monitoring automatically resumes on page load if scan is active
- **Better Word Wrapping**: Fixed file path wrapping without zero-width spaces
  - Changed to `word-break: break-all` for better path breaking
  - Removed zero-width space insertion that would cause copy/paste issues
  - Paths now break naturally without interfering with clipboard operations

### Enhanced
- **Page Load Behavior**: Page now properly detects and displays active scans on load
  - Added logging for scan detection on page load
  - Ensures monitoring continues for in-progress scans

## [1.15] - 2025-07-10

### Fixed
- **File Path Wrapping**: Properly fixed word wrapping issues
  - Added zero-width spaces after slashes to allow natural line breaks
  - Prevents awkward breaks like `/originals/` appearing on its own line
  - File paths now wrap cleanly without breaking directory names
- **Progress Bar Persistence**: Fixed progress bar disappearing too quickly
  - Progress bar now stays visible for 10 seconds after scan completion
  - Only shows "Scan completed!" if actually monitoring a scan (not on initial load)
  - Prevents confusing "Scan completed!" message when starting a new scan

### Changed
- **Button Text**: Changed "Scan All" to "Start Scan" for clarity
- **Button Color**: Made the Start Scan button green (#27ae60) for better visibility
- **Status Logic**: Improved scan completion detection to avoid false completion messages

### Enhanced
- **Scan Progress Visibility**: Improved scan progress display to show immediately when scan starts
  - Progress now shows "Initializing scan..." immediately when clicking "Scan All"
  - Added detailed progress messages during path validation phase
  - Shows "Validating scan paths..." with file counts for each path
  - Displays "Found X files already in database" during discovery
  - Shows "Adding X new files to database..." with progress updates
  - Progress message is updated from backend throughout all scan phases
  - Fixed issue where progress bar wouldn't appear until scan was already running

## [1.13] - 2025-07-10

### Added
- **Dynamic Version Display**: Version number now read from central `version.py` file
  - No more hardcoded versions in multiple places
  - Easier version management for releases
- **GitHub Repository Link**: Added link to GitHub repo in System Statistics modal
  - Direct link to https://github.com/ttlequals0/PixelProbe
  - Helps users find documentation and report issues

### Changed
- **UI Color Fix**: Changed pending scan count from orange to white
  - Orange was confusing as it matched the warning badge color
  - White provides better visual distinction

### Developer Experience
- **Version Management**: Created `version.py` for centralized version control
  - Single source of truth for application version
  - Imported in app.py for all version references

### Improved
- **Enhanced Scan Progress Display**: More detailed and persistent scan progress
  - Progress bar now shows phase, percentage, duration, and ETA inside the bar
  - Progress display remains visible throughout entire scan
  - Shows "Complete" status for 3 seconds before hiding
  - Taller progress bar (50px) for better visibility
  - Real-time updates of scan phase (Discovering Files, Scanning Files)
  - Progress no longer disappears immediately when checking scan status

## [1.12] - 2025-07-10

### Fixed
- **CRITICAL DATABASE FIX**: Fixed missing warning columns causing application crash
  - Removed duplicate `migrate_database` function that was missing warning column definitions
  - Added `has_warnings` and `warning_details` columns to database migration
  - The duplicate function was preventing proper database migration on startup
- **Added reset script**: Created `reset_nal_files_for_rescan.py` to reset NAL-only files
  - Allows existing files with NAL errors to be rescanned with new warning logic
  - Files will be properly marked with warnings instead of corrupted on rescan

## [1.11] - 2025-07-10

### Added
- **Warning State Support**: Complete implementation for tracking files with warnings (not corrupted but have issues)
  - Added `has_warnings` and `warning_details` fields to database schema
  - Migration script `add_warning_columns.py` to update existing databases
  - NAL unit errors without other issues now create warnings instead of marking files as corrupted
  - Warning filter button in UI to show only files with warnings
  - Orange warning badge displayed for files with warnings
  - Warning count added to main stats grid and system statistics modal
  - Updated scan result processing to handle warning states
- **Version Display**: Added application version display in system statistics popup
  - Version now shown at top of stats modal
  - Version matches Docker image version for consistency

### Changed
- **Mark as Good Enhancement**: Files marked as good now have `is_corrupted` set to `False` in database
  - This ensures marked files are properly counted as healthy in statistics
  - Improves consistency in database state management

### Removed
- **UI Cleanup**: Removed "Files Marked as Good" field from statistics popup
  - Redundant since marked files are now counted as healthy
  - Simplifies the statistics display

### Fixed
- **False Positive Reduction**: Major improvements to corruption detection accuracy
  - Removed incorrect PIL tile data check that was marking valid images as corrupted
    - After img.load(), tile data is consumed and cleared in PIL - this is normal behavior
  - ImageMagick CorruptImageProfile warnings (XMP metadata) no longer mark images as corrupted
    - These are common in thumbnails and processed images
  - FFmpeg NAL unit errors alone no longer mark videos as corrupted (now warnings)
    - NAL unit errors often indicate container/muxing issues, not actual corruption
    - Videos with only NAL unit errors that still process successfully are marked with warnings
  - Added scripts to fix existing false positives:
    - `fix_tile_data_false_positives.py` - Re-scans affected files with fixed detection
    - `fix_tile_data_sql.py` - Direct SQL updates for faster processing
    - `fix_imagemagick_profile_warnings.py` - Fixes ImageMagick profile warning false positives
    - `fix_nal_unit_false_positives.py` - Fixes FFmpeg NAL unit false positives

## [1.10] - 2025-07-10

### Fixed
- Removed incorrect PIL tile data check that was causing false positives for image corruption
  - After img.load(), tile data is consumed and cleared in PIL - this is normal behavior
  - This was incorrectly marking valid images as having "Image has no tile data"
- Added scripts to fix existing false positives in the database:
  - `fix_tile_data_false_positives.py` - Re-scans affected files with fixed detection
  - `fix_tile_data_sql.py` - Direct SQL updates for faster processing

## [1.09] - 2025-07-09

### Fixed
- **🔧 SCAN LOGIC FIX**: Fixed scan terminating when no new files are discovered
  - Removed early return when no new files are found
  - Scan now continues to Phase 3 to process existing pending files
  - Fixes issue where scan would stop if all files were already in database as pending

## [1.08] - 2025-07-09

### Fixed
- **⚡ BATCH PROCESSING**: Implemented batch processing to avoid system overload
  - Files are now processed in batches of 10,000 instead of all at once
  - Prevents memory issues when scanning 1M+ files
  - Each batch completes before the next one starts
  - Maintains real-time updates within each batch

## [1.07] - 2025-07-09

### Fixed
- **🔧 REAL-TIME SCAN UPDATES**: Fixed scan results not updating in real-time
  - Replaced batch processing approach with real-time result processing
  - Scan results are now saved to database immediately as each file completes
  - Database commits happen every 50 files (configurable via BATCH_COMMIT_SIZE)
  - UI will now show live updates of completed/corrupted/healthy files during scanning
  - Removed the wait for all files to complete before processing results

## [1.06] - 2025-07-09

### Fixed
- **🔧 APPLICATION CONTEXT ERROR**: Fixed "Working outside of application context" error in progress callback
  - Progress callback was running in thread pool without Flask app context
  - Added `with app.app_context():` wrapper around database operations in progress callback
  - This fixes the database updates not being saved during parallel scanning

## [1.05] - 2025-07-09

### Fixed
- **🔧 DATABASE UPDATE BUG**: Fixed critical issue where scanned files were not being saved to database
  - Added error logging when scanned files are not found in database (should not happen)
  - Added fallback logic to create new records if files are missing from database
  - Improved progress callback to log warnings when files are not found
  - Added debug logging to track scan result processing
  - Fixed batch commit size to use configured value instead of hardcoded 50
  - Added logging to show number of scan results received from parallel scan
  - **CRITICAL FIX**: Added app context to progress callback to fix "Working outside of application context" error
    - This was preventing database updates during parallel scanning
    - Progress callback now properly wraps database operations in app.app_context()

## [1.04] - 2025-07-09

### Fixed
- **🔧 SCAN STATE SYNCHRONIZATION**: Fixed UI not reflecting real-time scan progress
  - Fixed synchronization issue between background scan threads and UI state tracking
  - Added `scan_thread_active` flag to properly track active scan threads
  - Modified scan-status endpoint to check both database state and thread state
  - Added proper file status updates during scanning (marks files as 'scanning' when processed)
  - Fixed scan state reset timing to only occur after all scan processing is complete
  - Improved error handling in scan result processing loop
  - UI now properly shows scan progress and file counts update in real-time

## [1.03] - 2025-07-09

### Fixed
- **🔧 RESET FUNCTION REPAIR**: Fixed reset-for-rescan endpoint not working with bulk operations
  - Fixed batch size calculation that was trying to process all 1M+ records in a single batch
  - Replaced inefficient record loading with proper bulk SQL updates using `synchronize_session=False`
  - Added proper batch processing that processes records in configurable batches (default 500)
  - Fixed memory issues by using query offsets instead of loading all records into memory
  - Added detailed logging showing batch progress and number of records updated per batch
  - Reset operations now properly show progress logs like "Reset batch 1 (500/1042632) - updated 500 records"

## [1.02] - 2025-07-09

### Added
- **🎨 PIXELPROBE LOGO INTEGRATION**: Added new PixelProbe logo and favicon
  - Created favicon files (16x16, 32x32, ICO format) from the PixelProbe logo
  - Updated Flask routes to serve the new favicon files
  - Added logo image to main header section of web interface
  - Maintained fallback to dynamically generated favicons if files are missing
  - Logo positioned above the main title with responsive design and drop shadow effects

- **⚡ PERFORMANCE OPTIMIZATIONS**: Major performance improvements for large datasets
  - Added automatic database indexing on startup for optimal query performance
  - Implemented scan output truncation to prevent memory issues (max 100 lines, 5000 chars)
  - Added configurable performance settings via environment variables:
    - `BATCH_COMMIT_SIZE` - Control commit frequency (default: 50)
    - `RESET_BATCH_SIZE` - Control reset batch size (default: 500)
  - Created performance tuning documentation (`PERFORMANCE_TUNING.md`)
  - Added database migration script (`create_indexes.py`) for existing installations

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

- **⚡ PERFORMANCE IMPROVEMENTS**: Optimized database operations and memory usage
  - Replaced individual record updates with bulk database operations for reset functions
  - Reduced batch sizes from 1000 to 500 for better memory management
  - Implemented automatic database indexing on frequently queried columns
  - Fixed memory issues causing worker process kills during large resets
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