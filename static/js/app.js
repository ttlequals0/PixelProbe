// PixelProbe Modern UI JavaScript

// HTML Escaping Utility to prevent XSS attacks
function escapeHtml(text) {
    if (text == null) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

// Theme Management
class ThemeManager {
    constructor() {
        this.theme = localStorage.getItem('theme') || 'light';
        this.init();
    }

    init() {
        this.applyTheme();
        this.bindEvents();
    }

    applyTheme() {
        document.body.classList.toggle('dark-mode', this.theme === 'dark');
        this.updateToggleUI();
    }

    toggle() {
        this.theme = this.theme === 'light' ? 'dark' : 'light';
        localStorage.setItem('theme', this.theme);
        this.applyTheme();
    }

    updateToggleUI() {
        const toggle = document.querySelector('#theme-toggle');
        if (toggle) {
            toggle.checked = this.theme === 'dark';
        }
        
        const icon = document.querySelector('.theme-icon');
        if (icon) {
            icon.className = `theme-icon fas fa-${this.theme === 'dark' ? 'moon' : 'sun'}`;
        }
    }

    bindEvents() {
        const toggle = document.querySelector('#theme-toggle');
        if (toggle) {
            toggle.addEventListener('change', () => this.toggle());
        }
    }
}

// Sidebar Management
class SidebarManager {
    constructor() {
        this.sidebar = document.querySelector('.sidebar');
        this.overlay = document.querySelector('.sidebar-overlay');
        this.toggleBtn = document.querySelector('.sidebar-toggle-btn');
        this.init();
    }

    init() {
        // Single toggle button handles both mobile and desktop
        if (this.toggleBtn) {
            this.toggleBtn.addEventListener('click', () => {
                if (window.innerWidth <= 768) {
                    this.toggleMobile();
                } else {
                    this.toggleDesktop();
                }
            });
        }
        
        if (this.overlay) {
            this.overlay.addEventListener('click', () => this.closeMobile());
        }

        // Close sidebar on navigation item click (mobile)
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', () => {
                if (window.innerWidth <= 768) {
                    this.closeMobile();
                }
            });
        });
        
        // Restore desktop sidebar state from localStorage
        const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
        if (isCollapsed && window.innerWidth > 768) {
            this.sidebar?.classList.add('collapsed');
            this.updateToggleIcon(true);
        }
        
        // Allow clicking sidebar header to expand when collapsed
        const sidebarHeader = document.querySelector('.sidebar-header');
        if (sidebarHeader) {
            sidebarHeader.addEventListener('click', () => {
                if (this.sidebar?.classList.contains('collapsed') && window.innerWidth > 768) {
                    this.toggleDesktop();
                }
            });
        }
    }

    toggleMobile() {
        this.sidebar?.classList.toggle('active');
        this.overlay?.classList.toggle('active');
        document.body.style.overflow = this.sidebar?.classList.contains('active') ? 'hidden' : '';
    }

    closeMobile() {
        this.sidebar?.classList.remove('active');
        this.overlay?.classList.remove('active');
        document.body.style.overflow = '';
    }
    
    toggleDesktop() {
        this.sidebar?.classList.toggle('collapsed');
        
        // Save state to localStorage
        const isCollapsed = this.sidebar?.classList.contains('collapsed');
        localStorage.setItem('sidebarCollapsed', isCollapsed);
        
        // Update button icon
        this.updateToggleIcon(isCollapsed);
    }
    
    updateToggleIcon(isCollapsed) {
        if (this.toggleBtn && window.innerWidth > 768) {
            const icon = this.toggleBtn.querySelector('i');
            if (icon) {
                icon.className = isCollapsed ? 'fas fa-angles-right' : 'fas fa-bars';
            }
            this.toggleBtn.title = isCollapsed ? 'Expand sidebar' : 'Collapse sidebar';
        }
    }
}

// API Client
class APIClient {
    constructor() {
        this.baseURL = '/api';
    }

    async request(endpoint, options = {}) {
        try {
            const headers = { ...options.headers };

            // Only add Content-Type for requests with body
            if (options.body) {
                headers['Content-Type'] = 'application/json';
            }

            const response = await fetch(`${this.baseURL}${endpoint}`, {
                headers,
                credentials: 'same-origin',
                ...options
            });

            // Handle authentication failures
            if (response.status === 401 || response.status === 403) {
                // Redirect to login if not authenticated
                window.location.href = '/login';
                return null;
            }

            if (!response.ok) {
                // Surface the server's reason (e.g. "Celery workers not available")
                let detail = '';
                try {
                    const data = await response.json();
                    if (data && data.error) {
                        detail = `: ${data.error}`;
                    }
                } catch (parseError) {
                    // Non-JSON error body
                }
                throw new Error(`HTTP error! status: ${response.status}${detail}`);
            }

            return await response.json();
        } catch (error) {
            // Handle network/connection errors silently for stats updates
            if (endpoint === '/stats' || endpoint === '/system-info') {
                return null;
            }
            throw error;
        }
    }

    // Stats methods
    async getStats() {
        return this.request('/stats');
    }

    async getSystemInfo() {
        return this.request('/system-info');
    }

    async getTrends(days = 30) {
        return this.request(`/trends?days=${days}`);
    }

    async getDurationHistogram(days = 30, buckets = 10) {
        return this.request(`/stats/duration-histogram?days=${days}&buckets=${buckets}`);
    }

    // Scan methods
    async getScanResults(params = {}) {
        const queryString = new URLSearchParams(params).toString();
        return this.request(`/scan-results${queryString ? '?' + queryString : ''}`);
    }

    async getScanStatus() {
        return this.request('/scan-status');
    }

    async startScan() {
        return this.request('/scan', {
            method: 'POST',
            body: JSON.stringify({})
        });
    }

    async scanFile(fileId) {
        return this.request('/scan-file', {
            method: 'POST',
            body: JSON.stringify({ file_id: fileId })
        });
    }

    // File operations
    async markAsGood(fileIds) {
        return this.request('/mark-as-good', {
            method: 'POST',
            body: JSON.stringify({ file_ids: fileIds })
        });
    }

    async resetForRescan(resetType = 'all') {
        return this.request('/reset-for-rescan', {
            method: 'POST',
            body: JSON.stringify({ reset_type: resetType })
        });
    }

    async cleanupOrphaned() {
        return this.request('/cleanup-orphaned', {
            method: 'POST',
            body: JSON.stringify({})
        });
    }

    async checkFileChanges() {
        return this.request('/file-changes', {
            method: 'POST',
            body: JSON.stringify({})
        });
    }

    async getCleanupStatus() {
        return this.request('/cleanup-status');
    }

    async getFileChangesStatus() {
        return this.request('/file-changes-status');
    }

    // Cancel operations
    async cancelScan() {
        try {
            const result = await this.request('/cancel-scan', {
                method: 'POST',
                body: JSON.stringify({})
            });
            return result;
        } catch (error) {
            throw error;
        }
    }

    async cancelCleanup() {
        return this.request('/cancel-cleanup', {
            method: 'POST',
            body: JSON.stringify({})
        });
    }

    async cancelFileChanges() {
        return this.request('/cancel-file-changes', {
            method: 'POST',
            body: JSON.stringify({})
        });
    }

    // Export
    async exportCSV(fileIds = null) {
        return this.request('/export', {
            method: 'POST',
            body: JSON.stringify({
                format: 'csv',
                file_ids: fileIds
            })
        });
    }

    // System
    async getVersion() {
        return this.request('/version');
    }
}

// Stats Dashboard
class StatsDashboard {
    constructor(apiClient) {
        this.api = apiClient;
        this.refreshInterval = null;
    }

    async init() {
        await this.updateStats();
        this.startAutoRefresh();
    }

    async updateStats() {
        try {
            const stats = await this.api.getStats();
            if (stats) {
                this.renderStats(stats);
            }
        } catch (error) {
            // Silently handle stats update failures (likely during server restart)
        }
    }

    renderStats(stats) {
        // Update stat cards
        // Show completed files as total so math adds up: healthy + corrupted + warnings = total
        this.updateStatCard('total-files', stats.completed_files);
        this.updateStatCard('healthy-files', stats.healthy_files);
        this.updateStatCard('corrupted-files', stats.corrupted_files);
        this.updateStatCard('warning-files', stats.warning_files || 0);
        this.updateStatCard('pending-files', stats.pending_files);
        this.updateStatCard('scanning-files', stats.scanning_files);
    }

    updateStatCard(id, value) {
        const element = document.querySelector(`#${id}`);
        if (element) {
            element.textContent = value.toLocaleString();
        }
    }

    startAutoRefresh() {
        // Refresh every 30 seconds instead of 5 seconds to reduce server load
        this.refreshInterval = setInterval(() => this.updateStats(), 30000);
    }

    stopAutoRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }
}

// Progress Manager
class ProgressManager {
    constructor(apiClient, app = null) {
        this.api = apiClient;
        this.app = app;
        this.progressBar = document.querySelector('.progress-bar');
        this.progressText = document.querySelector('.progress-text');
        this.progressContainer = document.querySelector('.progress-container');
        this.checkInterval = null;
        this.operationType = 'scan'; // 'scan', 'cleanup', or 'file-changes'
    }

    show() {
        if (this.progressContainer) {
            this.progressContainer.style.display = 'block';
            
            // Update progress title based on operation type
            const progressTitle = this.progressContainer.querySelector('.progress-title');
            if (progressTitle) {
                if (this.operationType === 'scan') {
                    progressTitle.textContent = 'Scan Progress';
                } else if (this.operationType === 'cleanup') {
                    progressTitle.textContent = 'Cleanup Progress';
                } else if (this.operationType === 'file-changes') {
                    progressTitle.textContent = 'Integrity Scan Progress';
                }
            }
            
            // Show cancel button
            const cancelButton = this.progressContainer.querySelector('.cancel-button');
            if (cancelButton) {
                cancelButton.style.display = 'flex';
            }
        }
    }

    hide() {
        if (this.progressContainer) {
            this.progressContainer.style.display = 'none';
            
            // Hide cancel button
            const cancelButton = this.progressContainer.querySelector('.cancel-button');
            if (cancelButton) {
                cancelButton.style.display = 'none';
            }
        }
    }

    update(percentage, text, details = '', isStuck = false) {
        if (this.progressBar) {
            this.progressBar.style.width = `${percentage}%`;
        }
        if (this.progressText) {
            // Show percentage in the progress bar
            this.progressText.textContent = `${percentage}%`;
        }
        const progressDetails = document.querySelector('.progress-details');
        if (progressDetails) {
            // Show both the main text and details if available
            let detailsText = '';
            if (details) {
                detailsText = `${text} - ${details}`;
            } else if (text) {
                detailsText = text;
            }
            
            // Add recovery button if scan is stuck
            if (isStuck && this.operationType === 'scan') {
                progressDetails.innerHTML = `
                    <div>${escapeHtml(detailsText)}</div>
                    <div style="margin-top: 10px;">
                        <button class="btn btn-warning" onclick="app.recoverStuckScan()">
                            <i class="fas fa-wrench"></i> Recover Stuck Scan
                        </button>
                    </div>
                `;
            } else {
                progressDetails.textContent = detailsText;
            }

            // Render per-worker chunk progress grid if available
            if (this._lastScanStatus && this._lastScanStatus.chunks && this._lastScanStatus.chunks.length > 0) {
                this._renderWorkerGrid(progressDetails, this._lastScanStatus.chunks);
            }
        }
    }

    _renderWorkerGrid(container, chunks) {
        let grid = container.querySelector('.worker-grid-container');
        if (!grid) {
            grid = document.createElement('div');
            grid.className = 'worker-grid-container';

            const toggle = document.createElement('button');
            toggle.className = 'worker-grid-toggle';
            toggle.addEventListener('click', () => {
                const gridEl = grid.querySelector('.worker-grid');
                const expanded = gridEl.style.display !== 'none';
                gridEl.style.display = expanded ? 'none' : 'block';
                this._workersExpanded = !expanded;
                this._updateToggleText(grid, chunks);
            });
            grid.appendChild(toggle);

            const gridEl = document.createElement('div');
            gridEl.className = 'worker-grid';
            gridEl.style.display = 'none';
            grid.appendChild(gridEl);

            container.appendChild(grid);
        }

        this._updateToggleText(grid, chunks);

        const gridEl = grid.querySelector('.worker-grid');
        if (this._workersExpanded) {
            gridEl.style.display = 'block';
        }

        const processing = chunks.filter(c => c.status === 'processing');
        const completed = chunks.filter(c => c.status === 'completed');
        const errors = chunks.filter(c => c.status === 'error');
        const sorted = [...processing, ...errors, ...completed].slice(0, 20);

        // Clear and rebuild with safe DOM methods
        gridEl.textContent = '';
        for (const chunk of sorted) {
            const pct = chunk.files_total > 0 ? Math.round((chunk.files_scanned / chunk.files_total) * 100) : 0;

            const row = document.createElement('div');
            row.className = 'worker-row ' + chunk.status;

            const icon = document.createElement('span');
            icon.className = 'worker-icon ' + chunk.status;
            icon.textContent = chunk.status === 'completed' ? '\u2713' : chunk.status === 'error' ? '\u2717' : '\u2022';
            row.appendChild(icon);

            let dirLabel = chunk.directory;
            let fullPath = chunk.directory;
            try {
                const meta = JSON.parse(chunk.directory);
                if (meta.f) {
                    fullPath = meta.f;
                    const parts = meta.f.split('/');
                    dirLabel = parts.length > 3 ? '.../' + parts.slice(-3).join('/') : meta.f;
                }
            } catch (e) {
                const parts = chunk.directory.split('/');
                dirLabel = parts.length > 3 ? '.../' + parts.slice(-3).join('/') : chunk.directory;
            }

            const path = document.createElement('span');
            path.className = 'worker-path';
            path.textContent = dirLabel;
            path.title = fullPath;
            row.appendChild(path);

            const count = document.createElement('span');
            count.className = 'worker-count';
            count.textContent = chunk.files_scanned + '/' + chunk.files_total;
            row.appendChild(count);

            const barBg = document.createElement('div');
            barBg.className = 'worker-bar-bg';
            const bar = document.createElement('div');
            bar.className = 'worker-bar';
            bar.style.width = pct + '%';
            barBg.appendChild(bar);
            row.appendChild(barBg);

            gridEl.appendChild(row);
        }
    }

    _updateToggleText(grid, chunks) {
        const toggle = grid.querySelector('.worker-grid-toggle');
        const processing = chunks.filter(c => c.status === 'processing').length;
        const completed = chunks.filter(c => c.status === 'completed').length;
        const arrow = this._workersExpanded ? '\u25B2' : '\u25BC';
        toggle.textContent = arrow + ' ' + processing + ' active, ' + completed + ' done';
    }

    async checkProgress() {
        try {
            let status;
            let isRunning = false;
            
            // Get status based on operation type
            if (this.operationType === 'scan') {
                status = await this.api.getScanStatus();
                this._lastScanStatus = status;
                // Use is_active from database state as primary indicator
                const isActive = status.is_active !== undefined ? status.is_active : (status.phase === 'scanning' || status.phase === 'discovering' || status.phase === 'adding');
                isRunning = status.is_scanning || status.is_running || isActive;
                
                // Check for stuck scan - has progress but not running AND progress hasn't changed
                // Only check for stuck if database says not active AND service says not running
                const isStuck = !isActive && !status.is_running && status.phase === 'scanning' && status.current > 0;
                if (isStuck) {
                    // Check if progress has actually changed since last check
                    const lastProgress = this._lastProgress || {};
                    const progressChanged = lastProgress.current !== status.current || 
                                          lastProgress.file !== status.file;
                    
                    if (progressChanged) {
                        // Progress is still changing, not actually stuck
                        this._stuckCounter = 0;
                    } else {
                        // Progress hasn't changed, increment stuck counter
                        this._stuckCounter = (this._stuckCounter || 0) + 1;
                    }
                    
                    // Only consider it stuck if progress hasn't changed for multiple checks
                    const reallyStuck = this._stuckCounter >= 5; // 5 seconds of no progress
                    
                    if (reallyStuck) {
                        // Treat stuck scan as still running so UI shows progress
                        isRunning = true;
                        // Add stuck indicator to progress message
                        status.progress_message = '⚠️ SCAN STUCK: ' + (status.progress_message || 'Scan appears to have stopped unexpectedly');
                        status._isStuck = true; // Add flag to pass to update method
                    }
                    
                    // Store current progress for next check
                    this._lastProgress = {
                        current: status.current,
                        file: status.file
                    };
                } else {
                    // Not stuck - clear counters
                    this._stuckCounter = 0;
                    this._lastProgress = {
                        current: status.current,
                        file: status.file
                    };
                }

                // Track progress value for exponential backoff polling
                this.lastProgressValue = status.current || 0;
            } else if (this.operationType === 'cleanup') {
                status = await this.api.getCleanupStatus();
                isRunning = status.is_running;
            } else if (this.operationType === 'file-changes') {
                status = await this.api.getFileChangesStatus();
                isRunning = status.is_running;
            }
            
            if (status) {
                if (isRunning) {
                    const progress = this.calculateProgress(status, this.operationType);
                    this.update(progress.percentage, progress.text, progress.details, status._isStuck || false);
                } else if (status.phase === 'complete' || status.phase === 'completed' ||
                          status.phase === 'cancelled' || status.phase === 'error' ||
                          status.status === 'completed') {
                    // Ignore stale completed status for 15s after user starts a scan
                    // (the API returns the previous scan's status before the new one initializes)
                    const recentStart = this._scanStartedAt && (Date.now() - this._scanStartedAt) < 15000;
                    if (recentStart && !isRunning) {
                        this.update(0, 'Starting scan...', '', false);
                    } else {
                        this.complete(this.operationType, status);
                    }
                } else {
                    // Still showing last progress state
                    const progress = this.calculateProgress(status, this.operationType);
                    this.update(progress.percentage, progress.text, progress.details, status._isStuck || false);
                }
            }
        } catch (error) {
        }
    }

    async startMonitoring(operationType = 'scan') {
        this.operationType = operationType;
        this.show();
        
        // Update button states based on operation type
        if (operationType === 'scan') {
            this.updateScanButtons(true);
        } else if (operationType === 'cleanup') {
            this.updateCleanupButton(true);
        } else if (operationType === 'file-changes') {
            this.updateFileChangesButton(true);
        }
        
        // If user just started a scan, show placeholder and delay first poll
        // to give the Celery task time to create the ScanState
        if (this._scanStartedAt && (Date.now() - this._scanStartedAt) < 5000) {
            this.update(0, 'Starting scan...', '', false);
            await new Promise(r => setTimeout(r, 3000));
        }
        await this.checkProgress();

        // Implement exponential backoff polling (P1 performance optimization)
        // Start at 1 second, backoff to max 5 seconds if no changes
        this.pollDelay = 1000;
        this.lastProgressValue = null;

        const pollWithBackoff = async () => {
            const previousProgress = this.lastProgressValue;
            await this.checkProgress();

            // If progress changed, reset to fast polling
            // Otherwise, increase delay with exponential backoff
            const currentProgress = this.lastProgressValue;
            if (currentProgress !== previousProgress && currentProgress !== null) {
                this.pollDelay = 1000; // Reset to 1 second on change
            } else {
                // Exponential backoff: increase by 1.5x, max 5 seconds
                this.pollDelay = Math.min(this.pollDelay * 1.5, 5000);
            }

            this.checkInterval = setTimeout(pollWithBackoff, this.pollDelay);
        };

        this.checkInterval = setTimeout(pollWithBackoff, this.pollDelay);
    }
    
    updateCleanupButton(isRunning) {
        const cleanupButton = document.querySelector('[onclick*="cleanupOrphaned"]');
        if (cleanupButton) {
            cleanupButton.disabled = isRunning;
            cleanupButton.innerHTML = isRunning ?
                '<i class="fas fa-spinner fa-spin"></i> Cleaning up...' :
                '<i class="fas fa-broom"></i> Cleanup';
        }
    }
    
    updateFileChangesButton(isRunning) {
        const fileChangesButton = document.querySelector('[onclick*="checkFileChanges"]');
        if (fileChangesButton) {
            fileChangesButton.disabled = isRunning;
            fileChangesButton.innerHTML = isRunning ?
                '<i class="fas fa-spinner fa-spin"></i> Checking...' :
                '<i class="fas fa-shield-alt"></i> Integrity Check';
        }
    }
    
    updateScanButtons(isScanning) {
        // Update all Start Scan buttons
        const scanButtons = document.querySelectorAll('[onclick*="startScan"]');
        scanButtons.forEach(button => {
            button.disabled = isScanning;
            if (isScanning) {
                button.classList.add('disabled');
                button.style.opacity = '0.5';
                button.style.cursor = 'not-allowed';
            } else {
                button.classList.remove('disabled');
                button.style.opacity = '';
                button.style.cursor = '';
            }
        });
    }

    stopMonitoring() {
        if (this.checkInterval) {
            clearInterval(this.checkInterval);
            this.checkInterval = null;
        }
        this._lastScanStatus = null;
        this._workersExpanded = false;
    }

    calculateProgress(status, operationType = 'scan') {
        let percentage = 0;
        let text = '';
        let details = '';
        let eta = '';
        
        // Calculate ETA if we have timing data
        // Prefer backend-provided ETA and speed
        if (status.eta && status.eta !== 'None' && status.eta !== null) {
            const etaDate = new Date(status.eta);
            const now = new Date();
            const remainingMs = etaDate - now;
            
            if (remainingMs > 0) {
                const remainingSeconds = Math.floor(remainingMs / 1000);
                eta = this.formatTime(remainingSeconds);
            } else {
                // ETA is in the past, don't show it
                eta = '';
            }
        } else if (status.start_time && status.current > 0 && status.total > 0) {
            // Fallback to client-side calculation
            const startTime = new Date(status.start_time).getTime();
            const currentTime = new Date().getTime();
            const elapsedMs = currentTime - startTime;
            const elapsedSeconds = elapsedMs / 1000;
            
            // Calculate rate and remaining time
            const itemsProcessed = status.current;
            const itemsRemaining = status.total - status.current;
            const rate = itemsProcessed / elapsedSeconds; // items per second
            
            if (rate > 0) {
                const remainingSeconds = itemsRemaining / rate;
                eta = this.formatTime(remainingSeconds);
            }
        }
        
        if (operationType === 'scan') {
            // 3-phase progress tracking for scans
            const phaseNumber = status.phase_number || 1;
            const totalPhases = status.total_phases || 3;
            const phaseCurrent = status.phase_current || 0;
            const phaseTotal = status.phase_total || 0;
            
            // Special handling for completed scans
            if (status.phase === 'completed' || status.status === 'completed') {
                percentage = 100;
                text = status.progress_message || 'Scan completed';
            } else {
                // Calculate percentage based on phase
                const phasePercentage = 100 / totalPhases;
                const phaseStart = (phaseNumber - 1) * phasePercentage;
                
                if (phaseTotal > 0) {
                    const phaseProgress = (phaseCurrent / phaseTotal) * phasePercentage;
                    percentage = Math.round(phaseStart + phaseProgress);
                } else {
                    percentage = Math.round(phaseStart);
                }
                
                text = status.progress_message || `Phase ${phaseNumber} of ${totalPhases}`;
                
                // The backend now includes all progress details in the progress_message
                // No need to build additional details string
                details = '';
            }
        } else if (operationType === 'cleanup') {
            // Use the progress percentage directly from the backend
            // Backend handles 3-phase weighting: scanning → checking → deleting
            percentage = Math.round(status.progress_percentage || 0);
            
            text = status.progress_message || `Phase ${status.phase_number || 1} of ${status.total_phases || 3}`;
            
            const parts = [];
            
            // Add file count for cleanup
            if (status.current > 0 && status.total > 0) {
                parts.push(`${status.current.toLocaleString()} of ${status.total.toLocaleString()} files`);
            }
            
            if (status.current_file) {
                if (status.phase === 'deleting_entries') {
                    parts.push(status.current_file);
                } else {
                    parts.push(`Checking: ${status.current_file.split('/').pop()}`);
                }
            }
            
            // Don't append orphan count here - it's already in the progress_message from backend
            // This prevents duplicate display like "121 orphaned found - Found 121 orphaned files"
            
            // Add ETA or stuck warning
            if (eta) {
                parts.push(`ETA: ${eta}`);
            } else if (status.is_running && status.files_per_second === 0 && status.current > 0) {
                // Only warn if scan has made no progress (0 files/sec) after starting
                parts.push(`Processing...`);
            }
            
            details = parts.join(' - ');
            
        } else if (operationType === 'file-changes') {
            // Calculate percentage from phase information if backend doesn't provide it
            if (status.progress_percentage !== undefined) {
                percentage = Math.round(status.progress_percentage);
            } else if (status.phase_total > 0) {
                // Calculate from phase progress
                const phaseNumber = status.phase_number || 1;
                const totalPhases = status.total_phases || 3;
                const phasePercentage = 100 / totalPhases;
                const phaseStart = (phaseNumber - 1) * phasePercentage;
                const phaseProgress = (status.phase_current / status.phase_total) * phasePercentage;
                percentage = Math.round(phaseStart + phaseProgress);
            } else {
                percentage = 0;
            }

            text = status.progress_message || 'Checking file integrity...';

            const parts = [];

            // Add file count - use phase_current/phase_total or files_processed/total_files
            const current = status.phase_current || status.files_processed || status.current || 0;
            const total = status.phase_total || status.total_files || status.total || 0;
            if (current > 0 || total > 0) {
                parts.push(`${current.toLocaleString()} of ${total.toLocaleString()} files`);
            }
            
            if (status.current_file) {
                parts.push(`Checking: ${status.current_file.split('/').pop()}`);
            }
            
            if (status.changes_found > 0) {
                parts.push(`Found ${status.changes_found} changed files`);
            }
            
            // Add ETA or stuck warning
            if (eta) {
                parts.push(`ETA: ${eta}`);
            } else if (status.is_running && status.files_per_second === 0 && status.current > 0) {
                // Only warn if scan has made no progress (0 files/sec) after starting
                parts.push(`Processing...`);
            }
            
            details = parts.join(' - ');
        }
        
        return { percentage, text, details };
    }

    formatTime(seconds) {
        // Handle edge cases
        if (seconds <= 0) {
            return 'calculating...';
        }
        
        // For very short times, show "Less than 1 minute"
        if (seconds < 60) {
            return 'Less than 1 minute';
        }
        
        const hours = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        
        if (hours > 0) {
            if (hours > 24) {
                const days = Math.floor(hours / 24);
                const remainingHours = hours % 24;
                return `${days}d ${remainingHours}h`;
            }
            return `${hours}h ${mins}m`;
        } else if (mins > 0) {
            // Don't show seconds for times over 5 minutes for cleaner display
            if (mins >= 5) {
                return `${mins}m`;
            }
            return `${mins}m ${secs}s`;
        } else {
            return 'Less than 1 minute';
        }
    }

    async complete(operationType = 'scan', status = null) {
        // Always show 100% when operation completes
        let completionMessage = '';
        
        // Debug log the status on completion
        
        // Handle cancelled operations
        if (status?.phase === 'cancelled') {
            this.stopMonitoring();
            this.hide();
            
            if (operationType === 'scan') {
                this.updateScanButtons(false);
                this.app.showNotification('Scan cancelled', 'info');
            } else if (operationType === 'cleanup') {
                this.updateCleanupButton(false);
                this.app.showNotification('Cleanup cancelled', 'info');
            } else if (operationType === 'file-changes') {
                this.updateFileChangesButton(false);
                this.app.showNotification('Integrity scan cancelled', 'info');
            }
            
            // Refresh stats to update UI
            if (this.app) {
                await this.app.stats.updateStats();
            }
            return;
        }
        
        // Handle completed operations
        if (operationType === 'scan') {
            completionMessage = 'Scan completed!';
            this.updateScanButtons(false); // Re-enable scan buttons
        } else if (operationType === 'cleanup') {
            const deletedCount = status?.orphaned_found || 0;
            completionMessage = `Cleanup completed! Removed ${deletedCount} orphaned records.`;
            this.updateCleanupButton(false); // Re-enable cleanup button
        } else if (operationType === 'file-changes') {
            const changesFound = status?.changes_found || 0;
            completionMessage = `Integrity scan completed! Found ${changesFound} changed files.`;
            this.updateFileChangesButton(false); // Re-enable file changes button
            
            // Show results if any changes were found
            if (status?.result && changesFound > 0) {
                this.showFileChangesResults(status.result);
            }
        }
        
        this.update(100, completionMessage);
        this.stopMonitoring();
        
        // For scan completion, reload the page after a short delay
        if (operationType === 'scan') {
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        } else {
            // For other operations, refresh data and hide progress bar
            if (this.app) {
                await this.app.stats.updateStats();
                
                // Only reload table for cleanup operations
                if (operationType === 'cleanup') {
                    await this.app.table.loadData();
                }
            }
            
            setTimeout(() => this.hide(), 5000);
        }
    }
    
    showFileChangesResults(result) {
        // Show file changes in a modal or alert
        const changedFiles = result.changed_files || [];
        if (changedFiles.length > 0) {
            let message = `Found ${changedFiles.length} changed files:\n\n`;
            changedFiles.forEach(file => {
                message += `${file.file_path} - ${file.change_type}\n`;
            });
            alert(message);
        }
    }
}

// Table Manager
class TableManager {
    constructor(apiClient) {
        this.api = apiClient;
        this.currentPage = 1;
        this.itemsPerPage = 50;
        this.sortField = 'scan_date';
        this.sortOrder = 'desc';
        this.filter = 'all';
        this.searchQuery = '';
        this.pathFilter = '';
        this.selectedFiles = new Set();
        this.lastClickedCheckbox = null; // Track last clicked checkbox for shift-select
    }

    async init() {
        // Read initial value from select element
        const perPageSelect = document.querySelector('#items-per-page');
        if (perPageSelect) {
            const value = perPageSelect.value;
            this.itemsPerPage = value === 'all' ? -1 : parseInt(value);
        }
        
        this.bindEvents();
        await this.loadData();
        
        // Handle window resize
        window.addEventListener('resize', () => {
            this.loadData();
        });
    }

    bindEvents() {
        // Pagination
        document.querySelectorAll('[data-page]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const page = e.target.dataset.page;
                if (page === 'prev') this.currentPage--;
                else if (page === 'next') this.currentPage++;
                else this.currentPage = parseInt(page);
                this.loadData();
            });
        });

        // Items per page
        const perPageSelect = document.querySelector('#items-per-page');
        if (perPageSelect) {
            perPageSelect.addEventListener('change', (e) => {
                this.itemsPerPage = e.target.value === 'all' ? -1 : parseInt(e.target.value);
                this.currentPage = 1;
                this.loadData();
            });
        }

        // Sort headers
        document.querySelectorAll('th[data-sort]').forEach(header => {
            header.addEventListener('click', (e) => {
                const field = e.target.dataset.sort;
                if (this.sortField === field) {
                    this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                } else {
                    this.sortField = field;
                    this.sortOrder = 'desc';
                }
                this.loadData();
            });
        });

        // Filter buttons
        document.querySelectorAll('[data-filter]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                // Remove active class from all buttons
                document.querySelectorAll('[data-filter]').forEach(b => b.classList.remove('active'));
                // Add active class to clicked button
                e.target.classList.add('active');
                
                // Clear selections when filter changes
                this.selectedFiles.clear();
                const selectAllCheckbox = document.querySelector('#select-all');
                if (selectAllCheckbox) selectAllCheckbox.checked = false;
                this.updateSelectionUI();
                
                this.filter = e.target.dataset.filter;
                this.currentPage = 1;
                this.loadData();
            });
        });

        // Path filter
        const pathFilter = document.querySelector('#path-filter');
        if (pathFilter) {
            pathFilter.addEventListener('change', (e) => {
                this.pathFilter = e.target.value;
                this.currentPage = 1;
                this.loadData();
                // Persist selection
                try { localStorage.setItem('pixelprobe_path_filter', e.target.value); } catch (e) {}
            });
            // Restore persisted selection
            try {
                const saved = localStorage.getItem('pixelprobe_path_filter');
                if (saved) {
                    this.pathFilter = saved;
                    pathFilter.value = saved;
                }
            } catch (e) {}
        }

        // Search
        const searchInput = document.querySelector('#search-input');
        if (searchInput) {
            let searchTimeout;
            searchInput.addEventListener('input', (e) => {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(() => {
                    this.searchQuery = e.target.value;
                    this.currentPage = 1;
                    this.loadData();
                }, 300);
            });
        }

        // Select all
        const selectAll = document.querySelector('#select-all');
        if (selectAll) {
            selectAll.addEventListener('change', (e) => {
                const checkboxes = document.querySelectorAll('.file-checkbox');
                checkboxes.forEach(cb => {
                    cb.checked = e.target.checked;
                    if (e.target.checked) {
                        this.selectedFiles.add(parseInt(cb.value));
                    } else {
                        this.selectedFiles.delete(parseInt(cb.value));
                    }
                });
                this.lastClickedCheckbox = null; // Reset last clicked when using select all
                this.updateSelectionUI();
            });
        }
    }

    async loadData() {
        try {
            const params = {
                page: this.currentPage,
                per_page: this.itemsPerPage,
                sort_field: this.sortField,
                sort_order: this.sortOrder
            };

            // Only add search if it has a value
            if (this.searchQuery && this.searchQuery.trim()) {
                params.search = this.searchQuery.trim();
            }

            // Add path filter
            if (this.pathFilter) {
                params.path = this.pathFilter;
            }

            // Map frontend filter values to backend parameters
            if (this.filter) {
                switch (this.filter) {
                    case 'corrupted':
                        params.is_corrupted = 'true';
                        break;
                    case 'healthy':
                        params.is_corrupted = 'false';
                        params.has_warnings = 'false';
                        break;
                    case 'warning':
                        params.has_warnings = 'true';
                        break;
                    case 'all':
                    default:
                        params.is_corrupted = 'all';
                        params.scan_status = 'all';
                        break;
                }
            }

            const data = await this.api.getScanResults(params);
            this.renderTable(data);
            this.updatePagination(data);
        } catch (error) {
        }
    }

    renderTable(data) {
        // Check if mobile
        const isMobile = window.innerWidth <= 768;
        
        if (isMobile) {
            this.renderMobileCards(data);
        } else {
            const tbody = document.querySelector('#results-tbody');
            if (!tbody) return;

            tbody.innerHTML = data.results.map(file => this.renderRow(file)).join('');

            // Re-bind checkbox events with shift-select support
            tbody.querySelectorAll('.file-checkbox').forEach(cb => {
                cb.addEventListener('click', (e) => {
                    this.handleCheckboxClick(e, data.results);
                });
            });
        }
    }

    renderMobileCards(data) {
        let container = document.querySelector('.mobile-results');
        if (!container) {
            // Create mobile results container if it doesn't exist
            const tableContainer = document.querySelector('.table-container');
            if (!tableContainer) return;

            const mobileContainer = document.createElement('div');
            mobileContainer.className = 'mobile-results';
            tableContainer.parentNode.insertBefore(mobileContainer, tableContainer.nextSibling);
            container = mobileContainer;
        }

        container.innerHTML = data.results.map(file => this.renderMobileCard(file)).join('');

        // Re-bind checkbox events for mobile with shift-select support
        container.querySelectorAll('.file-checkbox').forEach(cb => {
            cb.addEventListener('click', (e) => {
                this.handleCheckboxClick(e, data.results);
            });
        });
    }

    renderMobileCard(file) {
        const statusClass = file.marked_as_good ? 'success' : (file.is_corrupted ? 'danger' : (file.has_warnings ? 'warning' : 'success'));
        const statusText = file.marked_as_good ? 'HEALTHY' : (file.is_corrupted ? 'CORRUPTED' : (file.has_warnings ? 'WARNING' : 'HEALTHY'));
        
        return `
            <div class="result-card">
                <div class="badge badge-${statusClass}">${statusText}</div>
                <div class="file-path">${this.escapeHtml(file.file_path)}</div>
                <div class="file-info">
                    <span>${this.formatFileSize(file.file_size)}</span>
                    <span>${file.file_type || 'Unknown'}</span>
                </div>
                <div class="file-details">
                    <span class="label">Tool:</span>
                    <span class="value">${file.scan_tool || 'N/A'}</span>
                    <span class="label">Scanned:</span>
                    <span class="value">${this.formatDate(file.scan_date)}</span>
                    ${file.last_integrity_check_date ? `
                        <span class="label">Last Integrity Check:</span>
                        <span class="value">${this.formatDate(file.last_integrity_check_date)}</span>
                    ` : ''}
                    ${file.corruption_details || file.scan_output || file.error_message || file.warning_details ? `
                        <span class="label">Details:</span>
                        <span class="value">${this.escapeHtml(file.corruption_details || file.scan_output || file.error_message || file.warning_details || '')}</span>
                    ` : ''}
                </div>
                <div class="action-buttons">
                    <button class="btn btn-secondary" onclick="app.viewFile(${file.id})" title="View File">
                        <i class="fas fa-eye"></i><span class="btn-text"> View</span>
                    </button>
                    <!-- Individual File Actions Dropdown for Mobile -->
                    <div class="action-dropdown">
                        <button class="btn btn-secondary" type="button"
                                onclick="app.toggleActionDropdown(event, 'mobile-file-action-menu-${file.id}')" title="Actions">
                            <i class="fas fa-tasks"></i><span class="btn-text"> Actions</span> <i class="fas fa-caret-down"></i>
                        </button>
                        <ul class="dropdown-menu" id="mobile-file-action-menu-${file.id}" style="display: none;">
                            <li><a class="dropdown-item" href="#" onclick="app.rescanFile(${file.id}); return false;">
                                <i class="fas fa-sync"></i> Rescan
                            </a></li>
                            <li><a class="dropdown-item" href="#" onclick="app.orphanCheckFile(${file.id}); return false;">
                                <i class="fas fa-search"></i> Cleanup
                            </a></li>
                            <li><a class="dropdown-item" href="#" onclick="app.changeCheckFile(${file.id}); return false;">
                                <i class="fas fa-shield-alt"></i> Integrity Check
                            </a></li>
                        </ul>
                    </div>
                    ${file.corruption_details || file.scan_output || file.error_message || file.warning_details ? `
                        <button class="btn btn-secondary" onclick="app.viewScanOutput(${file.id})" title="View Details">
                            <i class="fas fa-file-alt"></i><span class="btn-text"> Details</span>
                        </button>
                    ` : ''}
                    <button class="btn btn-secondary" onclick="app.downloadFile(${file.id})" title="Download">
                        <i class="fas fa-download"></i><span class="btn-text"> Download</span>
                    </button>
                    <button class="btn btn-primary" onclick="app.markFileAsGood(${file.id})" title="Mark as Good">
                        <i class="fas fa-check"></i><span class="btn-text"> Mark Good</span>
                    </button>
                </div>
                <input type="checkbox" class="file-checkbox" value="${file.id}" ${this.selectedFiles.has(file.id) ? 'checked' : ''}>
            </div>
        `;
    }

    renderRow(file) {
        const statusClass = file.marked_as_good ? 'success' : (file.is_corrupted ? 'danger' : (file.has_warnings ? 'warning' : 'success'));
        const statusText = file.marked_as_good ? 'Healthy' : (file.is_corrupted ? 'Corrupted' : (file.has_warnings ? 'Warning' : 'Healthy'));
        
        return `
            <tr>
                <td><input type="checkbox" class="file-checkbox" value="${file.id}" ${this.selectedFiles.has(file.id) ? 'checked' : ''}></td>
                <td><span class="badge badge-${statusClass}">${statusText}</span></td>
                <td class="file-path-cell" title="${this.escapeHtml(file.file_path)}">${this.escapeHtml(file.file_path)}</td>
                <td>${this.formatFileSize(file.file_size)}</td>
                <td>${file.file_type || 'N/A'}</td>
                <td>${file.scan_tool || 'N/A'}</td>
                <td class="text-truncate" title="${this.escapeHtml(file.corruption_details || file.scan_output || file.error_message || file.warning_details || '')}">${this.escapeHtml(file.corruption_details || file.scan_output || file.error_message || file.warning_details || '')}</td>
                <td>${this.formatDate(file.scan_date)}</td>
                <td class="action-buttons">
                    <button class="btn btn-sm btn-secondary" onclick="app.viewFile(${file.id})">
                        <i class="fas fa-eye"></i> View
                    </button>
                    <!-- Individual File Actions Dropdown -->
                    <div class="action-dropdown">
                        <button class="btn btn-sm btn-secondary" type="button"
                                onclick="app.toggleActionDropdown(event, 'file-action-menu-${file.id}')">
                            <i class="fas fa-tasks"></i> Actions <i class="fas fa-caret-down"></i>
                        </button>
                        <ul class="dropdown-menu" id="file-action-menu-${file.id}" style="display: none;">
                            <li><a class="dropdown-item" href="#" onclick="app.rescanFile(${file.id}); return false;">
                                <i class="fas fa-sync"></i> Rescan
                            </a></li>
                            <li><a class="dropdown-item" href="#" onclick="app.orphanCheckFile(${file.id}); return false;">
                                <i class="fas fa-search"></i> Cleanup
                            </a></li>
                            <li><a class="dropdown-item" href="#" onclick="app.changeCheckFile(${file.id}); return false;">
                                <i class="fas fa-shield-alt"></i> Integrity Check
                            </a></li>
                        </ul>
                    </div>
                    ${file.corruption_details || file.scan_output || file.error_message || file.warning_details ? `
                        <button class="btn btn-sm btn-secondary" onclick="app.viewScanOutput(${file.id})">
                            <i class="fas fa-file-alt"></i> Details
                        </button>
                    ` : ''}
                    <button class="btn btn-sm btn-secondary" onclick="app.downloadFile(${file.id})">
                        <i class="fas fa-download"></i> Download
                    </button>
                    <button class="btn btn-sm btn-primary" onclick="app.markFileAsGood(${file.id})">
                        <i class="fas fa-check"></i> Mark Good
                    </button>
                </td>
            </tr>
        `;
    }

    updatePagination(data) {
        const paginationEl = document.querySelector('.pagination');
        if (!paginationEl) return;

        // Handle "All" case where itemsPerPage is -1
        if (this.itemsPerPage === -1) {
            paginationEl.innerHTML = '';
            return;
        }

        const totalPages = Math.ceil(data.total / this.itemsPerPage);
        const currentPage = this.currentPage;
        
        let html = '';
        
        // Previous button
        html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <a class="page-link" href="#" data-page="prev">Previous</a>
        </li>`;
        
        // Smart pagination for mobile
        const isMobile = window.innerWidth <= 768;
        
        if (isMobile && totalPages > 5) {
            // Mobile: Show fewer pages to fit screen
            const pages = new Set();
            
            // Always show first page
            pages.add(1);
            
            // Show current page
            pages.add(currentPage);
            
            // Always show last page
            pages.add(totalPages);
            
            // Convert to sorted array
            const pageArray = Array.from(pages).sort((a, b) => a - b);
            
            let lastPage = 0;
            for (const page of pageArray) {
                // Add ellipsis if there's a gap
                if (page - lastPage > 1) {
                    html += `<li class="page-item disabled ellipsis"><span class="page-link">…</span></li>`;
                }
                
                html += `<li class="page-item ${page === currentPage ? 'active' : ''}">
                    <a class="page-link" href="#" data-page="${page}">${page}</a>
                </li>`;
                
                lastPage = page;
            }
        } else {
            // Desktop or few pages: show normal range
            const pageRange = isMobile ? 1 : 2;
            const startPage = Math.max(1, currentPage - pageRange);
            const endPage = Math.min(totalPages, currentPage + pageRange);
            
            if (startPage > 1) {
                html += `<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>`;
                if (startPage > 2) {
                    html += `<li class="page-item disabled ellipsis"><span class="page-link">…</span></li>`;
                }
            }
            
            for (let i = startPage; i <= endPage; i++) {
                html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
                    <a class="page-link" href="#" data-page="${i}">${i}</a>
                </li>`;
            }
            
            if (endPage < totalPages) {
                if (endPage < totalPages - 1) {
                    html += `<li class="page-item disabled ellipsis"><span class="page-link">…</span></li>`;
                }
                html += `<li class="page-item"><a class="page-link" href="#" data-page="${totalPages}">${totalPages.toLocaleString()}</a></li>`;
            }
        }
        
        // Next button
        html += `<li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
            <a class="page-link" href="#" data-page="next">Next</a>
        </li>`;
        
        paginationEl.innerHTML = html;
        
        // Re-bind pagination events
        paginationEl.querySelectorAll('[data-page]').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const page = e.target.dataset.page;
                if (page === 'prev' && this.currentPage > 1) {
                    this.currentPage--;
                    this.loadData();
                } else if (page === 'next' && this.currentPage < totalPages) {
                    this.currentPage++;
                    this.loadData();
                } else if (!isNaN(page)) {
                    this.currentPage = parseInt(page);
                    this.loadData();
                }
            });
        });
    }

    handleCheckboxClick(event, allFiles) {
        const checkbox = event.target;
        const fileId = parseInt(checkbox.value);

        // If shift key is pressed and we have a last clicked checkbox, select range
        if (event.shiftKey && this.lastClickedCheckbox !== null) {
            // Get all checkboxes currently in the DOM
            const allCheckboxes = Array.from(document.querySelectorAll('.file-checkbox'));

            // Find indices of current and last clicked checkboxes
            const currentIndex = allCheckboxes.findIndex(cb => parseInt(cb.value) === fileId);
            const lastIndex = allCheckboxes.findIndex(cb => parseInt(cb.value) === this.lastClickedCheckbox);

            if (currentIndex !== -1 && lastIndex !== -1) {
                // Determine the range
                const startIndex = Math.min(currentIndex, lastIndex);
                const endIndex = Math.max(currentIndex, lastIndex);

                // Select all checkboxes in the range
                const shouldCheck = checkbox.checked;
                for (let i = startIndex; i <= endIndex; i++) {
                    const cb = allCheckboxes[i];
                    cb.checked = shouldCheck;
                    const id = parseInt(cb.value);
                    if (shouldCheck) {
                        this.selectedFiles.add(id);
                    } else {
                        this.selectedFiles.delete(id);
                    }
                }
            }
        } else {
            // Normal checkbox click (no shift key)
            if (checkbox.checked) {
                this.selectedFiles.add(fileId);
            } else {
                this.selectedFiles.delete(fileId);
            }
        }

        // Update last clicked checkbox
        this.lastClickedCheckbox = fileId;

        // Update UI
        this.updateSelectionUI();
    }

    updateSelectionUI() {
        const count = this.selectedFiles.size;
        const selectionInfo = document.querySelector('.selection-info');
        if (selectionInfo) {
            selectionInfo.textContent = count > 0 ? `${count} files selected` : '';
        }

        // Enable/disable bulk action buttons
        const buttons = ['#mark-good-btn', '#deep-scan-btn', '#rescan-btn', '#download-btn', '#bulk-action-btn'];
        buttons.forEach(selector => {
            const btn = document.querySelector(selector);
            if (btn) {
                btn.disabled = count === 0;
            }
        });
    }

    formatFileSize(bytes) {
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        if (bytes === 0) return '0 B';
        const i = Math.floor(Math.log(bytes) / Math.log(1024));
        return Math.round(bytes / Math.pow(1024, i) * 100) / 100 + ' ' + sizes[i];
    }

    formatDate(dateString) {
        if (!dateString) return 'N/A';
        
        // The backend sends datetime in the server's configured timezone WITHOUT timezone info
        // e.g., "2025-08-31T04:25:50" which is already in the server's timezone
        // We need to display it AS-IS without any timezone conversion
        
        // Parse the date components manually to avoid timezone interpretation
        const match = dateString.match(/(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/);
        if (!match) return 'Invalid Date';
        
        const [_, year, month, day, hour, minute, second] = match;
        
        // Format month name
        const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const monthName = monthNames[parseInt(month) - 1];
        
        // Return formatted string without any timezone conversion
        return `${monthName} ${parseInt(day)}, ${year} ${hour}:${minute}:${second}`;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize app
// Log Viewer
class LogViewer {
    static MAX_LOGS = 2000;

    constructor(apiClient) {
        this.api = apiClient;
        this.page = 1;
        this.perPage = 200;
        this.logs = [];
        this.autoRefreshInterval = null;
        this.lastTimestamp = null;
        this.initialized = false;
    }

    init() {
        if (this.initialized) {
            this.loadLogs();
            this.startAutoRefresh();
            return;
        }
        this.initialized = true;

        // Bind filter change events
        ['log-type-filter', 'log-run-filter', 'log-level-filter', 'log-time-filter'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('change', () => { this.page = 1; this.logs = []; this.loadLogs(); });
        });

        // When job type changes, reload the runs dropdown
        const typeFilter = document.getElementById('log-type-filter');
        if (typeFilter) typeFilter.addEventListener('change', () => this.loadRuns());

        // Search with debounce
        const searchInput = document.getElementById('log-search-input');
        if (searchInput) {
            let timeout;
            searchInput.addEventListener('input', () => {
                clearTimeout(timeout);
                timeout = setTimeout(() => { this.page = 1; this.logs = []; this.loadLogs(); }, 300);
            });
        }

        // Auto-refresh toggle
        const autoRefresh = document.getElementById('log-auto-refresh');
        if (autoRefresh) {
            autoRefresh.addEventListener('change', (e) => {
                if (e.target.checked) this.startAutoRefresh();
                else this.stopAutoRefresh();
            });
        }

        // Event delegation for traceback expand/collapse (avoids per-row listeners)
        const tbody = document.getElementById('logs-tbody');
        if (tbody) {
            tbody.addEventListener('click', (e) => {
                const row = e.target.closest('.log-row-expandable');
                if (!row) return;
                const tbRow = row.nextElementSibling;
                if (tbRow && tbRow.classList.contains('log-traceback-row')) {
                    tbRow.style.display = tbRow.style.display === 'none' ? 'table-row' : 'none';
                }
            });
        }

        this.loadRuns();
        this.loadLogs();
        this.startAutoRefresh();
    }

    getFilterParams() {
        const params = {};
        const scanId = document.getElementById('log-run-filter')?.value;
        if (scanId) params.scan_id = scanId;

        const level = document.getElementById('log-level-filter')?.value;
        if (level) params.level = level;

        const search = document.getElementById('log-search-input')?.value?.trim();
        if (search) params.search = search;

        const timeRange = document.getElementById('log-time-filter')?.value;
        if (timeRange) {
            const now = new Date();
            let start;
            switch (timeRange) {
                case '1h': start = new Date(now - 3600000); break;
                case '6h': start = new Date(now - 6 * 3600000); break;
                case '24h': start = new Date(now - 24 * 3600000); break;
                case '7d': start = new Date(now - 7 * 86400000); break;
                case '30d': start = new Date(now - 30 * 86400000); break;
            }
            if (start) params.start_time = start.toISOString();
        }

        return params;
    }

    async loadLogs() {
        try {
            const params = { ...this.getFilterParams(), page: this.page, per_page: this.perPage };
            const qs = new URLSearchParams(params).toString();
            const response = await fetch(`/api/logs?${qs}`);
            const data = await response.json();

            if (this.page === 1) {
                this.logs = data.logs || [];
            } else {
                this.logs = this.logs.concat(data.logs || []).slice(0, LogViewer.MAX_LOGS);
            }

            this.renderLogs();

            // Update count and load-more button
            const countEl = document.getElementById('logs-count');
            if (countEl) countEl.textContent = `Showing ${this.logs.length} of ${data.total} entries`;

            const loadMoreBtn = document.getElementById('logs-load-more');
            if (loadMoreBtn) loadMoreBtn.style.display = data.has_more ? 'inline-block' : 'none';

            // Track last timestamp for polling
            if (this.logs.length > 0) {
                this.lastTimestamp = this.logs[0].timestamp;
            }
        } catch (e) {
            // Silently fail
        }
    }

    async loadMore() {
        this.page++;
        await this.loadLogs();
    }

    async poll() {
        if (!this.lastTimestamp) return;
        // Skip polling when modal is not visible
        const modal = document.getElementById('logs-modal');
        if (!modal || modal.style.display === 'none' || modal.style.display === '') return;

        try {
            const params = { ...this.getFilterParams(), since: this.lastTimestamp };
            const qs = new URLSearchParams(params).toString();
            const response = await fetch(`/api/logs?${qs}`);
            const data = await response.json();

            if (data.logs && data.logs.length > 0) {
                // Prepend new entries and cap at MAX_LOGS
                this.logs = data.logs.concat(this.logs).slice(0, LogViewer.MAX_LOGS);
                this.lastTimestamp = data.logs[0].timestamp;

                // Incremental DOM prepend instead of full rebuild
                const tbody = document.getElementById('logs-tbody');
                if (tbody) {
                    // Remove empty state row if present
                    const emptyRow = tbody.querySelector('.log-table-empty');
                    if (emptyRow) emptyRow.parentElement.remove();

                    const fragment = document.createDocumentFragment();
                    data.logs.forEach(log => this._buildLogRow(log, fragment));
                    tbody.insertBefore(fragment, tbody.firstChild);

                    // Trim excess rows from the end
                    while (tbody.children.length > LogViewer.MAX_LOGS * 2) {
                        tbody.removeChild(tbody.lastChild);
                    }
                }

                const countEl = document.getElementById('logs-count');
                if (countEl) countEl.textContent = `Showing ${this.logs.length} entries`;

                // Auto-scroll to top if auto-refresh is on
                const container = document.getElementById('logs-table-container');
                if (container) container.scrollTop = 0;
            }
        } catch (e) {
            // Silently fail
        }
    }

    startAutoRefresh() {
        this.stopAutoRefresh();
        const autoRefresh = document.getElementById('log-auto-refresh');
        if (autoRefresh && !autoRefresh.checked) return;

        this.autoRefreshInterval = setInterval(() => {
            // Pause polling when tab is hidden
            if (!document.hidden) this.poll();
        }, 3000);
    }

    stopAutoRefresh() {
        if (this.autoRefreshInterval) {
            clearInterval(this.autoRefreshInterval);
            this.autoRefreshInterval = null;
        }
    }

    async loadRuns() {
        try {
            const scanType = document.getElementById('log-type-filter')?.value || '';
            const params = scanType ? `?scan_type=${scanType}` : '';
            const response = await fetch(`/api/logs/runs${params}`);
            const data = await response.json();

            const select = document.getElementById('log-run-filter');
            if (!select) return;

            // Preserve current selection
            const current = select.value;

            // Clear and rebuild options using DOM methods (safe from XSS)
            while (select.options.length > 0) select.remove(0);
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = 'All Runs';
            select.appendChild(defaultOpt);

            (data.runs || []).forEach(run => {
                const opt = document.createElement('option');
                opt.value = run.scan_id;
                const date = run.start_time ? new Date(run.start_time).toLocaleString() : 'Unknown';
                opt.textContent = `${run.scan_id === 'system' ? 'System' : run.scan_id.substring(0, 12)} (${date}) [${run.log_count}]`;
                if (run.scan_id === current) opt.selected = true;
                select.appendChild(opt);
            });
        } catch (e) {
            // Silently fail
        }
    }

    renderLogs() {
        const tbody = document.getElementById('logs-tbody');
        if (!tbody) return;

        // Build log table rows using safe DOM methods
        tbody.textContent = ''; // Clear existing content safely

        if (this.logs.length === 0) {
            const emptyRow = document.createElement('tr');
            const emptyTd = document.createElement('td');
            emptyTd.colSpan = 4;
            emptyTd.className = 'log-table-empty';
            emptyTd.textContent = 'No log entries match the current filters';
            emptyRow.appendChild(emptyTd);
            tbody.appendChild(emptyRow);
            return;
        }

        this.logs.forEach(log => this._buildLogRow(log, tbody));
    }

    _buildLogRow(log, container) {
        const levelClass = this.getLevelClass(log.level);
        const ts = log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '';
        const loggerShort = (log.logger_name || '').split('.').slice(-2).join('.');

        const row = document.createElement('tr');
        row.className = `log-row ${levelClass} ${log.traceback ? 'log-row-expandable' : ''}`;

        const tdTime = document.createElement('td');
        tdTime.className = 'log-time';
        tdTime.textContent = ts;
        row.appendChild(tdTime);

        const tdLevel = document.createElement('td');
        const levelBadge = document.createElement('span');
        levelBadge.className = `log-level-badge ${levelClass}`;
        levelBadge.textContent = log.level;
        tdLevel.appendChild(levelBadge);
        row.appendChild(tdLevel);

        const tdLogger = document.createElement('td');
        tdLogger.className = 'log-logger';
        tdLogger.title = log.logger_name || '';
        tdLogger.textContent = loggerShort;
        row.appendChild(tdLogger);

        const tdMessage = document.createElement('td');
        tdMessage.className = 'log-message';
        tdMessage.textContent = log.message || '';
        row.appendChild(tdMessage);

        container.appendChild(row);

        if (log.traceback) {
            const tbRow = document.createElement('tr');
            tbRow.className = 'log-traceback-row';
            tbRow.style.display = 'none';
            const tbTd = document.createElement('td');
            tbTd.colSpan = 4;
            const pre = document.createElement('pre');
            pre.className = 'log-traceback';
            pre.textContent = log.traceback;
            tbTd.appendChild(pre);
            tbRow.appendChild(tbTd);
            container.appendChild(tbRow);
        }
    }

    getLevelClass(level) {
        switch (level) {
            case 'DEBUG': return 'log-debug';
            case 'INFO': return 'log-info';
            case 'WARNING': return 'log-warning';
            case 'ERROR': return 'log-error';
            case 'CRITICAL': return 'log-critical';
            default: return '';
        }
    }

    downloadLogs() {
        const params = this.getFilterParams();
        const qs = new URLSearchParams(params).toString();
        window.location.href = `/api/logs/download?${qs}`;
    }
}


class PixelProbeApp {
    constructor() {
        this.api = new APIClient();
        this.theme = new ThemeManager();
        this.sidebar = new SidebarManager();
        this.stats = new StatsDashboard(this.api);
        this.progress = new ProgressManager(this.api, this);
        this.table = new TableManager(this.api);
        this.trendsChart = null;
        this.histogramChart = null;
        this.logViewer = new LogViewer(this.api);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    formatScanType(type) {
        const types = {
            'normal': 'Normal Scan',
            'orphan': 'Cleanup',
            'file_changes': 'Integrity Scan'
        };
        return types[type] || type;
    }
    
    handleVideoError(fileId) {
        const video = document.getElementById(`video-player-${fileId}`);
        const errorDiv = document.getElementById(`video-error-${fileId}`);
        
        if (video) {
            // Log detailed error information
            
            video.style.display = 'none';
        }
        
        if (errorDiv) {
            errorDiv.style.display = 'block';
        }
    }

    async init() {
        // Initialize components
        await this.stats.init();
        await this.table.init();

        // Populate path filter dropdown
        this.loadScanPaths();
        
        // Check for ongoing operations
        try {
            // Check for ongoing scan
            const scanStatus = await this.api.getScanStatus();
            
            // Check for active scan (remove stuck detection on page load - it will be handled by monitoring)
            if (scanStatus.is_scanning || scanStatus.is_running || 
                (scanStatus.phase === 'scanning' && scanStatus.current > 0)) {
                this.progress.operationType = 'scan';
                this.progress.startMonitoring('scan');
                return; // Only monitor one operation at a time
            }
            
            // Check for ongoing cleanup
            const cleanupStatus = await this.api.getCleanupStatus();
            if (cleanupStatus.is_running) {
                this.progress.operationType = 'cleanup';
                this.progress.startMonitoring('cleanup');
                return; // Only monitor one operation at a time
            }
            
            // Check for ongoing file changes check
            const fileChangesStatus = await this.api.getFileChangesStatus();
            if (fileChangesStatus.is_running) {
                this.progress.operationType = 'file-changes';
                this.progress.startMonitoring('file-changes');
                return; // Only monitor one operation at a time
            }
        } catch (error) {
        }

        // Start background detection for scheduled scans
        this.startBackgroundScanDetection();
    }

    startBackgroundScanDetection() {
        // Check every 30 seconds for any running operations
        // This catches scheduled scans that start after page load
        this.scanDetectionInterval = setInterval(async () => {
            // Skip if already monitoring an operation
            if (this.progress.checkInterval) {
                return;
            }

            try {
                // Check for any running operations
                const scanStatus = await this.api.getScanStatus();
                if (scanStatus.is_scanning) {
                    this.progress.operationType = 'scan';
                    this.progress.startMonitoring('scan');
                    return;
                }

                const cleanupStatus = await this.api.getCleanupStatus();
                if (cleanupStatus.is_running) {
                    this.progress.operationType = 'cleanup';
                    this.progress.startMonitoring('cleanup');
                    return;
                }

                const fileChangesStatus = await this.api.getFileChangesStatus();
                if (fileChangesStatus.is_running) {
                    this.progress.operationType = 'file-changes';
                    this.progress.startMonitoring('file-changes');
                    return;
                }
            } catch (error) {
                // Silent fail - will retry on next interval
            }
        }, 30000); // 30 seconds
    }

    // Public methods for inline event handlers
    async startScan() {
        try {
            // Check if scan is already running
            const status = await this.api.getScanStatus();
            if (status.is_scanning) {
                this.showNotification('A scan is already in progress', 'warning');
                return;
            }
            
            await this.api.startScan();
            this.progress.operationType = 'scan';
            this.progress._scanStartedAt = Date.now();
            this.progress.startMonitoring('scan');
            this.showNotification('Scan started', 'success');
        } catch (error) {
            const detail = error && error.message ? ` (${error.message})` : '';
            this.showNotification(`Failed to start scan${detail}`, 'error');
        }
    }

    async recoverStuckScan() {
        try {
            this.showNotification('Attempting to recover stuck scan...', 'info');
            const response = await fetch('/api/scan/recovery', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                }
            });

            if (!response.ok) {
                throw new Error(`Failed to recover scan: ${response.statusText}`);
            }

            const result = await response.json();
            this.showNotification(result.message || 'Scan recovered successfully', 'success');

            // Reload the page to reset the UI
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        } catch (error) {
            this.showNotification(error.message || 'Failed to recover scan', 'error');
        }
    }

    async cleanupOrphaned() {
        try {
            // Use custom confirmation modal for better mobile support
            const confirmed = await this.showConfirmModal(
                'Confirm Cleanup',
                'Remove database entries for files that no longer exist on disk?'
            );

            if (!confirmed) {
                return;
            }

        } catch (error) {
            return;
        }

        try {
            const result = await this.api.cleanupOrphaned();
            
            if (result.status === 'started') {
                this.showNotification('Cleanup started...', 'info');
                // Start monitoring cleanup progress
                this.progress.operationType = 'cleanup';
                this.progress.startMonitoring('cleanup');
                
                // Also do a manual check after 1 second to debug
                setTimeout(async () => {
                    try {
                        const status = await this.api.getCleanupStatus();
                    } catch (e) {
                    }
                }, 1000);
            } else {
                // This is the old synchronous response - still handle it
                this.showNotification(`Cleaned up ${result.deleted_count || 0} orphaned entries`, 'success');
                await this.stats.updateStats();
                await this.table.loadData();
            }
        } catch (error) {
            this.showNotification('Failed to cleanup orphaned entries', 'error');
        }
    }

    async checkFileChanges() {
        try {
            const result = await this.api.checkFileChanges();
            
            if (result.status === 'started') {
                this.showNotification('Integrity scan started...', 'info');
                // Start monitoring file changes progress
                this.progress.operationType = 'file-changes';
                this.progress.startMonitoring('file-changes');
            } else {
                // This is the old synchronous response - still handle it
                const changedCount = result.changed_files?.length || 0;
                if (changedCount > 0) {
                    this.showNotification(`Found ${changedCount} files with changes`, 'info');
                } else {
                    this.showNotification('No integrity issues detected', 'success');
                }
            }
        } catch (error) {
            this.showNotification('Failed to perform integrity check', 'error');
        }
    }

    async viewFile(fileId) {
        try {
            const response = await fetch(`/api/scan-results/${fileId}`);
            if (response.ok) {
                const file = await response.json();
                this.showMediaViewerModal(file);
            }
        } catch (error) {
            this.showNotification('Failed to load file', 'error');
        }
    }

    showMediaViewerModal(file) {
        const modal = document.querySelector('#media-viewer-modal');
        if (!modal) return;
        
        const modalBody = modal.querySelector('.modal-body');
        const modalTitle = modal.querySelector('.modal-title');
        
        modalTitle.textContent = file.file_path.split('/').pop();
        
        // Determine file type and create appropriate viewer
        const fileType = file.file_type?.toLowerCase() || '';
        const filePath = file.file_path;
        let content = '';
        
        if (fileType.startsWith('image/')) {
            content = `<img src="/api/view/${file.id}" alt="${this.escapeHtml(filePath)}" style="max-width: 100%; max-height: 60vh; height: auto; object-fit: contain; display: block; margin: 0 auto;">`;
        } else if (fileType.startsWith('video/')) {
            // Match v1.x implementation more closely
            const videoUrl = `/api/view/${file.id}`;
            
            content = `
                <div style="position: relative; width: 100%; max-width: 800px; margin: 0 auto;">
                    <video id="video-player-${file.id}"
                           class="video-player"
                           controls
                           preload="metadata"
                           style="width: 100%; display: block;"
                           onloadedmetadata="this.volume = 1.0;"
                           onerror="app.handleVideoError(${file.id})">
                        <source src="${videoUrl}" type="${fileType}">
                        <source src="${videoUrl}" type="video/mp4">
                        <source src="${videoUrl}" type="video/webm">
                        <source src="${videoUrl}" type="video/ogg">
                        Your browser does not support the video tag.
                    </video>
                    <div id="video-error-${file.id}" style="display: none; padding: 20px; text-align: center; color: #ff6b6b;">
                        <p>Unable to load video. <a href="${videoUrl}" target="_blank">Try opening directly</a></p>
                    </div>
                </div>
            `;
        } else if (fileType.startsWith('audio/')) {
            content = `
                <audio id="audio-player-${file.id}"
                       controls
                       style="width: 100%; display: block; margin: 0 auto;"
                       onloadedmetadata="this.volume = 1.0;">
                    <source src="/api/view/${file.id}" type="${fileType}">
                    Your browser does not support the audio element.
                </audio>
            `;
        } else {
            content = `<p style="text-align: center;">Preview not available for this file type.</p>`;
        }
        
        content += `
            <div style="margin-top: 1rem;">
                <a href="/api/download/${file.id}" class="btn btn-primary" download>
                    <i class="fas fa-download"></i> Download
                </a>
            </div>
        `;
        
        modalBody.innerHTML = content;
        modal.style.display = 'block';
        
        // Setup close handlers
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            closeBtn.onclick = () => modal.style.display = 'none';
        }
        
        // Close on outside click
        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        };
        
    }

    async rescanFile(fileId) {
        // Close all dropdowns first
        this.closeAllDropdowns();

        try {
            // Check if scan is already running
            const status = await this.api.getScanStatus();
            if (status.is_scanning) {
                this.showNotification('A scan is already in progress', 'warning');
                return;
            }

            // Get file path first
            const response = await fetch(`/api/scan-results/${fileId}`);
            if (response.ok) {
                const file = await response.json();
                // Use scan-file endpoint for single file rescanning
                const scanResponse = await this.api.request('/scan-file', {
                    method: 'POST',
                    body: JSON.stringify({
                        file_path: file.file_path  // Single file endpoint expects 'file_path'
                    })
                });
                this.showNotification(scanResponse.message || 'File rescan started', 'success');
                this.progress.startMonitoring();
            } else {
                throw new Error('Failed to get file info');
            }
        } catch (error) {
            this.showNotification(error.message || 'Failed to rescan file', 'error');
        }
    }

    async orphanCheckFile(fileId) {
        // Close all dropdowns first
        this.closeAllDropdowns();

        try {
            // Get file path first
            const response = await fetch(`/api/scan-results/${fileId}`);
            if (response.ok) {
                const file = await response.json();
                // Start cleanup for this file
                const orphanResponse = await fetch('/api/cleanup-orphaned', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        file_paths: [file.file_path]
                    })
                });

                if (orphanResponse.ok) {
                    this.showNotification('Cleanup started for file', 'success');
                    this.progress.startMonitoring('cleanup');
                } else {
                    throw new Error('Failed to start cleanup');
                }
            } else {
                throw new Error('Failed to get file info');
            }
        } catch (error) {
            this.showNotification(error.message || 'Failed to start cleanup', 'error');
        }
    }

    async changeCheckFile(fileId) {
        // Close all dropdowns first
        this.closeAllDropdowns();

        try {
            // Get file path first
            const response = await fetch(`/api/scan-results/${fileId}`);
            if (response.ok) {
                const file = await response.json();
                // Start file changes check for this file
                const changeResponse = await fetch('/api/file-changes', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        file_paths: [file.file_path]
                    })
                });

                if (changeResponse.ok) {
                    this.showNotification('Integrity check started for file', 'success');
                    // Monitor as 'scan' type since we create ScanState for single file integrity checks
                    this.progress.startMonitoring('scan');
                } else {
                    throw new Error('Failed to start integrity check');
                }
            } else {
                throw new Error('Failed to get file info');
            }
        } catch (error) {
            this.showNotification(error.message || 'Failed to start integrity check', 'error');
        }
    }

    async markFileAsGood(fileId) {
        try {
            await this.api.markAsGood([fileId]);
            this.showNotification('File marked as good', 'success');
            await this.table.loadData();
            await this.stats.updateStats(); // Fix: Update stats after marking file as good
        } catch (error) {
            this.showNotification('Failed to mark file as good', 'error');
        }
    }

    async markSelectedAsGood() {
        if (this.table.selectedFiles.size === 0) {
            this.showNotification('No files selected', 'warning');
            return;
        }

        try {
            await this.api.markAsGood(Array.from(this.table.selectedFiles));
            this.showNotification(`${this.table.selectedFiles.size} files marked as good`, 'success');
            this.table.selectedFiles.clear();
            await this.table.loadData();
        } catch (error) {
            this.showNotification('Failed to mark files as good', 'error');
        }
    }

    showNotification(message, type = 'info') {
        // Create notification element
        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = message;

        // Add to page
        document.body.appendChild(notification);

        // Show with animation
        setTimeout(() => notification.classList.add('show'), 10);

        // Remove after 3 seconds
        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }

    showConfirmModal(title, message) {
        return new Promise((resolve) => {
            const modal = document.getElementById('confirm-modal');
            const titleEl = document.getElementById('confirm-title');
            const messageEl = document.getElementById('confirm-message');

            if (!modal || !titleEl || !messageEl) {
                resolve(false);
                return;
            }

            // Set content
            titleEl.textContent = title;
            messageEl.textContent = message;

            // Store the resolve function for later use
            this._confirmResolve = resolve;

            // Setup close button handler
            const closeBtn = modal.querySelector('.modal-close');
            if (closeBtn) {
                closeBtn.onclick = () => this.hideConfirmModal(false);
            }

            // Show modal using same pattern as other modals
            modal.style.display = 'block';
        });
    }

    hideConfirmModal(confirmed) {
        const modal = document.getElementById('confirm-modal');
        if (modal) {
            modal.style.display = 'none';
        }

        // Resolve the promise with the user's choice
        if (this._confirmResolve) {
            this._confirmResolve(confirmed);
            this._confirmResolve = null;
        }
    }

    async showSystemStats() {
        try {
            const info = await this.api.getSystemInfo();
            this.showSystemStatsModal(info);
        } catch (error) {
            this.showNotification('Failed to load system info', 'error');
        }
    }

    async showTrends() {
        try {
            const days = 30;
            const [trendsData, histogramData] = await Promise.all([
                this.api.getTrends(days),
                this.api.getDurationHistogram(days, 10)
            ]);
            this.showTrendsModal(trendsData, histogramData);
        } catch (error) {
            this.showNotification('Failed to load trends data', 'error');
        }
    }

    showTrendsModal(trendsData, histogramData) {
        const modal = document.querySelector('#trends-modal');
        if (!modal) return;

        const modalBody = modal.querySelector('.modal-body');
        if (!modalBody) return;

        // Create HTML structure for charts
        let html = '<div class="trends-dashboard">';

        // Summary stats at the top
        html += '<div class="trends-summary" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem;">';

        if (trendsData.summary) {
            html += `
                <div class="stat-card" style="padding: 1rem; background: var(--card-bg, #f8f9fa); border-radius: 4px;">
                    <div style="font-size: 0.875rem; color: var(--text-muted, #6c757d); margin-bottom: 0.5rem;">Total Scans</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">${trendsData.summary.total_scans || 0}</div>
                </div>
                <div class="stat-card" style="padding: 1rem; background: var(--card-bg, #f8f9fa); border-radius: 4px;">
                    <div style="font-size: 0.875rem; color: var(--text-muted, #6c757d); margin-bottom: 0.5rem;">Files Scanned</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">${(trendsData.summary.total_files_scanned || 0).toLocaleString()}</div>
                </div>
                <div class="stat-card" style="padding: 1rem; background: var(--card-bg, #f8f9fa); border-radius: 4px;">
                    <div style="font-size: 0.875rem; color: var(--text-muted, #6c757d); margin-bottom: 0.5rem;">Corrupted Files</div>
                    <div style="font-size: 1.5rem; font-weight: bold; color: ${(trendsData.summary.total_corrupted || 0) > 0 ? '#dc3545' : '#28a745'};">${trendsData.summary.total_corrupted || 0}</div>
                </div>
                <div class="stat-card" style="padding: 1rem; background: var(--card-bg, #f8f9fa); border-radius: 4px;">
                    <div style="font-size: 0.875rem; color: var(--text-muted, #6c757d); margin-bottom: 0.5rem;">Avg Duration</div>
                    <div style="font-size: 1.5rem; font-weight: bold;">${trendsData.summary.avg_duration ? trendsData.summary.avg_duration.toFixed(1) + 's' : 'N/A'}</div>
                </div>
            `;
        }

        html += '</div>';

        // Scan trends chart
        html += '<div class="chart-container" style="margin-bottom: 2rem;">';
        html += '<h4 style="margin-bottom: 1rem;">Scan Activity (Last 30 Days)</h4>';
        html += '<canvas id="trends-chart" style="max-height: 300px;"></canvas>';
        html += '</div>';

        // Duration histogram chart
        html += '<div class="chart-container" style="margin-bottom: 2rem;">';
        html += '<h4 style="margin-bottom: 1rem;">Scan Duration Distribution</h4>';
        html += '<canvas id="histogram-chart" style="max-height: 300px;"></canvas>';
        html += '</div>';

        // Per scan-type breakdown
        if (histogramData.by_scan_type && Object.keys(histogramData.by_scan_type).length > 0) {
            html += '<div class="scan-type-breakdown">';
            html += '<h4 style="margin-bottom: 1rem;">Duration by Scan Type</h4>';
            html += '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1rem;">';

            for (const [scanType, stats] of Object.entries(histogramData.by_scan_type)) {
                html += `
                    <div class="stat-card" style="padding: 1rem; background: var(--card-bg, #f8f9fa); border-radius: 4px;">
                        <div style="font-size: 0.875rem; font-weight: bold; margin-bottom: 0.5rem;">${scanType.replace('_', ' ').toUpperCase()}</div>
                        <div style="font-size: 0.75rem; color: var(--text-muted, #6c757d);">
                            Count: ${stats.count || 0}<br>
                            Avg: ${stats.avg ? stats.avg.toFixed(1) + 's' : 'N/A'}<br>
                            Min: ${stats.min ? stats.min.toFixed(1) + 's' : 'N/A'} | Max: ${stats.max ? stats.max.toFixed(1) + 's' : 'N/A'}
                        </div>
                    </div>
                `;
            }

            html += '</div></div>';
        }

        html += '</div>';

        modalBody.innerHTML = html;
        this.openModal('trends-modal');

        // Render charts after modal is opened
        this.renderTrendsCharts(trendsData, histogramData);
    }

    renderTrendsCharts(trendsData, histogramData) {
        // Destroy existing chart instances if they exist
        if (this.trendsChart) {
            this.trendsChart.destroy();
        }
        if (this.histogramChart) {
            this.histogramChart.destroy();
        }

        // Render scan trends line chart
        const trendsCanvas = document.getElementById('trends-chart');
        if (trendsCanvas && trendsData.daily_trends) {
            const ctx = trendsCanvas.getContext('2d');
            const dates = trendsData.daily_trends.map(d => d.date);
            const scanCounts = trendsData.daily_trends.map(d => d.scan_count || 0);
            const corruptedCounts = trendsData.daily_trends.map(d => d.corrupted_count || 0);

            this.trendsChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: dates,
                    datasets: [
                        {
                            label: 'Scans',
                            data: scanCounts,
                            borderColor: '#007bff',
                            backgroundColor: 'rgba(0, 123, 255, 0.1)',
                            tension: 0.4,
                            fill: true
                        },
                        {
                            label: 'Corrupted Files',
                            data: corruptedCounts,
                            borderColor: '#dc3545',
                            backgroundColor: 'rgba(220, 53, 69, 0.1)',
                            tension: 0.4,
                            fill: true
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            position: 'top',
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                precision: 0
                            }
                        }
                    }
                }
            });
        }

        // Render duration histogram bar chart
        const histogramCanvas = document.getElementById('histogram-chart');
        if (histogramCanvas && histogramData.histogram) {
            const ctx = histogramCanvas.getContext('2d');
            const labels = histogramData.histogram.map(bucket =>
                `${bucket.min_duration.toFixed(0)}-${bucket.max_duration.toFixed(0)}s`
            );
            const counts = histogramData.histogram.map(bucket => bucket.count || 0);

            this.histogramChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Number of Scans',
                        data: counts,
                        backgroundColor: '#28a745',
                        borderColor: '#1e7e34',
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                precision: 0
                            }
                        }
                    }
                }
            });
        }
    }

    showSystemStatsModal(info) {
        const modal = document.querySelector('#system-stats-modal');
        if (!modal) return;

        const modalBody = modal.querySelector('.modal-body');
        if (!modalBody) return;

        // Format the system info with columns layout
        let html = '<div class="system-stats-content">';
        html += '<div class="stats-columns">';

        // Column 1
        html += '<div class="stats-column">';

        // Database Stats
        if (info.database) {
            html += '<h4>Database Statistics</h4>';
            html += '<div class="stats-section">';
            html += `<p>Total Files: ${info.database.total_files?.toLocaleString() || 0}</p>`;
            // Healthy files already includes marked as good
            html += `<p>Healthy Files: ${info.database.healthy_files?.toLocaleString() || 0}</p>`;
            html += `<p>Corrupted Files: ${info.database.corrupted_files?.toLocaleString() || 0}</p>`;
            html += `<p>Warning Files: ${info.database.warning_files?.toLocaleString() || 0}</p>`;
            html += `<p>Error Files: ${info.database.error_files?.toLocaleString() || 0}</p>`;
            html += '</div>';
        }

        // Scan Performance Stats
        if (info.database && info.database.performance) {
            const perf = info.database.performance;
            html += '<h4>Scan Performance</h4>';
            html += '<div class="stats-section">';
            html += `<p>Total Scans: ${perf.total_scans?.toLocaleString() || 0}</p>`;
            html += `<p>Days Since Last Scan: ${perf.avg_days_since_scan ? parseFloat(perf.avg_days_since_scan).toFixed(1) : 0} days</p>`;
            if (perf.newest_scan) {
                html += `<p>Last Scan: ${new Date(perf.newest_scan).toLocaleString()}</p>`;
            }
            if (perf.oldest_scan) {
                html += `<p>First Scan: ${new Date(perf.oldest_scan).toLocaleString()}</p>`;
            }
            html += '</div>';
        }

        html += '</div>'; // End Column 1

        // Column 2
        html += '<div class="stats-column">';

        // System Information
        if (info.version || info.timezone || info.features) {
            html += '<h4>System Information</h4>';
            html += '<div class="stats-section">';
            if (info.version) {
                html += `<p>Version: ${info.version}</p>`;
            }
            if (info.timezone) {
                html += `<p>Timezone: ${info.timezone}</p>`;
            }
            if (info.current_time) {
                html += `<p>Current Time: ${new Date(info.current_time).toLocaleString()}</p>`;
            }
            html += '</div>';
        }

        // File System Statistics
        if (info.filesystem || info.database) {
            html += '<h4>File System Statistics</h4>';
            html += '<div class="stats-section">';
            const totalFiles = info.filesystem?.total_files || info.database?.total_files || 0;
            const completedFiles = info.database?.completed_files || 0;
            const percentageTracked = totalFiles > 0 ? (100).toFixed(1) : (0).toFixed(1);
            const percentageChecked = totalFiles > 0 ? ((completedFiles / totalFiles) * 100).toFixed(1) : (0).toFixed(1);

            html += `<p>Total Files Found: ${totalFiles.toLocaleString()}</p>`;
            html += `<p>Paths Monitored: ${info.filesystem?.paths_monitored || 0}</p>`;
            html += `<p>Percentage Tracked: ${percentageTracked}%</p>`;
            html += `<p>Percentage Checked: ${percentageChecked}%</p>`;
            html += '</div>';
        }

        // Features
        if (info.features) {
            html += '<h4>Features</h4>';
            html += '<div class="stats-section">';
            Object.entries(info.features).forEach(([key, value]) => {
                const featureName = key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                html += `<p>${featureName}: ${value ? 'Enabled' : 'Disabled'}</p>`;
            });
            html += '</div>';
        }

        html += '</div>'; // End Column 2

        // Column 3 - Monitored Paths (if they exist)
        if (info.monitored_paths && info.monitored_paths.length > 0) {
            html += '<div class="stats-column">';
            html += '<h4>Monitored Paths</h4>';
            html += '<div class="stats-section">';
            info.monitored_paths.forEach(path => {
                html += `<p>${path.path}: ${path.file_count?.toLocaleString() || 0} files`;
                if (!path.exists) html += ' (not accessible)';
                html += '</p>';
            });
            html += '</div>';
            html += '</div>'; // End Column 3
        }

        html += '</div>'; // End stats-columns
        html += '</div>';

        modalBody.innerHTML = html;
        modal.style.display = 'block';

        // Setup period tab switchers
        this.setupTrendTabs();

        // Setup close handlers
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            closeBtn.onclick = () => modal.style.display = 'none';
        }

        // Close on outside click
        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        };
    }

    formatStorage(gb) {
        // Convert GB to TB if >= 1024 GB
        const value = Number(gb);
        if (value >= 1024) {
            return `${(value / 1024).toFixed(2)} TB`;
        }
        return `${value.toFixed(2)} GB`;
    }

    renderTrendsSection(trends) {
        let html = '<div class="trends-section">';
        html += '<h3>Trend Analytics</h3>';

        // Period selector tabs
        html += '<div class="period-tabs">';
        html += '<button class="period-tab active" data-period="30d">30 Days</button>';
        html += '<button class="period-tab" data-period="60d">60 Days</button>';
        html += '<button class="period-tab" data-period="90d">90 Days</button>';
        html += '<button class="period-tab" data-period="1y">1 Year</button>';
        html += '</div>';

        // Trend content for each period
        ['30d', '60d', '90d', '1y'].forEach((period, index) => {
            const periodData = trends.trends[period];
            if (!periodData) return;

            const isActive = index === 0 ? 'active' : '';
            html += `<div class="trend-content ${isActive}" data-period="${period}">`;
            html += '<div class="trends-container">';

            // Column 1: Corruption Trends, Scanning Performance, Overall Summary
            html += '<div class="trend-column column-1">';

            // Corruption Trends
            html += '<h4>Corruption Trends</h4>';
            html += '<div class="stats-section">';
            html += `<p>Corruption Rate: ${Number(periodData.corruption.corruption_rate).toFixed(2)}%</p>`;
            html += `<p>Total Scanned: ${Number(periodData.corruption.total_scanned).toLocaleString()}</p>`;
            html += `<p>Corrupted Files: ${Number(periodData.corruption.corrupted).toLocaleString()}</p>`;
            html += `<p>Files with Warnings: ${Number(periodData.corruption.warnings).toLocaleString()}</p>`;
            if (periodData.corruption.top_corrupted_types && periodData.corruption.top_corrupted_types.length > 0) {
                html += '<p><strong>Top Corrupted Types:</strong></p>';
                html += '<ul style="margin-left: 20px;">';
                periodData.corruption.top_corrupted_types.slice(0, 5).forEach(item => {
                    html += `<li>${item.type}: ${item.count} files</li>`;
                });
                html += '</ul>';
            }
            html += '</div>';

            // Scanning Performance (in same column)
            html += '<h4 style="margin-top: 2rem;">Scanning Performance</h4>';
            html += '<div class="stats-section">';
            html += `<p>File Types Scanned: ${periodData.scanning.unique_file_types}</p>`;
            html += `<p>Avg Scan Duration: ${Number(periodData.scanning.avg_scan_duration).toFixed(2)}s</p>`;
            html += `<p>Files per Day: ${Number(periodData.scanning.files_per_day).toFixed(1)}</p>`;
            html += '</div>';

            // Overall Summary (appears in all period tabs)
            if (trends.summary) {
                html += '<h4 style="margin-top: 2rem;">Overall Summary</h4>';
                html += '<div class="stats-section">';
                html += `<p><strong>Total Storage:</strong> ${this.formatStorage(trends.summary.total_storage_gb)}</p>`;
                html += `<p><strong>Total Files:</strong> ${Number(trends.summary.total_files).toLocaleString()}</p>`;
                html += `<p><strong>Collection Age:</strong> ${trends.summary.collection_age_days} days</p>`;
                html += `<p><strong>Average Growth:</strong> ${this.formatStorage(trends.summary.avg_gb_per_day)}/day</p>`;
                html += '</div>';
            }

            html += '</div>'; // End Column 1

            // Storage Trends Column
            html += '<div class="trend-column storage-trends">';
            html += '<h4>Storage Trends</h4>';
            html += '<div class="stats-section">';
            html += `<p><strong>Total Storage:</strong> ${this.formatStorage(periodData.storage.total_gb)}</p>`;
            html += `<p><strong>Growth Rate:</strong> ${this.formatStorage(periodData.storage.gb_per_day)}/day</p>`;
            html += '<p><strong>Projections:</strong></p>';
            html += `<p class="ml-20">Next 30 days: ${this.formatStorage(periodData.storage.projections.next_30d_gb)}</p>`;
            html += `<p class="ml-20">Next 1 year: ${this.formatStorage(periodData.storage.projections.next_1y_gb)}</p>`;

            // Files by Type section with bar chart
            if (periodData.storage.by_file_type && periodData.storage.by_file_type.length > 0) {
                html += '<div class="file-types-section">';
                html += '<h4 style="margin-top: 20px;">Files by Type (All Types)</h4>';

                // Calculate dynamic height based on number of file types (25px per bar)
                const chartHeight = Math.max(300, periodData.storage.by_file_type.length * 25);

                // Bar chart container with dynamic height
                html += `<div class="chart-container" style="position: relative; height: ${chartHeight}px; width: 100%; margin: 15px 0;">`;
                html += `<canvas id="fileTypeChart-${period}"></canvas>`;
                html += '</div>';

                // Summary stats
                html += '<div class="file-types-summary">';
                html += `<p><strong>Total Types:</strong> ${periodData.storage.by_file_type.length}</p>`;
                html += `<p><strong>Largest:</strong> ${periodData.storage.by_file_type[0].type} - ${this.formatStorage(periodData.storage.by_file_type[0].total_gb)}</p>`;
                if (periodData.storage.by_file_type.length > 1) {
                    const last = periodData.storage.by_file_type[periodData.storage.by_file_type.length - 1];
                    html += `<p><strong>Smallest:</strong> ${last.type} - ${this.formatStorage(last.total_gb)}</p>`;
                }
                html += '</div>';

                // Complete listing of all file types
                html += '<div class="file-types-list">';
                html += '<h5 style="margin: 15px 0 10px 0; color: var(--text-primary);">Complete Listing:</h5>';
                periodData.storage.by_file_type.forEach((item, index) => {
                    html += `<p class="file-type-item"><strong>${index + 1}.</strong> ${item.type}: ${this.formatStorage(item.total_gb)} (${Number(item.file_count).toLocaleString()} files)</p>`;
                });
                html += '</div>';
                html += '</div>';
            }
            html += '</div>';
            html += '</div>'; // End Storage Column

            html += '</div>'; // End trends-container
            html += '</div>'; // End trend-content
        });

        html += '</div>'; // End trends-section
        return html;
    }

    showTrendsModal(trends) {
        const modal = document.querySelector('#trends-modal');
        if (!modal) return;

        const modalBody = modal.querySelector('.modal-body');
        if (!modalBody) return;

        // Store trends data for chart recreation
        this.trendsData = trends;

        // Render trends content
        modalBody.innerHTML = this.renderTrendsSection(trends);

        // Setup tab switching
        this.setupTrendTabs();

        // Create pie charts for all periods
        this.createFileTypeCharts(trends);

        // Show modal
        modal.style.display = 'flex';
    }

    createFileTypeCharts(trends) {
        // Destroy existing charts if any
        if (this.fileTypeCharts) {
            Object.values(this.fileTypeCharts).forEach(chart => chart.destroy());
        }
        this.fileTypeCharts = {};

        // Color palette for charts - cycle through these colors for all file types
        const baseColors = [
            '#00ff88',  // Primary green
            '#ff6b6b',  // Red
            '#4ecdc4',  // Cyan
            '#ffe66d',  // Yellow
            '#a8dadc',  // Light blue
            '#95e1d3',  // Mint
            '#f38181',  // Light red
            '#aa96da',  // Purple
            '#fcbad3',  // Pink
            '#a8e6cf'   // Light green
        ];

        // Helper function to generate colors for any number of items
        const generateColors = (count) => {
            const colors = [];
            for (let i = 0; i < count; i++) {
                colors.push(baseColors[i % baseColors.length]);
            }
            return colors;
        };

        ['30d', '60d', '90d', '1y'].forEach(period => {
            const periodData = trends.trends[period];
            if (!periodData || !periodData.storage.by_file_type) return;

            const canvas = document.getElementById(`fileTypeChart-${period}`);
            if (!canvas) return;

            const ctx = canvas.getContext('2d');
            const data = periodData.storage.by_file_type; // Use ALL file types

            const colors = generateColors(data.length);

            this.fileTypeCharts[period] = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: data.map(item => item.type),
                    datasets: [{
                        label: 'Storage (GB)',
                        data: data.map(item => Number(item.total_gb)),
                        backgroundColor: colors,
                        borderColor: colors,
                        borderWidth: 1
                    }]
                },
                options: {
                    indexAxis: 'y',  // Horizontal bar chart
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: {
                            display: false
                        },
                        tooltip: {
                            callbacks: {
                                label: (context) => {
                                    const value = context.parsed.x || 0;
                                    const fileType = data[context.dataIndex];
                                    return [
                                        `Storage: ${this.formatStorage(value)}`,
                                        `Files: ${Number(fileType.file_count).toLocaleString()}`
                                    ];
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            type: 'logarithmic',  // Logarithmic scale for better visualization
                            beginAtZero: false,
                            ticks: {
                                color: '#b0b0b0',
                                callback: (value) => this.formatStorage(value)
                            },
                            grid: {
                                color: '#333'
                            },
                            title: {
                                display: true,
                                text: 'Storage (Log Scale)',
                                color: '#b0b0b0'
                            }
                        },
                        y: {
                            ticks: {
                                color: '#b0b0b0',
                                font: {
                                    size: 10
                                }
                            },
                            grid: {
                                display: false
                            }
                        }
                    }
                }
            });
        });
    }

    setupTrendTabs() {
        const tabs = document.querySelectorAll('.period-tab');
        const contents = document.querySelectorAll('.trend-content');

        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const period = tab.dataset.period;

                // Update active tab
                tabs.forEach(t => t.classList.remove('active'));
                tab.classList.add('active');

                // Update active content
                contents.forEach(c => {
                    if (c.dataset.period === period) {
                        c.classList.add('active');
                    } else {
                        c.classList.remove('active');
                    }
                });
            });
        });
    }

    async showApiDocs() {
        // Navigate to API documentation page
        window.location.href = '/api-docs';
    }

    async showScanReports() {
        const modal = document.querySelector('#scan-reports-modal');
        if (!modal) return;
        
        // Show modal
        modal.style.display = 'block';
        
        // Load reports
        await this.loadScanReports();
        
        // Setup modal close handlers
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            closeBtn.onclick = () => {
                modal.style.display = 'none';
            };
        }
        
        // Close on outside click
        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        };
    }

    async loadScanPaths() {
        try {
            const response = await fetch('/api/scan-paths');
            const data = await response.json();
            const select = document.querySelector('#path-filter');
            if (!select || !data.paths) return;
            // Keep "All Paths" option and add paths
            const saved = this.table.pathFilter;
            data.paths.forEach(p => {
                const opt = document.createElement('option');
                opt.value = p;
                opt.textContent = p;
                if (p === saved) opt.selected = true;
                select.appendChild(opt);
            });
            // Validate saved path still exists
            if (this.table.pathFilter && !data.paths.includes(this.table.pathFilter)) {
                this.table.pathFilter = '';
                select.value = '';
                try { localStorage.removeItem('pixelprobe_path_filter'); } catch(e) {}
            }
        } catch (e) {
            // Path filter is optional; silently fail
        }
    }

    async showLogs() {
        const modal = document.querySelector('#logs-modal');
        if (!modal) return;

        modal.style.display = 'block';
        this.logViewer.init();

        // Setup close handlers
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            closeBtn.onclick = () => {
                modal.style.display = 'none';
                this.logViewer.stopAutoRefresh();
            };
        }
        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
                this.logViewer.stopAutoRefresh();
            }
        };

        // Escape key handler
        const escHandler = (e) => {
            if (e.key === 'Escape' && modal.style.display === 'block') {
                modal.style.display = 'none';
                this.logViewer.stopAutoRefresh();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    }

    async downloadLogs() {
        this.logViewer.downloadLogs();
    }

    async purgeLogs() {
        // Build purge params from current view filters
        const filters = this.logViewer.getFilterParams();
        const purgeBody = {};
        if (filters.scan_id) purgeBody.scan_id = filters.scan_id;
        if (filters.start_time) purgeBody.before = new Date().toISOString();
        if (filters.level) purgeBody.level = filters.level;

        const hasFilter = purgeBody.scan_id || purgeBody.before || purgeBody.level;
        const confirmMsg = hasFilter
            ? 'Are you sure you want to purge the currently filtered logs? This cannot be undone.'
            : 'No filters are active. This will purge ALL logs. Are you sure?';

        // If no filters, require explicit "purge all" intent
        if (!hasFilter) {
            purgeBody.before = new Date().toISOString();
        }

        if (!confirm(confirmMsg)) return;
        try {
            const response = await fetch('/api/logs/purge', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(purgeBody)
            });
            const data = await response.json();
            if (data.error) {
                this.showNotification(data.error, 'error');
                return;
            }
            this.showNotification(`Purged ${data.deleted} log entries`, 'success');
            this.logViewer.loadLogs();
        } catch (e) {
            this.showNotification('Failed to purge logs', 'error');
        }
    }

    async viewReport(filename) {
        // Handle viewing reports - support both JSON and PDF
        if (filename.endsWith('.pdf')) {
            // Open PDF in new window/tab
            window.open(`/api/reports/${filename}`, '_blank');
        } else if (filename.endsWith('.json')) {
            // For JSON files, load and display in modal
            try {
                const response = await fetch(`/api/reports/${filename}`);
                if (!response.ok) throw new Error('Failed to load report');
                
                const data = await response.json();
                this.showReportDetails(data);
            } catch (error) {
                this.showNotification('Failed to load report', 'error');
            }
        }
    }

    showReportDetails(report) {
        // Create modal content for report details
        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.style.display = 'block';
        
        const content = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3>Report Details</h3>
                    <button class="modal-close">&times;</button>
                </div>
                <div class="modal-body">
                    <pre>${JSON.stringify(report, null, 2)}</pre>
                </div>
            </div>
        `;
        
        modal.innerHTML = content;
        document.body.appendChild(modal);
        
        // Setup close handlers
        const closeBtn = modal.querySelector('.modal-close');
        closeBtn.onclick = () => {
            modal.remove();
        };
        
        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        };
    }

    async loadScanReports(page = 1) {
        try {
            // Clear selections when loading new reports
            this.selectedReports.clear();
            this.updateReportSelectionUI();
            const selectAllCheckbox = document.getElementById('select-all-reports');
            if (selectAllCheckbox) selectAllCheckbox.checked = false;
            
            // Get filter values
            const typeFilter = document.querySelector('#report-type-filter')?.value || 'all';
            const statusFilter = document.querySelector('#report-status-filter')?.value || 'all';
            
            // Build query params
            const params = new URLSearchParams({
                page: page,
                per_page: 100,  // Use 100 for reports instead of 20
                scan_type: typeFilter,
                status: statusFilter,
                sort_order: 'desc'
            });
            
            const response = await fetch(`/api/scan-reports?${params}`);
            if (!response.ok) throw new Error('Failed to load reports');
            
            const data = await response.json();
            
            // Update table
            const tbody = document.querySelector('#scan-reports-table tbody');
            const cardsContainer = document.querySelector('#scan-reports-cards');
            if (!tbody) return;

            tbody.innerHTML = '';
            if (cardsContainer) cardsContainer.innerHTML = '';

            if (data.reports.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="text-center">No reports found</td></tr>';
                if (cardsContainer) cardsContainer.innerHTML = '<p style="text-align: center; padding: 20px;">No reports found</p>';
                return;
            }

            // Render reports in both formats
            data.reports.forEach((report) => {
                const row = document.createElement('tr');
                
                // Format scan type
                const scanType = report.scan_type.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase());
                
                // Format status with color
                let statusClass = '';
                switch (report.status) {
                    case 'completed': statusClass = 'text-success'; break;
                    case 'running': statusClass = 'text-info'; break;
                    case 'error': statusClass = 'text-danger'; break;
                    case 'cancelled': statusClass = 'text-warning'; break;
                }
                
                // Calculate files processed based on scan type
                let filesInfo = '';
                let issuesInfo = '';
                
                if (report.scan_type === 'cleanup') {
                    filesInfo = `${report.orphaned_records_found} orphaned`;
                    issuesInfo = `${report.orphaned_records_deleted} deleted`;
                } else if (report.scan_type === 'file_changes') {
                    filesInfo = `${report.files_scanned} checked`;
                    issuesInfo = `${report.files_changed} changed`;
                } else {
                    filesInfo = `${report.files_scanned}`;
                    issuesInfo = `${report.files_corrupted} corrupted`;
                    if (report.files_with_warnings > 0) {
                        issuesInfo += `, ${report.files_with_warnings} warnings`;
                    }
                }
                
                row.innerHTML = `
                    <td data-label="Select">
                        <input type="checkbox" 
                               data-report-id="${report.report_id}" 
                               data-filename="${report.filename || ''}"
                               onchange="app.toggleReportSelection('${report.report_id}', this.checked)">
                    </td>
                    <td data-label="Date">${this.table.formatDate(report.start_time)}</td>
                    <td data-label="Type">${scanType}</td>
                    <td data-label="Status"><span class="${statusClass}">${report.status}</span></td>
                    <td data-label="Duration">${report.duration_formatted || 'N/A'}</td>
                    <td data-label="Files">${filesInfo}</td>
                    <td data-label="Issues">${issuesInfo}</td>
                    <td data-label="Actions">
                        <button class="btn btn-sm btn-primary" onclick="app.viewScanReport('${report.report_id}')" title="View Details">
                            <i class="fas fa-eye"></i>
                        </button>
                        <button class="btn btn-sm btn-secondary" onclick="app.exportScanReport('${report.report_id}', 'json')" title="Export JSON">
                            <i class="fas fa-file-export"></i>
                        </button>
                        <button class="btn btn-sm btn-secondary" onclick="app.exportScanReport('${report.report_id}', 'pdf')" title="Export PDF">
                            <i class="fas fa-file-pdf"></i>
                        </button>
                        <button class="btn btn-sm btn-danger" onclick="app.deleteScanReport('${report.report_id}')" title="Delete Report">
                            <i class="fas fa-trash"></i>
                        </button>
                    </td>
                `;

                tbody.appendChild(row);

                // Create mobile card
                if (cardsContainer) {
                    const card = document.createElement('div');
                    card.className = 'report-card';
                    card.innerHTML = `
                        <div class="report-card-header">
                            <h4>${scanType}</h4>
                            <div class="report-card-actions">
                                <button class="btn btn-xs ${statusClass === 'text-success' ? 'btn-success' : statusClass === 'text-danger' ? 'btn-danger' : 'btn-warning'}">${report.status}</button>
                                <button class="btn btn-xs btn-danger" onclick="app.deleteScanReport('${report.report_id}')" title="Delete">
                                    <i class="fas fa-trash"></i>
                                </button>
                            </div>
                        </div>
                        <div class="report-card-info">
                            <p><strong>Date:</strong> ${this.table.formatDate(report.start_time)}</p>
                            <p><strong>Duration:</strong> ${report.duration_formatted || 'N/A'}</p>
                            <p><strong>Files:</strong> ${filesInfo} | <strong>Issues:</strong> ${issuesInfo}</p>
                        </div>
                        <div class="report-card-footer">
                            <button class="btn btn-sm btn-primary" onclick="app.viewScanReport('${report.report_id}')" title="View">
                                <i class="fas fa-eye"></i>
                            </button>
                            <button class="btn btn-sm btn-secondary" onclick="app.exportScanReport('${report.report_id}', 'json')" title="Export JSON">
                                <i class="fas fa-file-code"></i>
                            </button>
                            <button class="btn btn-sm btn-secondary" onclick="app.exportScanReport('${report.report_id}', 'pdf')" title="Export PDF">
                                <i class="fas fa-file-pdf"></i>
                            </button>
                        </div>
                    `;
                    cardsContainer.appendChild(card);
                }
            });

            // Update pagination
            this.updateScanReportsPagination(data.page, data.pages, data.total);
            
        } catch (error) {
            this.showNotification('Failed to load scan reports', 'error');
        }
    }

    updateScanReportsPagination(currentPage, totalPages, totalItems) {
        const paginationContainer = document.querySelector('#scan-reports-pagination');
        if (!paginationContainer) return;
        
        let paginationHtml = '<div class="pagination">';
        
        // Previous button
        if (currentPage > 1) {
            paginationHtml += `<button class="pagination-btn" onclick="app.loadScanReports(${currentPage - 1})">Previous</button>`;
        }
        
        // Page numbers
        for (let i = 1; i <= totalPages; i++) {
            if (i === currentPage) {
                paginationHtml += `<span class="pagination-current">${i}</span>`;
            } else if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
                paginationHtml += `<button class="pagination-btn" onclick="app.loadScanReports(${i})">${i}</button>`;
            } else if (i === currentPage - 3 || i === currentPage + 3) {
                paginationHtml += '<span>...</span>';
            }
        }
        
        // Next button
        if (currentPage < totalPages) {
            paginationHtml += `<button class="pagination-btn" onclick="app.loadScanReports(${currentPage + 1})">Next</button>`;
        }
        
        paginationHtml += `<span class="pagination-info">Total: ${totalItems} reports</span>`;
        paginationHtml += '</div>';
        
        paginationContainer.innerHTML = paginationHtml;
    }

    async viewScanReport(reportId) {
        try {
            const response = await fetch(`/api/scan-reports/${reportId}`);
            if (!response.ok) throw new Error('Failed to load report');
            
            const report = await response.json();
            
            // Create a detailed view modal
            let detailsHtml = '<div class="scan-report-details">';
            detailsHtml += '<h4>Report Details</h4>';
            detailsHtml += '<table class="table">';
            detailsHtml += `<tr><th>Report ID:</th><td>${report.report_id}</td></tr>`;
            detailsHtml += `<tr><th>Scan Type:</th><td>${report.scan_type.replace('_', ' ').toUpperCase()}</td></tr>`;
            detailsHtml += `<tr><th>Status:</th><td>${report.status}</td></tr>`;
            detailsHtml += `<tr><th>Start Time:</th><td>${new Date(report.start_time).toLocaleString()}</td></tr>`;
            detailsHtml += `<tr><th>End Time:</th><td>${report.end_time ? new Date(report.end_time).toLocaleString() : 'N/A'}</td></tr>`;
            detailsHtml += `<tr><th>Duration:</th><td>${report.duration_formatted || 'N/A'}</td></tr>`;
            
            if (report.directories_scanned && Array.isArray(report.directories_scanned) && report.directories_scanned.length > 0) {
                // Cleanup and file-changes reports store file lists (objects with
                // file_path/change_type) in this field; scan reports store paths
                const label = report.scan_type === 'cleanup' ? 'Orphaned Files:'
                    : report.scan_type === 'file_changes' ? 'Changed Files:'
                    : 'Directories:';
                const maxEntries = 100;
                const entries = report.directories_scanned.slice(0, maxEntries).map(entry => {
                    if (entry && typeof entry === 'object') {
                        const path = entry.file_path || JSON.stringify(entry);
                        return escapeHtml(entry.change_type ? `${path} (${entry.change_type})` : path);
                    }
                    return escapeHtml(String(entry));
                });
                if (report.directories_scanned.length > maxEntries) {
                    entries.push(`... and ${report.directories_scanned.length - maxEntries} more`);
                }
                detailsHtml += `<tr><th>${label}</th><td>${entries.join('<br>')}</td></tr>`;
            }
            
            detailsHtml += '</table>';
            
            if (report.summary) {
                detailsHtml += '<h4>Summary Statistics</h4>';
                detailsHtml += '<table class="table">';
                Object.entries(report.summary).forEach(([key, value]) => {
                    const label = key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
                    detailsHtml += `<tr><th>${label}:</th><td>${value}</td></tr>`;
                });
                detailsHtml += '</table>';
            }
            
            detailsHtml += '</div>';
            
            // Show in a simple alert for now (could be improved with a modal)
            const detailModal = document.createElement('div');
            detailModal.className = 'modal';
            detailModal.style.display = 'block';
            detailModal.innerHTML = `
                <div class="modal-content">
                    <div class="modal-header">
                        <h3 class="modal-title">Scan Report Details</h3>
                        <button class="modal-close">&times;</button>
                    </div>
                    <div class="modal-body">
                        ${detailsHtml}
                    </div>
                </div>
            `;
            
            document.body.appendChild(detailModal);
            
            // Setup close handlers
            const closeBtn = detailModal.querySelector('.modal-close');
            closeBtn.onclick = () => detailModal.remove();
            detailModal.onclick = (e) => {
                if (e.target === detailModal) detailModal.remove();
            };
            
        } catch (error) {
            this.showNotification('Failed to load report details', 'error');
        }
    }

    async exportScanReport(reportId, format) {
        try {
            const endpoint = format === 'pdf' ? `/api/scan-reports/${reportId}/pdf` : `/api/scan-reports/${reportId}/export`;
            
            // Create a temporary link and click it to download
            const link = document.createElement('a');
            link.href = endpoint;
            link.download = `scan_report_${reportId}.${format}`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            this.showNotification(`Exporting report as ${format.toUpperCase()}...`, 'info');
            
        } catch (error) {
            this.showNotification('Failed to export report', 'error');
        }
    }
    async deleteScanReport(reportId) {
        if (!confirm('Are you sure you want to delete this report? This action cannot be undone.')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/scan-reports/${reportId}`, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json'
                }
            });
            
            if (!response.ok) throw new Error('Failed to delete report');
            
            const result = await response.json();
            this.showNotification('Report deleted successfully', 'success');
            
            // Reload the reports list
            await this.loadScanReports();
            
        } catch (error) {
            this.showNotification('Failed to delete report', 'error');
        }
    }

    // Report selection handling
    selectedReports = new Set();

    toggleAllReports(checked) {
        const checkboxes = document.querySelectorAll('#scan-reports-table tbody input[type="checkbox"]');
        checkboxes.forEach(cb => {
            cb.checked = checked;
            if (checked) {
                this.selectedReports.add(cb.dataset.reportId);
            } else {
                this.selectedReports.delete(cb.dataset.reportId);
            }
        });
        this.updateReportSelectionUI();
    }

    toggleReportSelection(reportId, checked) {
        if (checked) {
            this.selectedReports.add(reportId);
        } else {
            this.selectedReports.delete(reportId);
        }
        this.updateReportSelectionUI();
    }

    updateReportSelectionUI() {
        const downloadBtn = document.getElementById('downloadBtn');
        const deleteBtn = document.getElementById('deleteBtn');
        
        if (downloadBtn) downloadBtn.disabled = this.selectedReports.size === 0;
        if (deleteBtn) deleteBtn.disabled = this.selectedReports.size === 0;
        
        // Update select-all checkbox state
        const selectAllCheckbox = document.getElementById('select-all-reports');
        if (selectAllCheckbox) {
            const allCheckboxes = document.querySelectorAll('#scan-reports-table tbody input[type="checkbox"]');
            const checkedCount = document.querySelectorAll('#scan-reports-table tbody input[type="checkbox"]:checked').length;
            selectAllCheckbox.checked = allCheckboxes.length > 0 && checkedCount === allCheckboxes.length;
        }
    }

    async downloadSelectedReports(format) {
        if (this.selectedReports.size === 0) {
            this.showNotification('No reports selected', 'warning');
            return;
        }

        try {
            // Get filenames for selected reports
            const filenames = [];
            for (const reportId of this.selectedReports) {
                filenames.push(`scan_report_${reportId}.json`);
            }

            const response = await fetch('/api/reports/download-multiple', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    filenames: filenames,
                    format: format
                })
            });

            if (!response.ok) throw new Error('Download failed');

            // Get filename from content-disposition header
            const contentDisposition = response.headers.get('content-disposition');
            let filename = `pixelprobe_reports_${new Date().toISOString().slice(0,10)}.${format === 'pdf' ? 'pdf' : 'zip'}`;
            if (contentDisposition) {
                const match = contentDisposition.match(/filename="?(.+)"?/);
                if (match) filename = match[1];
            }

            // Download the file
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            this.showNotification(`Downloaded ${this.selectedReports.size} report(s)`, 'success');
            this.selectedReports.clear();
            this.updateReportSelectionUI();
            // Clear the select-all checkbox
            const selectAllCheckbox = document.getElementById('select-all-reports');
            if (selectAllCheckbox) selectAllCheckbox.checked = false;
            // Close the dropdown
            const dropdown = document.getElementById('bulk-download-menu');
            if (dropdown) dropdown.style.display = 'none';
        } catch (error) {
            this.showNotification('Failed to download reports', 'error');
        }
    }

    async deleteSelectedReports() {
        if (this.selectedReports.size === 0) {
            this.showNotification('No reports selected', 'warning');
            return;
        }

        if (!confirm(`Are you sure you want to delete ${this.selectedReports.size} report(s)?`)) {
            return;
        }

        try {
            for (const reportId of this.selectedReports) {
                await fetch(`/api/scan-reports/${reportId}`, {
                    method: 'DELETE',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });
            }

            this.showNotification(`Deleted ${this.selectedReports.size} report(s)`, 'success');
            this.selectedReports.clear();
            this.updateReportSelectionUI();
            // Clear the select-all checkbox
            const selectAllCheckbox = document.getElementById('select-all-reports');
            if (selectAllCheckbox) selectAllCheckbox.checked = false;
            await this.loadScanReports();
        } catch (error) {
            this.showNotification('Failed to delete reports', 'error');
        }
    }

    toggleDropdown(event, dropdownId) {
        event.stopPropagation();
        const dropdown = document.getElementById(dropdownId);
        
        // Close all other dropdowns
        document.querySelectorAll('.dropdown-menu').forEach(menu => {
            if (menu.id !== dropdownId) {
                menu.style.display = 'none';
            }
        });
        
        // Toggle current dropdown
        dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';
        
        // Close on outside click
        const closeDropdown = (e) => {
            if (!e.target.closest('.dropdown')) {
                dropdown.style.display = 'none';
                document.removeEventListener('click', closeDropdown);
            }
        };
        
        if (dropdown.style.display === 'block') {
            setTimeout(() => document.addEventListener('click', closeDropdown), 0);
        }
    }

    toggleExportMenu(event) {
        event.stopPropagation();
        const menu = document.getElementById('exportDropdownMenu');
        menu.classList.toggle('show');
        
        // Close menu when clicking outside
        const closeMenu = (e) => {
            if (!e.target.closest('.export-dropdown')) {
                menu.classList.remove('show');
                document.removeEventListener('click', closeMenu);
            }
        };
        
        if (menu.classList.contains('show')) {
            document.addEventListener('click', closeMenu);
        }
    }

    async exportData(format = 'csv') {
        // Close the dropdown menu
        const menu = document.getElementById('exportDropdownMenu');
        if (menu) {
            menu.classList.remove('show');
        }
        
        try {
            // Show loading notification
            let itemDescription;
            if (this.table.selectedFiles.size > 0) {
                itemDescription = `${this.table.selectedFiles.size} selected files`;
            } else {
                const filterText = this.table.filter !== 'all' ? `${this.table.filter} files` : 'all files';
                const searchText = this.table.searchQuery ? ` matching "${this.table.searchQuery}"` : '';
                itemDescription = filterText + searchText;
            }
            
            const formatUpper = format.toUpperCase();
            this.showNotification(`Generating ${formatUpper} export for ${itemDescription}...`, 'info');
            
            let requestBody = {
                format: format
            };
            
            if (this.table.selectedFiles.size > 0) {
                // Export selected files
                requestBody.file_ids = Array.from(this.table.selectedFiles);
            } else {
                // Export all files in current filter/search
                requestBody.filter = this.table.filter;
                requestBody.search = this.table.searchQuery;
            }
            
            const response = await fetch('/api/export', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(requestBody)
            });

            if (response.ok) {
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                
                // Set appropriate filename based on format
                const date = new Date().toISOString().split('T')[0];
                let filename = `pixelprobe_export_${date}`;
                if (format === 'json') {
                    filename += '.json';
                } else if (format === 'pdf') {
                    filename += '.pdf';
                } else {
                    filename += '.csv';
                }
                
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                this.showNotification(`${formatUpper} export completed successfully`, 'success');
            } else {
                throw new Error('Export failed');
            }
        } catch (error) {
            this.showNotification(`Failed to export ${format.toUpperCase()}`, 'error');
        }
    }


    async downloadFile(fileId) {
        window.location.href = `/api/download/${fileId}`;
    }

    async downloadSelected() {
        if (this.table.selectedFiles.size === 0) {
            this.showNotification('No files selected', 'warning');
            return;
        }

        if (this.table.selectedFiles.size > 10) {
            if (!confirm(`Are you sure you want to download ${this.table.selectedFiles.size} files?`)) {
                return;
            }
        }

        // Download files one by one with a small delay
        const fileIds = Array.from(this.table.selectedFiles);
        for (let i = 0; i < fileIds.length; i++) {
            const fileId = fileIds[i];
            setTimeout(() => {
                const link = document.createElement('a');
                link.href = `/api/download/${fileId}`;
                link.download = '';
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
            }, i * 500); // 500ms delay between downloads
        }
        
        this.showNotification(`Downloading ${fileIds.length} files...`, 'info');
    }

    async rescanSelected() {
        if (this.table.selectedFiles.size === 0) {
            this.showNotification('No files selected', 'warning');
            return;
        }

        try {
            const fileIds = Array.from(this.table.selectedFiles);

            // Get file paths for the selected files
            const filePaths = [];
            for (const fileId of fileIds) {
                const response = await fetch(`/api/scan-results/${fileId}`);
                if (response.ok) {
                    const result = await response.json();
                    filePaths.push(result.file_path);
                }
            }

            // Send all files as one bulk rescan request to /scan-files-parallel
            // This scans the specific files directly without discovery phase
            const scanResponse = await this.api.request('/scan-files-parallel', {
                method: 'POST',
                body: JSON.stringify({
                    file_paths: filePaths,
                    force_rescan: true
                })
            });

            if (scanResponse) {
                this.showNotification(`Rescan started for ${filePaths.length} files`, 'success');
                this.progress.startMonitoring('scan');
            } else {
                throw new Error('Rescan failed');
            }
        } catch (error) {
            this.showNotification('Failed to start rescan', 'error');
        }
    }

    async orphanScanSelected() {
        if (this.table.selectedFiles.size === 0) {
            this.showNotification('No files selected', 'warning');
            return;
        }

        try {
            const fileIds = Array.from(this.table.selectedFiles);

            // Get file paths for the selected files
            const filePaths = [];
            for (const fileId of fileIds) {
                const response = await fetch(`/api/scan-results/${fileId}`);
                if (response.ok) {
                    const result = await response.json();
                    filePaths.push(result.file_path);
                }
            }

            // Start cleanup for selected files
            const response = await fetch('/api/cleanup-orphaned', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    file_paths: filePaths
                })
            });

            if (response.ok) {
                this.showNotification(`Cleanup started for ${fileIds.length} files`, 'success');
                this.progress.startMonitoring('cleanup');
            } else {
                throw new Error('Cleanup failed');
            }
        } catch (error) {
            this.showNotification('Failed to start cleanup', 'error');
        }
    }

    async changeCheckSelected() {
        if (this.table.selectedFiles.size === 0) {
            this.showNotification('No files selected', 'warning');
            return;
        }

        try {
            const fileIds = Array.from(this.table.selectedFiles);

            // Get file paths for the selected files
            const filePaths = [];
            for (const fileId of fileIds) {
                const response = await fetch(`/api/scan-results/${fileId}`);
                if (response.ok) {
                    const result = await response.json();
                    filePaths.push(result.file_path);
                }
            }

            // Start file changes check for selected files
            const response = await fetch('/api/file-changes', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    file_paths: filePaths
                })
            });

            if (response.ok) {
                this.showNotification(`Integrity check started for ${fileIds.length} files`, 'success');
                this.progress.startMonitoring('file_changes');
            } else {
                throw new Error('Integrity check failed');
            }
        } catch (error) {
            this.showNotification('Failed to start integrity check', 'error');
        }
    }

    closeAllDropdowns() {
        // Close all dropdown menus
        document.querySelectorAll('.dropdown-menu').forEach(menu => {
            menu.style.display = 'none';
        });
    }

    toggleActionDropdown(event, dropdownId) {
        event.stopPropagation();
        const dropdown = document.getElementById(dropdownId);

        // Close all other dropdowns
        document.querySelectorAll('.dropdown-menu').forEach(menu => {
            if (menu.id !== dropdownId) {
                menu.style.display = 'none';
            }
        });

        // Toggle current dropdown
        dropdown.style.display = dropdown.style.display === 'none' ? 'block' : 'none';

        // Close on outside click
        const closeDropdown = (e) => {
            if (!e.target.closest('.action-dropdown')) {
                dropdown.style.display = 'none';
                document.removeEventListener('click', closeDropdown);
            }
        };

        if (dropdown.style.display === 'block') {
            setTimeout(() => document.addEventListener('click', closeDropdown), 0);
        }
    }

    async viewScanOutput(fileId) {
        try {
            const response = await fetch(`/api/scan-results/${fileId}`);
            if (response.ok) {
                const file = await response.json();
                this.showScanOutputModal(file);
            } else {
                this.showNotification('Failed to load scan output', 'error');
            }
        } catch (error) {
            this.showNotification('Failed to load scan output', 'error');
        }
    }

    showScanOutputModal(file) {
        // Create modal if it doesn't exist
        let modal = document.querySelector('#scan-output-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'scan-output-modal';
            modal.className = 'modal';
            modal.innerHTML = `
                <div class="modal-content">
                    <div class="modal-header">
                        <h3 class="modal-title">Scan Output Details</h3>
                        <button class="modal-close">&times;</button>
                    </div>
                    <div class="modal-body"></div>
                </div>
            `;
            document.body.appendChild(modal);
        }
        
        const modalBody = modal.querySelector('.modal-body');
        
        // Collect all available details
        const details = [];
        if (file.corruption_details) details.push({label: 'Corruption Details', content: file.corruption_details});
        if (file.scan_output) details.push({label: 'Scan Output', content: file.scan_output});
        if (file.error_message) details.push({label: 'Error Message', content: file.error_message});
        if (file.warning_details) details.push({label: 'Warning Details', content: file.warning_details});
        
        const detailsHtml = details.length > 0 ? 
            details.map(detail => `
                <h4>${detail.label}:</h4>
                <pre class="scan-output-text">${this.escapeHtml(detail.content)}</pre>
            `).join('<hr>') :
            '<p>No scan output available</p>';
        
        modalBody.innerHTML = `
            <div class="scan-output-details">
                <h4>File: ${this.escapeHtml(file.file_path)}</h4>
                <p><strong>Status:</strong> ${file.marked_as_good ? 'Healthy' : (file.is_corrupted ? 'Corrupted' : (file.has_warnings ? 'Warning' : 'Healthy'))}</p>
                <p><strong>Tool:</strong> ${file.scan_tool || 'N/A'}</p>
                <p><strong>Scanned:</strong> ${file.scan_date ? new Date(file.scan_date).toLocaleString() : 'N/A'}</p>
                ${file.last_integrity_check_date ? `<p><strong>Last Integrity Check:</strong> ${new Date(file.last_integrity_check_date).toLocaleString()}</p>` : ''}
                <hr>
                ${detailsHtml}
            </div>
        `;
        
        modal.style.display = 'block';
        
        // Setup close handlers
        const closeBtn = modal.querySelector('.modal-close');
        if (closeBtn) {
            closeBtn.onclick = () => modal.style.display = 'none';
        }
        
        // Close on outside click
        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        };
    }

    async cancelCurrentOperation() {
        try {
            // Determine which operation is currently running and cancel it
            const operationType = this.progress.operationType;
            
            if (operationType === 'scan') {
                const status = await this.api.getScanStatus();
                if (status.is_scanning || status.is_running || status.is_active) {
                    const result = await this.api.cancelScan();
                    this.showNotification('Scan cancellation requested', 'info');
                } else {
                    this.showNotification('No scan is currently running', 'warning');
                }
            } else if (operationType === 'cleanup') {
                const status = await this.api.getCleanupStatus();
                if (status.is_running) {
                    await this.api.cancelCleanup();
                    this.showNotification('Cleanup cancellation requested', 'info');
                }
            } else if (operationType === 'file-changes') {
                const status = await this.api.getFileChangesStatus();
                if (status.is_running) {
                    await this.api.cancelFileChanges();
                    this.showNotification('Integrity scan cancellation requested', 'info');
                }
            } else {
                this.showNotification('No operation is currently running', 'warning');
            }
        } catch (error) {
            this.showNotification('Failed to cancel operation', 'error');
        }
    }

    // Schedule Management
    async showSchedules() {
        const modal = document.querySelector('#schedules-modal');
        if (!modal) return;
        
        modal.style.display = 'block';
        await this.loadSchedules();
    }

    async loadSchedules() {
        try {
            const response = await fetch('/api/schedules');
            const data = await response.json();
            
            const listContainer = document.querySelector('#schedules-list');
            if (!listContainer) return;
            
            if (data.schedules && data.schedules.length > 0) {
                let html = '<div class="schedules-list">';
                data.schedules.forEach(schedule => {
                    const nextRun = schedule.next_run ? new Date(schedule.next_run).toLocaleString() : 'Not scheduled';
                    const lastRun = schedule.last_run ? new Date(schedule.last_run).toLocaleString() : 'Never';
                    
                    html += `
                        <div class="schedule-item">
                            <div class="schedule-header">
                                <h4>${this.escapeHtml(schedule.name)}</h4>
                                <div class="schedule-actions">
                                    <button class="btn btn-sm btn-primary"
                                            onclick="app.showEditSchedule(${schedule.id})"
                                            title="Edit Schedule">
                                        <i class="fas fa-edit"></i>
                                    </button>
                                    <button class="btn btn-sm ${schedule.has_healthcheck ? 'btn-success' : 'btn-info'}"
                                            onclick="app.showHealthcheckConfig(${schedule.id}, '${this.escapeHtml(schedule.name)}')"
                                            title="${schedule.has_healthcheck ? (schedule.healthcheck_active ? 'Healthcheck Active' : 'Healthcheck Configured (Inactive)') : 'Configure Healthcheck'}">
                                        <i class="fas fa-heartbeat"></i>${schedule.has_healthcheck ? ' ✓' : ''}
                                    </button>
                                    <button class="btn btn-sm ${schedule.is_active ? 'btn-warning' : 'btn-success'}"
                                            onclick="app.toggleSchedule(${schedule.id}, ${!schedule.is_active})"
                                            title="${schedule.is_active ? 'Disable Schedule' : 'Enable Schedule'}">
                                        <i class="fas ${schedule.is_active ? 'fa-pause' : 'fa-play'}"></i>
                                    </button>
                                    <button class="btn btn-sm btn-danger" onclick="app.deleteSchedule(${schedule.id})">
                                        <i class="fas fa-trash"></i>
                                    </button>
                                </div>
                            </div>
                            <div class="schedule-info">
                                <p><strong>Schedule:</strong> ${this.escapeHtml(schedule.cron_expression)}</p>
                                <p><strong>Type:</strong> ${this.formatScanType(schedule.scan_type || 'normal')}</p>
                                <p><strong>Next Run:</strong> ${nextRun}</p>
                                <p><strong>Last Run:</strong> ${lastRun}</p>
                                ${schedule.scan_paths && schedule.scan_paths.length > 0 ? `<p><strong>Paths:</strong> ${this.escapeHtml(schedule.scan_paths.join(', '))}</p>` : ''}
                            </div>
                        </div>
                    `;
                });
                html += '</div>';
                listContainer.innerHTML = html;
            } else {
                listContainer.innerHTML = '<p class="text-muted">No schedules configured.</p>';
            }
        } catch (error) {
            this.showNotification('Failed to load schedules', 'error');
        }
    }

    showAddSchedule() {
        const modal = document.querySelector('#add-schedule-modal');
        if (modal) {
            modal.style.display = 'block';
            
            // Reset form
            const form = document.querySelector('#add-schedule-form');
            if (form) form.reset();
        }
    }

    toggleScheduleInput() {
        const scheduleType = document.querySelector('#schedule-type').value;
        const cronInput = document.querySelector('#cron-input');
        const intervalInput = document.querySelector('#interval-input');

        if (scheduleType === 'cron') {
            cronInput.style.display = 'block';
            intervalInput.style.display = 'none';
        } else {
            cronInput.style.display = 'none';
            intervalInput.style.display = 'block';
        }
    }

    toggleEditScheduleInput() {
        const scheduleType = document.querySelector('#edit-schedule-type').value;
        const cronInput = document.querySelector('#edit-cron-input');
        const intervalInput = document.querySelector('#edit-interval-input');

        if (scheduleType === 'cron') {
            cronInput.style.display = 'block';
            intervalInput.style.display = 'none';
        } else {
            cronInput.style.display = 'none';
            intervalInput.style.display = 'block';
        }
    }

    async showEditSchedule(scheduleId) {
        try {
            // Load schedule data
            const response = await fetch(`/api/schedules/${scheduleId}`);
            if (!response.ok) throw new Error('Failed to load schedule');

            const schedule = await response.json();

            // Populate form fields
            document.getElementById('edit-schedule-id').value = schedule.id;
            document.getElementById('edit-schedule-name').value = schedule.name || '';
            document.getElementById('edit-scan-type').value = schedule.scan_type || 'normal';
            document.getElementById('edit-schedule-paths').value = schedule.scan_paths ? schedule.scan_paths.join('\n') : '';
            document.getElementById('edit-force-rescan').checked = schedule.force_rescan || false;
            document.getElementById('edit-is-active').checked = schedule.is_active !== undefined ? schedule.is_active : true;

            // Determine if it's cron or interval
            const cronExpression = schedule.cron_expression || '';
            const isCron = !cronExpression.match(/^\d+\s+(hour|day|week)s?$/i);

            if (isCron) {
                document.getElementById('edit-schedule-type').value = 'cron';
                document.getElementById('edit-cron-expression').value = cronExpression;
                document.getElementById('edit-cron-input').style.display = 'block';
                document.getElementById('edit-interval-input').style.display = 'none';
            } else {
                // Parse interval (e.g., "24 hours", "7 days")
                const match = cronExpression.match(/^(\d+)\s+(hour|day|week)s?$/i);
                if (match) {
                    document.getElementById('edit-schedule-type').value = 'interval';
                    document.getElementById('edit-interval-value').value = match[1];
                    document.getElementById('edit-interval-unit').value = match[2].toLowerCase() + 's';
                    document.getElementById('edit-cron-input').style.display = 'none';
                    document.getElementById('edit-interval-input').style.display = 'block';
                }
            }

            // Setup form submission
            const form = document.getElementById('edit-schedule-form');
            form.onsubmit = async (e) => {
                e.preventDefault();
                await this.saveScheduleEdit();
            };

            this.openModal('edit-schedule-modal');
        } catch (error) {
            this.showNotification('Failed to load schedule', 'error');
        }
    }

    async saveScheduleEdit() {
        try {
            const scheduleId = document.getElementById('edit-schedule-id').value;
            const name = document.getElementById('edit-schedule-name').value;
            const scanType = document.getElementById('edit-scan-type').value;
            const paths = document.getElementById('edit-schedule-paths').value;
            const forceRescan = document.getElementById('edit-force-rescan').checked;
            const isActive = document.getElementById('edit-is-active').checked;
            const scheduleType = document.getElementById('edit-schedule-type').value;

            let cronExpression;
            if (scheduleType === 'cron') {
                cronExpression = document.getElementById('edit-cron-expression').value;
            } else {
                const intervalValue = document.getElementById('edit-interval-value').value;
                const intervalUnit = document.getElementById('edit-interval-unit').value;
                cronExpression = `${intervalValue} ${intervalUnit}`;
            }

            const scanPaths = paths.trim() ? paths.split('\n').map(p => p.trim()).filter(p => p) : [];

            const response = await fetch(`/api/schedules/${scheduleId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    cron_expression: cronExpression,
                    scan_type: scanType,
                    scan_paths: scanPaths,
                    force_rescan: forceRescan,
                    is_active: isActive
                })
            });

            if (response.ok) {
                this.showNotification('Schedule updated successfully', 'success');
                this.closeModal('edit-schedule-modal');
                await this.loadSchedules();
            } else {
                const error = await response.json();
                throw new Error(error.error || 'Failed to update schedule');
            }
        } catch (error) {
            this.showNotification(`Failed to update schedule: ${error.message}`, 'error');
        }
    }

    async toggleSchedule(scheduleId, activate) {
        try {
            const response = await fetch(`/api/schedules/${scheduleId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ is_active: activate })
            });
            
            if (response.ok) {
                this.showNotification(`Schedule ${activate ? 'enabled' : 'disabled'}`, 'success');
                await this.loadSchedules();
            } else {
                throw new Error('Failed to update schedule');
            }
        } catch (error) {
            this.showNotification('Failed to update schedule', 'error');
        }
    }

    async deleteSchedule(scheduleId) {
        if (!confirm('Are you sure you want to delete this schedule?')) return;

        try {
            const response = await fetch(`/api/schedules/${scheduleId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                this.showNotification('Schedule deleted', 'success');
                await this.loadSchedules();
            } else {
                throw new Error('Failed to delete schedule');
            }
        } catch (error) {
            this.showNotification('Failed to delete schedule', 'error');
        }
    }

    // Healthcheck Management
    async showHealthcheckConfig(scheduleId, scheduleName) {
        const modal = document.querySelector('#healthcheck-modal');
        if (!modal) return;

        // Store current schedule ID
        this.currentScheduleId = scheduleId;

        // Update modal title
        const modalTitle = document.querySelector('#healthcheck-modal-title');
        if (modalTitle) {
            modalTitle.textContent = `Healthcheck for: ${scheduleName}`;
        }

        // Load existing config if any
        await this.loadHealthcheckConfig(scheduleId);

        modal.style.display = 'block';
    }

    async loadHealthcheckConfig(scheduleId) {
        try {
            const response = await fetch(`/api/healthcheck/schedule/${scheduleId}`);

            if (response.ok) {
                const config = await response.json();

                // Populate form with existing config
                document.querySelector('#healthcheck-url').value = config.healthcheck_url || '';
                document.querySelector('#healthcheck-active').checked = config.is_active !== false;
                document.querySelector('#send-start-ping').checked = config.send_start_ping !== false;
                document.querySelector('#send-success-ping').checked = config.send_success_ping !== false;
                document.querySelector('#send-failure-ping').checked = config.send_failure_ping !== false;
                document.querySelector('#include-report-data').checked = config.include_report_data !== false;

                // Show delete button if config exists
                const deleteBtn = document.querySelector('#delete-healthcheck-btn');
                if (deleteBtn) {
                    deleteBtn.style.display = 'inline-block';
                    deleteBtn.onclick = () => this.deleteHealthcheckConfig(config.id);
                }

                // Show last ping status if available
                const statusDiv = document.querySelector('#healthcheck-status');
                if (statusDiv && config.last_ping_time) {
                    const lastPing = new Date(config.last_ping_time).toLocaleString();
                    const statusClass = config.last_ping_status === 'success' ? 'text-success' : 'text-danger';
                    statusDiv.innerHTML = `<p class="${statusClass}">Last ping: ${lastPing} (${config.last_ping_status})</p>`;
                    statusDiv.style.display = 'block';
                }
            } else if (response.status === 404) {
                // No config exists yet, reset form
                document.querySelector('#healthcheck-url').value = '';
                document.querySelector('#healthcheck-active').checked = true;
                document.querySelector('#send-start-ping').checked = true;
                document.querySelector('#send-success-ping').checked = true;
                document.querySelector('#send-failure-ping').checked = true;
                document.querySelector('#include-report-data').checked = true;

                const deleteBtn = document.querySelector('#delete-healthcheck-btn');
                if (deleteBtn) deleteBtn.style.display = 'none';

                const statusDiv = document.querySelector('#healthcheck-status');
                if (statusDiv) statusDiv.style.display = 'none';
            }
        } catch (error) {
        }
    }

    async saveHealthcheckConfig() {
        const scheduleId = this.currentScheduleId;
        if (!scheduleId) return;

        const url = document.querySelector('#healthcheck-url').value.trim();
        if (!url) {
            this.showNotification('Healthcheck URL is required', 'error');
            return;
        }

        const configData = {
            schedule_id: scheduleId,
            healthcheck_url: url,
            is_active: document.querySelector('#healthcheck-active').checked,
            send_start_ping: document.querySelector('#send-start-ping').checked,
            send_success_ping: document.querySelector('#send-success-ping').checked,
            send_failure_ping: document.querySelector('#send-failure-ping').checked,
            include_report_data: document.querySelector('#include-report-data').checked
        };

        try {
            // Check if config exists
            const checkResponse = await fetch(`/api/healthcheck/schedule/${scheduleId}`);
            const method = checkResponse.ok ? 'PUT' : 'POST';
            const endpoint = checkResponse.ok ? `/api/healthcheck/${(await checkResponse.json()).id}` : '/api/healthcheck';

            const response = await fetch(endpoint, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(configData)
            });

            if (response.ok) {
                this.showNotification('Healthcheck configuration saved', 'success');
                this.closeModal('healthcheck-modal');
            } else {
                const error = await response.json();
                throw new Error(error.error || 'Failed to save configuration');
            }
        } catch (error) {
            this.showNotification(`Failed to save: ${error.message}`, 'error');
        }
    }

    async deleteHealthcheckConfig(configId) {
        if (!confirm('Are you sure you want to delete this healthcheck configuration?')) return;

        try {
            const response = await fetch(`/api/healthcheck/${configId}`, {
                method: 'DELETE'
            });

            if (response.ok) {
                this.showNotification('Healthcheck configuration deleted', 'success');
                this.closeModal('healthcheck-modal');
            } else {
                throw new Error('Failed to delete configuration');
            }
        } catch (error) {
            this.showNotification('Failed to delete configuration', 'error');
        }
    }

    async testHealthcheck() {
        const scheduleId = this.currentScheduleId;
        if (!scheduleId) return;

        try {
            // First check if config exists
            const checkResponse = await fetch(`/api/healthcheck/schedule/${scheduleId}`);
            if (!checkResponse.ok) {
                this.showNotification('Please save the configuration before testing', 'warning');
                return;
            }

            const config = await checkResponse.json();
            const response = await fetch(`/api/healthcheck/${config.id}/test`, {
                method: 'POST'
            });

            const result = await response.json();

            if (result.success) {
                this.showNotification('Test ping sent successfully!', 'success');

                // Reload config to show updated ping status
                await this.loadHealthcheckConfig(scheduleId);
            } else {
                this.showNotification(`Test ping failed: ${result.message}`, 'error');
            }
        } catch (error) {
            this.showNotification('Failed to send test ping', 'error');
        }
    }

    // Exclusions Management
    async showExclusions() {
        const modal = document.querySelector('#exclusions-modal');
        if (!modal) return;
        
        modal.style.display = 'block';
        await this.loadExclusions();
    }

    async loadExclusions() {
        try {
            const response = await fetch('/api/exclusions');
            const data = await response.json();
            
            // Update paths list
            const pathsList = document.querySelector('#excluded-paths-list');
            if (pathsList) {
                if (data.paths && data.paths.length > 0) {
                    pathsList.innerHTML = data.paths.map(path => `
                        <div class="exclusion-item">
                            <span>${this.escapeHtml(path)}</span>
                            <button class="btn btn-sm btn-danger" onclick="app.removeExclusion('path', '${this.escapeHtml(path)}')">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    `).join('');
                } else {
                    pathsList.innerHTML = '<div class="empty-state">No excluded paths</div>';
                }
            }
            
            // Update extensions list
            const extensionsList = document.querySelector('#excluded-extensions-list');
            if (extensionsList) {
                if (data.extensions && data.extensions.length > 0) {
                    extensionsList.innerHTML = data.extensions.map(ext => `
                        <div class="exclusion-item">
                            <span>${this.escapeHtml(ext)}</span>
                            <button class="btn btn-sm btn-danger" onclick="app.removeExclusion('extension', '${this.escapeHtml(ext)}')">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    `).join('');
                } else {
                    extensionsList.innerHTML = '<div class="empty-state">No excluded extensions</div>';
                }
            }
        } catch (error) {
            this.showNotification('Failed to load exclusions', 'error');
        }
    }
    
    async addExclusion(type) {
        try {
            const inputId = type === 'path' ? 'new-excluded-path' : 'new-excluded-extension';
            const input = document.querySelector(`#${inputId}`);
            if (!input || !input.value.trim()) return;
            
            const value = input.value.trim();
            const response = await fetch(`/api/exclusions/${type}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ item: value })
            });
            
            if (response.ok) {
                input.value = '';
                await this.loadExclusions();
                this.showNotification(`${type === 'path' ? 'Path' : 'Extension'} excluded successfully`, 'success');
            } else {
                throw new Error('Failed to add exclusion');
            }
        } catch (error) {
            this.showNotification('Failed to add exclusion', 'error');
        }
    }
    
    async removeExclusion(type, value) {
        try {
            const response = await fetch(`/api/exclusions/${type}`, {
                method: 'DELETE',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ item: value })
            });
            
            if (response.ok) {
                await this.loadExclusions();
                this.showNotification(`${type === 'path' ? 'Path' : 'Extension'} removed from exclusions`, 'success');
            } else {
                throw new Error('Failed to remove exclusion');
            }
        } catch (error) {
            this.showNotification('Failed to remove exclusion', 'error');
        }
    }

    openModal(modalId) {
        const modal = document.querySelector(`#${modalId}`);
        if (modal) {
            modal.style.display = 'block';
        }
    }

    closeModal(modalId) {
        const modal = document.querySelector(`#${modalId}`);
        if (modal) {
            modal.style.display = 'none';
        }
    }

}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new PixelProbeApp();
    window.app.init();
    
    // Setup modal close buttons
    document.querySelectorAll('.modal-close').forEach(btn => {
        btn.addEventListener('click', function() {
            this.closest('.modal').style.display = 'none';
        });
    });
    
    // Close modal when clicking outside
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', function(e) {
            if (e.target === this) {
                this.style.display = 'none';
            }
        });
    });
    
    // Setup add schedule form
    const addScheduleForm = document.querySelector('#add-schedule-form');
    if (addScheduleForm) {
        addScheduleForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const scheduleType = document.querySelector('#schedule-type').value;
            let cronExpression = '';
            
            if (scheduleType === 'cron') {
                cronExpression = document.querySelector('#cron-expression').value;
            } else {
                const value = document.querySelector('#interval-value').value;
                const unit = document.querySelector('#interval-unit').value;
                cronExpression = `interval:${unit}:${value}`;
            }
            
            const name = document.querySelector('#schedule-name').value;
            const pathsText = document.querySelector('#schedule-paths').value;
            const scanPaths = pathsText.trim() ? pathsText.split('\n').filter(p => p.trim()) : [];
            const scanType = document.querySelector('#scan-type').value;
            
            try {
                const response = await fetch('/api/schedules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: name,
                        cron_expression: cronExpression,
                        scan_paths: scanPaths,
                        scan_type: scanType
                    })
                });
                
                if (response.ok) {
                    app.showNotification('Schedule created successfully', 'success');
                    app.closeModal('add-schedule-modal');
                    await app.loadSchedules();
                } else {
                    const error = await response.json();
                    throw new Error(error.error || 'Failed to create schedule');
                }
            } catch (error) {
                app.showNotification(error.message || 'Failed to create schedule', 'error');
            }
        });
    }
    
    // Setup exclusion input handlers
    const pathInput = document.querySelector('#new-excluded-path');
    const extensionInput = document.querySelector('#new-excluded-extension');
    
    if (pathInput) {
        pathInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                app.addExclusion('path');
            }
        });
    }
    
    if (extensionInput) {
        extensionInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                app.addExclusion('extension');
            }
        });
    }
});