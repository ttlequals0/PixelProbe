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
        this.toggleBtn = document.querySelector('.mobile-menu-btn');
        this.init();
    }

    init() {
        if (!this.toggleBtn) return;
        
        this.toggleBtn.addEventListener('click', () => this.toggle());
        
        if (this.overlay) {
            this.overlay.addEventListener('click', () => this.close());
        }

        // Close sidebar on navigation item click (mobile)
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', () => {
                if (window.innerWidth <= 768) {
                    this.close();
                }
            });
        });
    }

    toggle() {
        this.sidebar?.classList.toggle('active');
        this.overlay?.classList.toggle('active');
        document.body.style.overflow = this.sidebar?.classList.contains('active') ? 'hidden' : '';
    }

    close() {
        this.sidebar?.classList.remove('active');
        this.overlay?.classList.remove('active');
        document.body.style.overflow = '';
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
        return this.request('/file-changes');
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
        this.refreshInterval = setInterval(() => this.updateStats(), 5000);
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
    constructor(apiClient) {
        this.api = apiClient;
        this.progressBar = document.querySelector('.progress-bar');
        this.progressText = document.querySelector('.progress-text');
        this.progressContainer = document.querySelector('.progress-container');
        this.checkInterval = null;
    }

    show() {
        if (this.progressContainer) {
            this.progressContainer.style.display = 'block';
        }
    }

    hide() {
        if (this.progressContainer) {
            this.progressContainer.style.display = 'none';
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
        if (progressDetails && text) {
            // Show the current file or status message below the progress bar
            progressDetails.textContent = text;
        }
    }

    async startMonitoring() {
        this.show();
        this.updateScanButtons(true); // Disable scan buttons
        this.checkInterval = setInterval(async () => {
            try {
                const status = await this.api.getScanStatus();
                if (status.is_scanning) {
                    const progress = this.calculateProgress(status);
                    this.update(progress.percentage, progress.text);
                } else if (status.pending_files === 0 && status.scanning_files === 0) {
                    this.complete();
                }
            } catch (error) {
                console.error('Failed to check scan status:', error);
            }
        }, 1000);
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

    calculateProgress(status) {
        // 3-phase progress tracking as per version 1.30
        // Phase 1: Discovery (0-33%)
        // Phase 2: Adding to Database (33-66%)
        // Phase 3: Scanning (66-100%)
        
        let percentage = 0;
        let text = '';
        
        const phaseNumber = status.phase_number || 1;
        const totalPhases = status.total_phases || 3;
        const phaseCurrent = status.phase_current || 0;
        const phaseTotal = status.phase_total || 0;
        
        // Calculate percentage based on phase
        const phasePercentage = 100 / totalPhases;
        const phaseStart = (phaseNumber - 1) * phasePercentage;
        
        if (phaseTotal > 0) {
            const phaseProgress = (phaseCurrent / phaseTotal) * phasePercentage;
            percentage = Math.round(phaseStart + phaseProgress);
        } else {
            percentage = Math.round(phaseStart);
        }
        
        // Use the progress_message from backend if available
        if (status.progress_message) {
            text = status.progress_message;
        } else {
            // Fallback text generation
            const phase = status.phase || '';
            
            if (phaseNumber === 1) {
                text = `Phase 1 of ${totalPhases}: Discovery`;
            } else if (phaseNumber === 2) {
                text = `Phase 2 of ${totalPhases}: Adding to Database`;
            } else if (phaseNumber === 3) {
                text = `Phase 3 of ${totalPhases}: Scanning`;
            }
            
            if (phaseCurrent > 0 && phaseTotal > 0) {
                const phasePercent = ((phaseCurrent / phaseTotal) * 100).toFixed(1);
                text += ` (${phaseCurrent}/${phaseTotal} - ${phasePercent}%)`;
            }
            
            if (status.current_file) {
                const filename = status.current_file.split('/').pop();
                text += ` - ${filename}`;
            }
        }
        
        // Add ETA if available
        if (status.eta_seconds && status.eta_seconds > 0) {
            text += ` | ETA: ${this.formatTime(status.eta_seconds)}`;
        }
        
        return { percentage, text };
    }

    formatTime(seconds) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}m ${secs}s`;
    }

    complete() {
        this.update(100, 'Scan completed!');
        this.stopMonitoring();
        this.updateScanButtons(false); // Re-enable scan buttons
        setTimeout(() => this.hide(), 3000);
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
                sort_order: this.sortOrder,
                filter: this.filter,
                search: this.searchQuery
            };

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
        const statusClass = file.is_corrupted ? 'danger' : (file.has_warnings ? 'warning' : 'success');
        const statusText = file.is_corrupted ? 'CORRUPTED' : (file.has_warnings ? 'WARNING' : 'HEALTHY');
        
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
                    ${file.corruption_details || file.scan_output ? `
                        <span class="label">Details:</span>
                        <span class="value">${this.escapeHtml(file.corruption_details || file.scan_output || '')}</span>
                    ` : ''}
                </div>
                <div class="action-buttons">
                    <button class="btn btn-secondary" onclick="app.viewFile(${file.id})">
                        <i class="fas fa-eye"></i> View
                    </button>
                    <button class="btn btn-secondary" onclick="app.rescanFile(${file.id})">
                        <i class="fas fa-sync"></i> Rescan
                    </button>
                    ${file.corruption_details || file.scan_output ? `
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
        const statusClass = file.is_corrupted ? 'danger' : (file.has_warnings ? 'warning' : 'success');
        const statusText = file.is_corrupted ? 'Corrupted' : (file.has_warnings ? 'Warning' : 'Healthy');
        
        return `
            <tr>
                <td><input type="checkbox" class="file-checkbox" value="${file.id}" ${this.selectedFiles.has(file.id) ? 'checked' : ''}></td>
                <td><span class="badge badge-${statusClass}">${statusText}</span></td>
                <td class="file-path-cell" title="${this.escapeHtml(file.file_path)}">${this.escapeHtml(file.file_path)}</td>
                <td>${this.formatFileSize(file.file_size)}</td>
                <td>${file.file_type || 'N/A'}</td>
                <td>${file.scan_tool || 'N/A'}</td>
                <td class="text-truncate" title="${this.escapeHtml(file.corruption_details || file.scan_output || '')}">${this.escapeHtml(file.corruption_details || file.scan_output || '')}</td>
                <td>${this.formatDate(file.scan_date)}</td>
                <td class="action-buttons">
                    <button class="btn btn-sm btn-secondary" onclick="app.viewFile(${file.id})">
                        <i class="fas fa-eye"></i> View
                    </button>
                    <button class="btn btn-sm btn-secondary" onclick="app.rescanFile(${file.id})">
                        <i class="fas fa-sync"></i> Rescan
                    </button>
                    ${file.corruption_details || file.scan_output ? `
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

        const totalPages = Math.ceil(data.total / this.itemsPerPage);
        const currentPage = this.currentPage;
        
        let html = '';
        
        // Previous button
        html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <a class="page-link" href="#" data-page="prev">Previous</a>
        </li>`;
        
        // Page numbers
        const startPage = Math.max(1, currentPage - 2);
        const endPage = Math.min(totalPages, currentPage + 2);
        
        if (startPage > 1) {
            html += `<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>`;
            if (startPage > 2) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
        }
        
        for (let i = startPage; i <= endPage; i++) {
            html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
                <a class="page-link" href="#" data-page="${i}">${i}</a>
            </li>`;
        }
        
        if (endPage < totalPages) {
            if (endPage < totalPages - 1) {
                html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
            }
            html += `<li class="page-item"><a class="page-link" href="#" data-page="${totalPages}">${totalPages}</a></li>`;
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
        return date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
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
        this.progress = new ProgressManager(this.api);
        this.table = new TableManager(this.api);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    async init() {
        // Initialize components
        await this.stats.init();
        await this.table.init();
        
        // Check for ongoing scan
        try {
            const status = await this.api.getScanStatus();
            if (status.is_scanning) {
                this.progress.startMonitoring();
            }
        } catch (error) {
            console.error('Failed to check scan status on init:', error);
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
            this.progress.startMonitoring();
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
            this.showNotification(`Cleaned up ${result.deleted_count || 0} orphaned entries`, 'success');
            await this.stats.updateStats();
            await this.table.loadData();
        } catch (error) {
            this.showNotification('Failed to cleanup orphaned entries', 'error');
        }
    }

    async checkFileChanges() {
        try {
            const result = await this.api.checkFileChanges();
            const changedCount = result.changed_files?.length || 0;
            if (changedCount > 0) {
                this.showNotification(`Found ${changedCount} files with changes`, 'info');
            } else {
                this.showNotification('No file changes detected', 'success');
            }
        } catch (error) {
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
            content = `
                <video controls style="max-width: 100%; max-height: 60vh; height: auto; display: block; margin: 0 auto;">
                    <source src="/api/view/${file.id}" type="${fileType}">
                    Your browser does not support the video tag.
                </video>
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
            await this.api.scanFile(fileId);
            this.showNotification('File rescan started', 'success');
            await this.table.loadData();
        } catch (error) {
            this.showNotification('Failed to rescan file', 'error');
        }
    }

    async markFileAsGood(fileId) {
        try {
            await this.api.markAsGood([fileId]);
            this.showNotification('File marked as good', 'success');
            await this.table.loadData();
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
        if (info.database_stats) {
            html += '<h4>Database Statistics</h4>';
            html += '<div class="stats-section">';
            html += `<p>Total Files: ${info.database_stats.total_files?.toLocaleString() || 0}</p>`;
            html += `<p>Corrupted Files: ${info.database_stats.corrupted_files?.toLocaleString() || 0}</p>`;
            html += `<p>Healthy Files: ${info.database_stats.healthy_files?.toLocaleString() || 0}</p>`;
            html += `<p>Warning Files: ${info.database_stats.warning_files?.toLocaleString() || 0}</p>`;
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
        
        // Scan Stats
        if (info.scan_stats) {
            html += '<h4>Scan Statistics</h4>';
            html += '<div class="stats-section">';
            html += `<p>Total Scans: ${info.scan_stats.total_scans || 0}</p>`;
            html += `<p>Average Scan Time: ${info.scan_stats.average_scan_time?.toFixed(2) || 0}s</p>`;
            if (info.scan_stats.last_scan_date) {
                html += `<p>Last Scan: ${new Date(info.scan_stats.last_scan_date).toLocaleString()}</p>`;
            }
            html += '</div>';
        }
        
        // File System Statistics
        if (info.total_files_found || info.database_stats) {
            html += '<h4>File System Statistics</h4>';
            html += '<div class="stats-section">';
            const totalFiles = info.total_files_found || info.database_stats?.total_files || 0;
            const completedFiles = info.database_stats?.completed_files || 0;
            const percentageTracked = totalFiles > 0 ? 100 : 0;
            const percentageChecked = totalFiles > 0 ? ((completedFiles / totalFiles) * 100).toFixed(1) : 0;
            
            html += `<p>Total Files Found: ${totalFiles.toLocaleString()}</p>`;
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

    async exportCSV() {
        try {
            // Show loading notification
            const itemCount = this.table.selectedFiles.size > 0 ? 
                `${this.table.selectedFiles.size} selected files` : 
                'all files in current view';
            this.showNotification(`Generating CSV export for ${itemCount}...`, 'info');
            
            let requestBody = {};
            
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
                a.download = `pixelprobe_export_${new Date().toISOString().split('T')[0]}.csv`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
                
                this.showNotification('CSV export completed successfully', 'success');
            } else {
                throw new Error('Export failed');
            }
        } catch (error) {
            this.showNotification('Failed to export CSV', 'error');
        }
    }

    async deepScanSelected() {
        if (this.table.selectedFiles.size === 0) {
            this.showNotification('No files selected', 'warning');
            return;
        }

        try {
            const fileIds = Array.from(this.table.selectedFiles);
            // Get file paths for deep scan
            const filePaths = [];
            for (const fileId of fileIds) {
                const response = await fetch(`/api/scan-results/${fileId}`);
                if (response.ok) {
                    const result = await response.json();
                    filePaths.push(result.file_path);
                }
            }

            const response = await fetch('/api/scan-parallel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    file_paths: filePaths,
                    deep_scan: true
                })
            });

            if (response.ok) {
                this.showNotification(`Deep scan started for ${fileIds.length} files`, 'success');
                this.progress.startMonitoring();
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
            // Get file paths for rescan
            const filePaths = [];
            for (const fileId of fileIds) {
                const response = await fetch(`/api/scan-results/${fileId}`);
                if (response.ok) {
                    const result = await response.json();
                    filePaths.push(result.file_path);
                }
            }

            const response = await fetch('/api/scan-parallel', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ 
                    file_paths: filePaths,
                    deep_scan: false
                })
            });

            if (response.ok) {
                this.showNotification(`Rescan started for ${fileIds.length} files`, 'success');
                this.progress.startMonitoring();
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
        const output = file.corruption_details || file.scan_output || 'No scan output available';
        
        modalBody.innerHTML = `
            <div class="scan-output-details">
                <h4>File: ${this.escapeHtml(file.file_path)}</h4>
                <p><strong>Status:</strong> ${file.is_corrupted ? 'Corrupted' : (file.has_warnings ? 'Warning' : 'Healthy')}</p>
                <p><strong>Tool:</strong> ${file.scan_tool || 'N/A'}</p>
                <p><strong>Scanned:</strong> ${file.scan_date ? new Date(file.scan_date).toLocaleString() : 'N/A'}</p>
                <hr>
                <h4>Scan Output:</h4>
                <pre class="scan-output-text">${this.escapeHtml(output)}</pre>
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

}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new PixelProbeApp();
    window.app.init();
});