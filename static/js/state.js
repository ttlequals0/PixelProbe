/**
 * Frontend State Management - P2 Implementation from Audit Plan
 * Centralized state management for the PixelProbe application
 */

class AppState {
    constructor() {
        // Scan state
        this.isScanning = false;
        this.activeScanType = null;
        this.scanProgress = 0;
        this.scanPhase = null;
        this.eta = null;
        this.scanStartTime = null;
        
        // UI state
        this.activeTab = 'dashboard';
        this.selectedFiles = [];
        this.filterStatus = 'all'; // all, corrupted, healthy, pending, error
        this.searchQuery = '';
        this.sortBy = 'discovered_date';
        this.sortOrder = 'desc';
        
        // Stats state
        this.stats = {
            totalFiles: 0,
            corruptedFiles: 0,
            healthyFiles: 0,
            pendingFiles: 0,
            errorFiles: 0,
            lastUpdated: null
        };
        
        // Schedule state
        this.schedules = [];
        this.nextScheduledScan = null;
        
        // Error state
        this.lastError = null;
        this.errorCount = 0;
        
        // Listeners for state changes
        this.listeners = new Map();
        
        // Initialize state from localStorage if available
        this.loadPersistedState();
    }
    
    // State persistence methods
    loadPersistedState() {
        try {
            const persisted = localStorage.getItem('pixelprobe_state');
            if (persisted) {
                const state = JSON.parse(persisted);
                // Only restore UI preferences, not active scan state
                this.filterStatus = state.filterStatus || 'all';
                this.searchQuery = state.searchQuery || '';
                this.sortBy = state.sortBy || 'discovered_date';
                this.sortOrder = state.sortOrder || 'desc';
                this.activeTab = state.activeTab || 'dashboard';
            }
        } catch (e) {
            console.warn('Failed to load persisted state:', e);
        }
    }
    
    persistState() {
        try {
            const stateToPersist = {
                filterStatus: this.filterStatus,
                searchQuery: this.searchQuery,
                sortBy: this.sortBy,
                sortOrder: this.sortOrder,
                activeTab: this.activeTab
            };
            localStorage.setItem('pixelprobe_state', JSON.stringify(stateToPersist));
        } catch (e) {
            console.warn('Failed to persist state:', e);
        }
    }
    
    // Event listener management
    subscribe(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);
        
        // Return unsubscribe function
        return () => {
            const callbacks = this.listeners.get(event);
            const index = callbacks.indexOf(callback);
            if (index > -1) {
                callbacks.splice(index, 1);
            }
        };
    }
    
    notifyListeners(event, data = {}) {
        const callbacks = this.listeners.get(event) || [];
        callbacks.forEach(callback => {
            try {
                callback(data);
            } catch (e) {
                console.error(`Error in listener for ${event}:`, e);
            }
        });
    }
    
    // Scan state management
    startScan(scanType) {
        this.isScanning = true;
        this.activeScanType = scanType;
        this.scanProgress = 0;
        this.scanPhase = 'initializing';
        this.eta = 'Calculating...';
        this.scanStartTime = new Date();
        
        this.notifyListeners('scan:started', {
            scanType,
            startTime: this.scanStartTime
        });
        
        this.disableAllScanButtons();
        this.updateUI();
    }
    
    updateScanProgress(data) {
        this.scanProgress = data.percentage || 0;
        this.scanPhase = data.phase || this.scanPhase;
        this.eta = data.eta || this.calculateETA();
        
        this.notifyListeners('scan:progress', {
            progress: this.scanProgress,
            phase: this.scanPhase,
            eta: this.eta,
            filesProcessed: data.filesProcessed,
            totalFiles: data.totalFiles
        });
        
        this.updateProgressBar();
    }
    
    completeScan(results) {
        this.isScanning = false;
        const duration = new Date() - this.scanStartTime;
        
        this.notifyListeners('scan:completed', {
            scanType: this.activeScanType,
            duration,
            results
        });
        
        this.activeScanType = null;
        this.scanProgress = 0;
        this.scanPhase = null;
        this.eta = null;
        this.scanStartTime = null;
        
        this.enableAllScanButtons();
        this.updateStats();
        this.updateUI();
    }
    
    cancelScan() {
        if (!this.isScanning) return;
        
        this.notifyListeners('scan:cancelled', {
            scanType: this.activeScanType,
            progress: this.scanProgress
        });
        
        this.isScanning = false;
        this.activeScanType = null;
        this.scanProgress = 0;
        this.scanPhase = null;
        this.eta = null;
        this.scanStartTime = null;
        
        this.enableAllScanButtons();
        this.updateUI();
    }
    
    // UI state management
    setActiveTab(tabName) {
        this.activeTab = tabName;
        this.notifyListeners('ui:tabChanged', { tab: tabName });
        this.persistState();
        this.updateUI();
    }
    
    setFilter(filterStatus) {
        this.filterStatus = filterStatus;
        this.notifyListeners('ui:filterChanged', { filter: filterStatus });
        this.persistState();
        this.refreshFileList();
    }
    
    setSearchQuery(query) {
        this.searchQuery = query;
        this.notifyListeners('ui:searchChanged', { query });
        this.persistState();
        
        // Debounce search
        clearTimeout(this.searchTimeout);
        this.searchTimeout = setTimeout(() => {
            this.refreshFileList();
        }, 300);
    }
    
    setSorting(sortBy, sortOrder = null) {
        this.sortBy = sortBy;
        if (sortOrder) {
            this.sortOrder = sortOrder;
        } else {
            // Toggle order if same column clicked
            if (this.sortBy === sortBy) {
                this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
            } else {
                this.sortOrder = 'desc';
            }
        }
        
        this.notifyListeners('ui:sortChanged', {
            sortBy: this.sortBy,
            sortOrder: this.sortOrder
        });
        
        this.persistState();
        this.refreshFileList();
    }
    
    selectFile(fileId) {
        if (!this.selectedFiles.includes(fileId)) {
            this.selectedFiles.push(fileId);
            this.notifyListeners('files:selected', { files: this.selectedFiles });
        }
    }
    
    deselectFile(fileId) {
        const index = this.selectedFiles.indexOf(fileId);
        if (index > -1) {
            this.selectedFiles.splice(index, 1);
            this.notifyListeners('files:selected', { files: this.selectedFiles });
        }
    }
    
    clearSelection() {
        this.selectedFiles = [];
        this.notifyListeners('files:selected', { files: [] });
    }
    
    // Stats management
    updateStats(newStats = null) {
        if (newStats) {
            this.stats = {
                ...this.stats,
                ...newStats,
                lastUpdated: new Date()
            };
        } else {
            // Fetch stats from server
            this.fetchStats();
        }
        
        this.notifyListeners('stats:updated', this.stats);
        this.updateStatsDisplay();
    }
    
    // Schedule management
    updateSchedules(schedules) {
        this.schedules = schedules;
        this.nextScheduledScan = this.findNextScheduledScan();
        this.notifyListeners('schedules:updated', {
            schedules: this.schedules,
            next: this.nextScheduledScan
        });
    }
    
    findNextScheduledScan() {
        // Find the next scheduled scan time
        if (!this.schedules.length) return null;
        
        const now = new Date();
        let nextScan = null;
        
        this.schedules.forEach(schedule => {
            if (schedule.is_active && schedule.next_run) {
                const nextRun = new Date(schedule.next_run);
                if (nextRun > now && (!nextScan || nextRun < nextScan)) {
                    nextScan = nextRun;
                }
            }
        });
        
        return nextScan;
    }
    
    // Error handling
    setError(error) {
        this.lastError = error;
        this.errorCount++;
        
        this.notifyListeners('error:occurred', {
            error,
            count: this.errorCount
        });
        
        // Show error notification
        this.showErrorNotification(error);
    }
    
    clearError() {
        this.lastError = null;
        this.notifyListeners('error:cleared', {});
    }
    
    // Helper methods
    calculateETA() {
        if (!this.scanStartTime || this.scanProgress === 0) {
            return 'Calculating...';
        }
        
        const elapsed = (new Date() - this.scanStartTime) / 1000; // seconds
        const rate = this.scanProgress / elapsed;
        const remaining = (100 - this.scanProgress) / rate;
        
        if (remaining < 60) {
            return `${Math.round(remaining)} seconds`;
        } else if (remaining < 3600) {
            return `${Math.round(remaining / 60)} minutes`;
        } else {
            return `${Math.round(remaining / 3600)} hours`;
        }
    }
    
    disableAllScanButtons() {
        document.querySelectorAll('[data-scan-button]').forEach(btn => {
            btn.disabled = true;
            btn.classList.add('disabled');
        });
    }
    
    enableAllScanButtons() {
        document.querySelectorAll('[data-scan-button]').forEach(btn => {
            btn.disabled = false;
            btn.classList.remove('disabled');
        });
    }
    
    updateProgressBar() {
        const progressBar = document.getElementById('scan-progress-bar');
        if (progressBar) {
            progressBar.style.width = `${this.scanProgress}%`;
            progressBar.textContent = `${Math.round(this.scanProgress)}%`;
        }
        
        const phaseText = document.getElementById('scan-phase');
        if (phaseText && this.scanPhase) {
            phaseText.textContent = `Phase: ${this.scanPhase}`;
        }
        
        const etaText = document.getElementById('scan-eta');
        if (etaText && this.eta) {
            etaText.textContent = `ETA: ${this.eta}`;
        }
    }
    
    updateStatsDisplay() {
        // Update stats cards
        const elements = {
            'total-files': this.stats.totalFiles,
            'corrupted-files': this.stats.corruptedFiles,
            'healthy-files': this.stats.healthyFiles,
            'pending-files': this.stats.pendingFiles,
            'error-files': this.stats.errorFiles
        };
        
        Object.entries(elements).forEach(([id, value]) => {
            const elem = document.getElementById(id);
            if (elem) {
                elem.textContent = value.toLocaleString();
            }
        });
    }
    
    showErrorNotification(error) {
        // Create or update error notification
        let notification = document.getElementById('error-notification');
        if (!notification) {
            notification = document.createElement('div');
            notification.id = 'error-notification';
            notification.className = 'alert alert-danger alert-dismissible fade show';
            notification.style.position = 'fixed';
            notification.style.top = '20px';
            notification.style.right = '20px';
            notification.style.zIndex = '9999';
            document.body.appendChild(notification);
        }
        
        notification.innerHTML = `
            <strong>Error:</strong> ${error}
            <button type="button" class="close" data-dismiss="alert">
                <span>&times;</span>
            </button>
        `;
        
        // Auto-hide after 5 seconds
        setTimeout(() => {
            notification.style.display = 'none';
        }, 5000);
    }
    
    // API calls
    async fetchStats() {
        try {
            const response = await fetch('/api/stats');
            const data = await response.json();
            this.updateStats({
                totalFiles: data.total_files,
                corruptedFiles: data.corrupted_files,
                healthyFiles: data.healthy_files,
                pendingFiles: data.pending_files,
                errorFiles: data.error_files || 0
            });
        } catch (error) {
            console.error('Failed to fetch stats:', error);
            this.setError('Failed to fetch statistics');
        }
    }
    
    async refreshFileList() {
        const params = new URLSearchParams({
            filter: this.filterStatus,
            search: this.searchQuery,
            sort_by: this.sortBy,
            sort_order: this.sortOrder
        });
        
        try {
            const response = await fetch(`/api/scan-results?${params}`);
            const data = await response.json();
            this.notifyListeners('files:refreshed', data);
        } catch (error) {
            console.error('Failed to refresh file list:', error);
            this.setError('Failed to refresh file list');
        }
    }
    
    updateUI() {
        // Trigger a general UI update
        this.notifyListeners('ui:updated', {
            isScanning: this.isScanning,
            activeScanType: this.activeScanType,
            activeTab: this.activeTab
        });
    }
}

// Create global app state instance
window.appState = new AppState();

// Initialize state when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Set up initial subscriptions
    window.appState.subscribe('scan:started', (data) => {
        console.log('Scan started:', data);
    });
    
    window.appState.subscribe('scan:completed', (data) => {
        console.log('Scan completed:', data);
    });
    
    window.appState.subscribe('error:occurred', (data) => {
        console.error('Application error:', data);
    });
    
    // Load initial stats
    window.appState.fetchStats();
    
    // Set up periodic stats refresh
    setInterval(() => {
        if (!window.appState.isScanning) {
            window.appState.fetchStats();
        }
    }, 30000); // Every 30 seconds
});