// PixelProbe Modern UI JavaScript

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
                ...options
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            return await response.json();
        } catch (error) {
            console.error('API request failed:', error);
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

    // Scan methods
    async getScanResults(params = {}) {
        const queryString = new URLSearchParams(params).toString();
        return this.request(`/scan-results${queryString ? '?' + queryString : ''}`);
    }

    async getScanStatus() {
        return this.request('/scan-status');
    }

    async startScan() {
        return this.request('/scan-all', {
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
        return this.request('/cancel-scan', {
            method: 'POST',
            body: JSON.stringify({})
        });
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
        return this.request('/export-csv', {
            method: 'POST',
            body: JSON.stringify({ file_ids: fileIds })
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
            this.renderStats(stats);
        } catch (error) {
            console.error('Failed to update stats:', error);
        }
    }

    renderStats(stats) {
        // Update stat cards
        this.updateStatCard('total-files', stats.total_files);
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
                    progressTitle.textContent = 'File Changes Check Progress';
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

    update(percentage, text, details = '') {
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
            if (details) {
                progressDetails.textContent = `${text} - ${details}`;
            } else if (text) {
                progressDetails.textContent = text;
            }
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
        
        this.checkInterval = setInterval(async () => {
            try {
                let status;
                let isRunning = false;
                
                // Get status based on operation type
                if (operationType === 'scan') {
                    status = await this.api.getScanStatus();
                    isRunning = status.is_scanning;
                } else if (operationType === 'cleanup') {
                    status = await this.api.getCleanupStatus();
                    isRunning = status.is_running;
                    // Debug log for cleanup status
                    console.log('Cleanup status:', status);
                } else if (operationType === 'file-changes') {
                    status = await this.api.getFileChangesStatus();
                    isRunning = status.is_running;
                }
                
                if (isRunning) {
                    const progress = this.calculateProgress(status, operationType);
                    this.update(progress.percentage, progress.text, progress.details);
                } else if (status.phase === 'complete' || status.phase === 'completed' || status.phase === 'cancelled' || status.phase === 'error') {
                    // Operation is complete - show completion state
                    this.complete(operationType, status);
                } else {
                    // Still initializing or in transition - keep showing progress
                    const progress = this.calculateProgress(status, operationType);
                    this.update(progress.percentage, progress.text, progress.details);
                }
            } catch (error) {
                console.error(`Failed to check ${operationType} status:`, error);
            }
        }, 1000); // Poll every 1 second
    }
    
    updateCleanupButton(isRunning) {
        const cleanupButton = document.querySelector('[onclick*="cleanupOrphaned"]');
        if (cleanupButton) {
            cleanupButton.disabled = isRunning;
            cleanupButton.innerHTML = isRunning ? 
                '<i class="fas fa-spinner fa-spin"></i> Cleaning up...' : 
                '<i class="fas fa-broom"></i> Cleanup Orphaned';
        }
    }
    
    updateFileChangesButton(isRunning) {
        const fileChangesButton = document.querySelector('[onclick*="checkFileChanges"]');
        if (fileChangesButton) {
            fileChangesButton.disabled = isRunning;
            fileChangesButton.innerHTML = isRunning ? 
                '<i class="fas fa-spinner fa-spin"></i> Checking...' : 
                '<i class="fas fa-exchange-alt"></i> Check File Changes';
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
    }

    calculateProgress(status, operationType = 'scan') {
        let percentage = 0;
        let text = '';
        let details = '';
        let eta = '';
        
        // Calculate ETA if we have timing data
        // Prefer backend-provided ETA and speed
        if (status.eta) {
            const etaDate = new Date(status.eta);
            const now = new Date();
            const remainingMs = etaDate - now;
            
            if (remainingMs > 0) {
                const remainingSeconds = Math.floor(remainingMs / 1000);
                eta = this.formatTime(remainingSeconds);
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
                
                // Build details string with all information
                const parts = [];
                
                // Add file count
                if (status.current > 0 && status.total > 0) {
                    parts.push(`${status.current.toLocaleString()} of ${status.total.toLocaleString()} files`);
                } else if (phaseCurrent > 0 && phaseTotal > 0) {
                    parts.push(`${phaseCurrent.toLocaleString()} of ${phaseTotal.toLocaleString()} files`);
                }
                
                // Add speed if available
                if (status.files_per_second > 0) {
                    parts.push(`${status.files_per_second} files/sec`);
                }
                
                // Add current file
                if (status.file || status.current_file) {
                    const currentFile = status.file || status.current_file;
                    parts.push(`Scanning: ${currentFile.split('/').pop()}`);
                }
                
                // Add ETA
                if (eta) {
                    parts.push(`ETA: ${eta}`);
                }
                
                details = parts.join(' - ');
            }
        } else if (operationType === 'cleanup') {
            // Use the progress percentage directly from the backend
            // Backend handles 3-phase weighting: scanning → checking → deleting
            percentage = Math.round(status.progress_percentage || 0);
            
            text = status.progress_message || `Phase ${status.phase_number || 1} of ${status.total_phases || 3}`;
            
            const parts = [];
            
            // Add file count for cleanup
            if (status.current > 0 && status.total > 0) {
                parts.push(`${status.current} of ${status.total} files`);
            }
            
            if (status.current_file) {
                if (status.phase === 'deleting_entries') {
                    parts.push(status.current_file);
                } else {
                    parts.push(`Checking: ${status.current_file.split('/').pop()}`);
                }
            }
            
            if (status.orphaned_found > 0) {
                if (status.phase === 'deleting_entries') {
                    parts.push(`Deleting ${status.orphaned_found} orphaned entries`);
                } else {
                    parts.push(`Found ${status.orphaned_found} orphaned files`);
                }
            }
            
            // Add ETA
            if (eta) {
                parts.push(`ETA: ${eta}`);
            }
            
            details = parts.join(' - ');
            
        } else if (operationType === 'file-changes') {
            // Use the progress percentage directly from the backend
            percentage = Math.round(status.progress_percentage || 0);
            
            text = status.progress_message || 'Checking for file changes...';
            
            const parts = [];
            
            // Add file count
            if (status.current > 0 && status.total > 0) {
                parts.push(`${status.current} of ${status.total} files`);
            }
            
            if (status.current_file) {
                parts.push(`Checking: ${status.current_file.split('/').pop()}`);
            }
            
            if (status.changes_found > 0) {
                parts.push(`Found ${status.changes_found} changed files`);
            }
            
            // Add ETA
            if (eta) {
                parts.push(`ETA: ${eta}`);
            }
            
            details = parts.join(' - ');
        }
        
        return { percentage, text, details };
    }

    formatTime(seconds) {
        const hours = Math.floor(seconds / 3600);
        const mins = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);
        
        if (hours > 0) {
            return `${hours}h ${mins}m`;
        } else if (mins > 0) {
            return `${mins}m ${secs}s`;
        } else {
            return `${secs}s`;
        }
    }

    async complete(operationType = 'scan', status = null) {
        // Always show 100% when operation completes
        let completionMessage = '';
        
        // Debug log the status on completion
        console.log(`${operationType} complete with status:`, status);
        
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
                this.app.showNotification('File changes check cancelled', 'info');
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
            completionMessage = `File changes check completed! Found ${changesFound} changed files.`;
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
        this.selectedFiles = new Set();
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

            // Map frontend filter values to backend parameters
            if (this.filter) {
                switch (this.filter) {
                    case 'corrupted':
                        params.is_corrupted = 'true';
                        break;
                    case 'healthy':
                        params.is_corrupted = 'false';
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
            console.error('Failed to load table data:', error);
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
            
            // Re-bind checkbox events
            tbody.querySelectorAll('.file-checkbox').forEach(cb => {
                cb.addEventListener('change', (e) => {
                    const fileId = parseInt(e.target.value);
                    if (e.target.checked) {
                        this.selectedFiles.add(fileId);
                    } else {
                        this.selectedFiles.delete(fileId);
                    }
                    this.updateSelectionUI();
                });
            });
        }
    }

    renderMobileCards(data) {
        const container = document.querySelector('.mobile-results');
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
        
        // Re-bind checkbox events for mobile
        container.querySelectorAll('.file-checkbox').forEach(cb => {
            cb.addEventListener('change', (e) => {
                const fileId = parseInt(e.target.value);
                if (e.target.checked) {
                    this.selectedFiles.add(fileId);
                } else {
                    this.selectedFiles.delete(fileId);
                }
                this.updateSelectionUI();
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
                    ${file.corruption_details || file.scan_output || file.error_message || file.warning_details ? `
                        <span class="label">Details:</span>
                        <span class="value">${this.escapeHtml(file.corruption_details || file.scan_output || file.error_message || file.warning_details || '')}</span>
                    ` : ''}
                </div>
                <div class="action-buttons">
                    <button class="btn btn-secondary" onclick="app.viewFile(${file.id})">
                        <i class="fas fa-eye"></i> View
                    </button>
                    <button class="btn btn-secondary" onclick="app.rescanFile(${file.id})">
                        <i class="fas fa-sync"></i> Rescan
                    </button>
                    ${file.corruption_details || file.scan_output || file.error_message || file.warning_details ? `
                        <button class="btn btn-secondary" onclick="app.viewScanOutput(${file.id})">
                            <i class="fas fa-file-alt"></i> Details
                        </button>
                    ` : ''}
                    <button class="btn btn-secondary" onclick="app.downloadFile(${file.id})">
                        <i class="fas fa-download"></i> Download
                    </button>
                    <button class="btn btn-primary" onclick="app.markFileAsGood(${file.id})">
                        <i class="fas fa-check"></i> Mark Good
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
                    <button class="btn btn-sm btn-secondary" onclick="app.rescanFile(${file.id})">
                        <i class="fas fa-sync"></i> Rescan
                    </button>
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

    updateSelectionUI() {
        const count = this.selectedFiles.size;
        const selectionInfo = document.querySelector('.selection-info');
        if (selectionInfo) {
            selectionInfo.textContent = count > 0 ? `${count} files selected` : '';
        }
        
        // Enable/disable bulk action buttons
        const buttons = ['#mark-good-btn', '#deep-scan-btn', '#rescan-btn', '#download-btn'];
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
        const date = new Date(dateString);
        
        // The backend now sends dates in the configured timezone
        // Display them as-is without converting to browser's local time
        const options = {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hour12: false
        };
        
        // Parse the ISO string to get timezone offset
        const match = dateString.match(/([+-]\d{2}:\d{2}|Z)$/);
        if (match) {
            const offset = match[0];
            if (offset === 'Z') {
                options.timeZone = 'UTC';
            }
        }
        
        return date.toLocaleString('en-US', options);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialize app
class PixelProbeApp {
    constructor() {
        this.api = new APIClient();
        this.theme = new ThemeManager();
        this.sidebar = new SidebarManager();
        this.stats = new StatsDashboard(this.api);
        this.progress = new ProgressManager(this.api, this);
        this.table = new TableManager(this.api);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    formatScanType(type) {
        const types = {
            'normal': 'Normal Scan',
            'orphan': 'Orphan Cleanup',
            'file_changes': 'File Changes Scan'
        };
        return types[type] || type;
    }
    
    handleVideoError(fileId) {
        console.error(`Video failed to load for file ${fileId}`);
        const video = document.getElementById(`video-player-${fileId}`);
        const errorDiv = document.getElementById(`video-error-${fileId}`);
        
        if (video) {
            // Log detailed error information
            console.error('Video error details:', {
                error: video.error,
                networkState: video.networkState,
                readyState: video.readyState,
                currentSrc: video.currentSrc
            });
            
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
        
        // Check for ongoing operations
        try {
            // Check for ongoing scan
            const scanStatus = await this.api.getScanStatus();
            if (scanStatus.is_scanning) {
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
            console.error('Failed to check operation status on init:', error);
        }
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
            this.progress.startMonitoring('scan');
            this.showNotification('Scan started', 'success');
        } catch (error) {
            this.showNotification('Failed to start scan', 'error');
        }
    }

    async cleanupOrphaned() {
        if (!confirm('Remove database entries for files that no longer exist on disk?')) {
            return;
        }
        
        try {
            const result = await this.api.cleanupOrphaned();
            console.log('Cleanup response:', result);
            
            if (result.status === 'started') {
                console.log('Starting progress monitoring for cleanup');
                this.showNotification('Cleanup started...', 'info');
                // Start monitoring cleanup progress
                this.progress.operationType = 'cleanup';
                this.progress.startMonitoring('cleanup');
                
                // Also do a manual check after 1 second to debug
                setTimeout(async () => {
                    try {
                        const status = await this.api.getCleanupStatus();
                        console.log('Cleanup status after 1s:', status);
                    } catch (e) {
                        console.error('Failed to get cleanup status:', e);
                    }
                }, 1000);
            } else {
                // This is the old synchronous response - still handle it
                console.log('Got synchronous response, not async');
                this.showNotification(`Cleaned up ${result.deleted_count || 0} orphaned entries`, 'success');
                await this.stats.updateStats();
                await this.table.loadData();
            }
        } catch (error) {
            console.error('Cleanup error:', error);
            this.showNotification('Failed to cleanup orphaned entries', 'error');
        }
    }

    async checkFileChanges() {
        try {
            const result = await this.api.checkFileChanges();
            console.log('File changes response:', result);
            
            if (result.status === 'started') {
                this.showNotification('File changes check started...', 'info');
                // Start monitoring file changes progress
                this.progress.operationType = 'file-changes';
                this.progress.startMonitoring('file-changes');
            } else {
                // This is the old synchronous response - still handle it
                const changedCount = result.changed_files?.length || 0;
                if (changedCount > 0) {
                    this.showNotification(`Found ${changedCount} files with changes`, 'info');
                } else {
                    this.showNotification('No file changes detected', 'success');
                }
            }
        } catch (error) {
            console.error('File changes error:', error);
            this.showNotification('Failed to check file changes', 'error');
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
                           onloadedmetadata="console.log('Video metadata loaded for file ${file.id}')"
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
                <audio controls style="width: 100%; display: block; margin: 0 auto;">
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
                // Use scan-parallel which will mark as pending and start full scan
                const scanResponse = await this.api.request('/scan-parallel', {
                    method: 'POST',
                    body: JSON.stringify({ 
                        file_paths: [file.file_path],
                        deep_scan: false
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

    async showSystemStats() {
        try {
            const info = await this.api.getSystemInfo();
            this.showSystemStatsModal(info);
        } catch (error) {
            this.showNotification('Failed to load system info', 'error');
        }
    }

    showSystemStatsModal(info) {
        const modal = document.querySelector('#system-stats-modal');
        if (!modal) return;
        
        const modalBody = modal.querySelector('.modal-body');
        if (!modalBody) return;
        
        // Format the system info
        let html = '<div class="system-stats-content">';
        
        // Database Stats
        if (info.database) {
            html += '<h4>Database Statistics</h4>';
            html += '<div class="stats-section">';
            html += `<p>Total Files: ${info.database.total_files?.toLocaleString() || 0}</p>`;
            html += `<p>Completed Files: ${info.database.completed_files?.toLocaleString() || 0}</p>`;
            html += `<p>Corrupted Files: ${info.database.corrupted_files?.toLocaleString() || 0}</p>`;
            html += `<p>Healthy Files: ${info.database.healthy_files?.toLocaleString() || 0}</p>`;
            html += `<p>Warning Files: ${info.database.warning_files?.toLocaleString() || 0}</p>`;
            html += `<p>Error Files: ${info.database.error_files?.toLocaleString() || 0}</p>`;
            html += `<p>Marked as Good: ${info.database.marked_as_good?.toLocaleString() || 0}</p>`;
            html += '</div>';
        }
        
        // Monitored Paths
        if (info.monitored_paths && info.monitored_paths.length > 0) {
            html += '<h4>Monitored Paths</h4>';
            html += '<div class="stats-section">';
            info.monitored_paths.forEach(path => {
                html += `<p>${path.path}: ${path.file_count?.toLocaleString() || 0} files`;
                if (!path.exists) html += ' (not accessible)';
                html += '</p>';
            });
            html += '</div>';
        }
        
        // Performance Stats
        if (info.database && info.database.performance) {
            const perf = info.database.performance;
            html += '<h4>Scan Performance</h4>';
            html += '<div class="stats-section">';
            html += `<p>Total Scans: ${perf.total_scans?.toLocaleString() || 0}</p>`;
            html += `<p>Average Days Since Scan: ${perf.avg_days_since_scan?.toFixed(1) || 0} days</p>`;
            if (perf.newest_scan) {
                html += `<p>Last Scan: ${new Date(perf.newest_scan).toLocaleString()}</p>`;
            }
            if (perf.oldest_scan) {
                html += `<p>First Scan: ${new Date(perf.oldest_scan).toLocaleString()}</p>`;
            }
            html += '</div>';
        }
        
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
        
        // File System Statistics
        if (info.filesystem || info.database) {
            html += '<h4>File System Statistics</h4>';
            html += '<div class="stats-section">';
            const totalFiles = info.filesystem?.total_files || info.database?.total_files || 0;
            const completedFiles = info.database?.completed_files || 0;
            const percentageTracked = totalFiles > 0 ? 100 : 0;
            const percentageChecked = totalFiles > 0 ? ((completedFiles / totalFiles) * 100).toFixed(1) : 0;
            
            html += `<p>Total Files Found: ${totalFiles.toLocaleString()}</p>`;
            html += `<p>Paths Monitored: ${info.filesystem?.paths_monitored || 0}</p>`;
            html += `<p>Percentage Tracked: ${percentageTracked}%</p>`;
            html += `<p>Percentage Checked: ${percentageChecked}%</p>`;
            html += '</div>';
        }
        
        html += '</div>';
        
        modalBody.innerHTML = html;
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
                console.error('Error viewing report:', error);
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
                per_page: 20,
                scan_type: typeFilter,
                status: statusFilter,
                sort_order: 'desc'
            });
            
            const response = await fetch(`/api/scan-reports?${params}`);
            if (!response.ok) throw new Error('Failed to load reports');
            
            const data = await response.json();
            
            // Update table
            const tbody = document.querySelector('#scan-reports-table tbody');
            if (!tbody) return;
            
            tbody.innerHTML = '';
            
            if (data.reports.length === 0) {
                tbody.innerHTML = '<tr><td colspan="8" class="text-center">No reports found</td></tr>';
                return;
            }
            
            // Render reports
            data.reports.forEach(report => {
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
                    <td>
                        <input type="checkbox" 
                               data-report-id="${report.report_id}" 
                               data-filename="${report.filename || ''}"
                               onchange="app.toggleReportSelection('${report.report_id}', this.checked)">
                    </td>
                    <td>${new Date(report.start_time).toLocaleString()}</td>
                    <td>${scanType}</td>
                    <td><span class="${statusClass}">${report.status}</span></td>
                    <td>${report.duration_formatted || 'N/A'}</td>
                    <td>${filesInfo}</td>
                    <td>${issuesInfo}</td>
                    <td>
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
            });
            
            // Update pagination
            this.updateScanReportsPagination(data.page, data.pages, data.total);
            
        } catch (error) {
            console.error('Failed to load scan reports:', error);
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
                detailsHtml += `<tr><th>Directories:</th><td>${report.directories_scanned.join('<br>')}</td></tr>`;
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
            console.error('Failed to view report:', error);
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
            console.error('Failed to export report:', error);
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
            console.error('Failed to delete report:', error);
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
            console.error('Download error:', error);
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
            console.error('Delete error:', error);
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
            
            const response = await fetch('/api/export-csv', {
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

    async deepScanSelected() {
        if (this.table.selectedFiles.size === 0) {
            this.showNotification('No files selected', 'warning');
            return;
        }

        try {
            const fileIds = Array.from(this.table.selectedFiles);
            
            // First, reset the selected files for deep scanning
            const resetResponse = await fetch('/api/reset-for-rescan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    type: 'selected',
                    file_ids: fileIds
                })
            });

            if (!resetResponse.ok) {
                throw new Error('Failed to reset files for deep scan');
            }

            // Get file paths for the selected files
            const filePaths = [];
            for (const fileId of fileIds) {
                const response = await fetch(`/api/scan-results/${fileId}`);
                if (response.ok) {
                    const result = await response.json();
                    filePaths.push(result.file_path);
                }
            }

            // Start deep scan on only the selected files
            const scanResponse = await fetch('/api/scan-parallel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    file_paths: filePaths,
                    deep_scan: true,
                    force_rescan: true  // Force rescan for deep scan
                })
            });

            if (scanResponse.ok) {
                this.showNotification(`Deep scan started for ${fileIds.length} files`, 'success');
                // Start monitoring with 'scan' operation type to ensure auto-refresh
                this.progress.startMonitoring('scan');
            } else {
                throw new Error('Deep scan failed');
            }
        } catch (error) {
            this.showNotification('Failed to start deep scan', 'error');
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
            
            // First, reset the selected files for rescanning
            const resetResponse = await fetch('/api/reset-for-rescan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    type: 'selected',
                    file_ids: fileIds
                })
            });

            if (!resetResponse.ok) {
                throw new Error('Failed to reset files for rescan');
            }

            // Get file paths for the selected files
            const filePaths = [];
            for (const fileId of fileIds) {
                const response = await fetch(`/api/scan-results/${fileId}`);
                if (response.ok) {
                    const result = await response.json();
                    filePaths.push(result.file_path);
                }
            }

            // Start scan on only the selected files
            const scanResponse = await fetch('/api/scan-parallel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    file_paths: filePaths,
                    deep_scan: false,
                    force_rescan: true  // Force rescan to actually re-scan the files
                })
            });

            if (scanResponse.ok) {
                this.showNotification(`Rescan started for ${fileIds.length} files`, 'success');
                // Start monitoring with 'scan' operation type to ensure auto-refresh
                this.progress.startMonitoring('scan');
            } else {
                throw new Error('Rescan failed');
            }
        } catch (error) {
            this.showNotification('Failed to start rescan', 'error');
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
            console.error('Error loading scan output:', error);
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
                if (status.is_scanning) {
                    await this.api.cancelScan();
                    this.showNotification('Scan cancellation requested', 'info');
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
                    this.showNotification('File changes check cancellation requested', 'info');
                }
            } else {
                this.showNotification('No operation is currently running', 'warning');
            }
        } catch (error) {
            console.error('Failed to cancel operation:', error);
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
                                    <button class="btn btn-sm ${schedule.is_active ? 'btn-warning' : 'btn-success'}" 
                                            onclick="app.toggleSchedule(${schedule.id}, ${!schedule.is_active})">
                                        ${schedule.is_active ? 'Disable' : 'Enable'}
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
            console.error('Failed to load schedules:', error);
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
            console.error('Failed to toggle schedule:', error);
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
            console.error('Failed to delete schedule:', error);
            this.showNotification('Failed to delete schedule', 'error');
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
            console.error('Failed to load exclusions:', error);
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
            console.error('Failed to add exclusion:', error);
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
            console.error('Failed to remove exclusion:', error);
            this.showNotification('Failed to remove exclusion', 'error');
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
                console.error('Failed to create schedule:', error);
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