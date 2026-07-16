#!/usr/bin/env node
/**
 * PixelProbe Node.js Client
 * A complete Node.js client for the PixelProbe API
 * 
 * Requirements:
 *   npm install axios
 * 
 * Usage:
 *   const PixelProbeClient = require('./pixelprobe-client');
 *   const client = new PixelProbeClient('http://localhost:5000', 30000, '<your-token>');
 *
 * Authentication:
 *   All API endpoints require a Bearer token. Create one via POST /api/tokens
 *   or the web UI, then pass it as apiToken or set PIXELPROBE_API_TOKEN.
 */

const axios = require('axios');
const fs = require('fs').promises;
const path = require('path');

class PixelProbeError extends Error {
    constructor(message, response) {
        super(message);
        this.name = 'PixelProbeError';
        this.response = response;
    }
}

class PixelProbeClient {
    /**
     * Initialize the PixelProbe client
     * @param {string} baseUrl - Base URL of the PixelProbe API
     * @param {number} timeout - Request timeout in milliseconds
     * @param {string} apiToken - API token (defaults to PIXELPROBE_API_TOKEN env var)
     */
    constructor(baseUrl = 'http://localhost:5000', timeout = 30000, apiToken = null) {
        this.baseUrl = baseUrl.replace(/\/$/, '');
        this.timeout = timeout;
        this.apiToken = apiToken || process.env.PIXELPROBE_API_TOKEN || '';

        if (!this.apiToken) {
            throw new PixelProbeError(
                'API token required: pass apiToken or set PIXELPROBE_API_TOKEN');
        }

        this.client = axios.create({
            baseURL: this.baseUrl,
            timeout: this.timeout,
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': `Bearer ${this.apiToken}`
            }
        });
        
        // Add response interceptor for error handling
        this.client.interceptors.response.use(
            response => response,
            error => {
                const message = error.response?.data?.error || error.message;
                throw new PixelProbeError(message, error.response);
            }
        );
    }
    
    // System endpoints
    
    async healthCheck() {
        const response = await this.client.get('/health');
        return response.data;
    }
    
    async getVersion() {
        const response = await this.client.get('/api/version');
        return response.data;
    }
    
    // Scanning operations
    
    async scanFile(filePath) {
        const response = await this.client.post('/api/scan-file', {
            file_path: filePath
        });
        return response.data;
    }
    
    async scanDirectory(directories, forceRescan = false) {
        const response = await this.client.post('/api/scan', {
            directories: directories,
            force_rescan: forceRescan
        });
        return response.data;
    }

    // Deprecated alias of scanDirectory (POST /api/scan-parallel)
    async scanParallel(directories, forceRescan = false) {
        const response = await this.client.post('/api/scan-parallel', {
            directories: directories,
            force_rescan: forceRescan
        });
        return response.data;
    }
    
    async getScanStatus() {
        const response = await this.client.get('/api/scan-status');
        return response.data;
    }
    
    async cancelScan() {
        const response = await this.client.post('/api/cancel-scan');
        return response.data;
    }
    
    async waitForScan(checkInterval = 5000, onProgress = null) {
        return new Promise((resolve) => {
            const checkStatus = async () => {
                try {
                    const status = await this.getScanStatus();
                    
                    if (onProgress) {
                        onProgress(status);
                    }
                    
                    if (['completed', 'error', 'cancelled', 'idle'].includes(status.status)) {
                        resolve(status);
                        return;
                    }
                    
                    setTimeout(checkStatus, checkInterval);
                } catch (error) {
                    resolve({ status: 'error', error: error.message });
                }
            };
            
            checkStatus();
        });
    }
    
    // Results and statistics
    
    async getScanResults(options = {}) {
        const {
            page = 1,
            perPage = 100,
            scanStatus = 'all',
            isCorrupted = 'all'
        } = options;
        
        const response = await this.client.get('/api/scan-results', {
            params: {
                page: page,
                per_page: perPage,
                scan_status: scanStatus,
                is_corrupted: isCorrupted
            }
        });
        return response.data;
    }
    
    async getScanResult(resultId) {
        const response = await this.client.get(`/api/scan-results/${resultId}`);
        return response.data;
    }
    
    async getCorruptedFiles(page = 1, perPage = 100) {
        return this.getScanResults({
            page,
            perPage,
            isCorrupted: 'true'
        });
    }
    
    async getAllCorruptedFiles() {
        const allFiles = [];
        let page = 1;
        
        while (true) {
            const result = await this.getCorruptedFiles(page, 500);
            allFiles.push(...result.results);
            
            if (page >= result.pages) {
                break;
            }
            
            page++;
        }
        
        return allFiles;
    }
    
    async getStatistics() {
        // File counts by state plus integrity coverage
        const response = await this.client.get('/api/stats');
        return response.data;
    }

    async getScanTrends(days = 30) {
        const response = await this.client.get('/api/stats/trends', {
            params: { days }
        });
        return response.data;
    }

    async getDurationHistogram(days = 30, buckets = 10) {
        const response = await this.client.get('/api/stats/duration-histogram', {
            params: { days, buckets }
        });
        return response.data;
    }
    
    // Administrative operations
    
    async markFilesAsGood(fileIds) {
        const response = await this.client.post('/api/mark-as-good', {
            file_ids: fileIds
        });
        return response.data;
    }
    
    async getIgnoredPatterns() {
        const response = await this.client.get('/api/ignored-patterns');
        return response.data;
    }
    
    async addIgnoredPattern(pattern, description = '') {
        const response = await this.client.post('/api/ignored-patterns', {
            pattern: pattern,
            description: description
        });
        return response.data;
    }
    
    async deleteIgnoredPattern(patternId) {
        const response = await this.client.delete(`/api/ignored-patterns/${patternId}`);
        return response.data;
    }
    
    async getConfigurations() {
        const response = await this.client.get('/api/configurations');
        return response.data;
    }
    
    async addConfiguration(path) {
        const response = await this.client.post('/api/configurations', {
            path: path
        });
        return response.data;
    }
    
    // Export operations
    
    async exportResults(options = {}, outputFile = null) {
        const {
            format = 'csv',      // 'csv', 'json', or 'pdf'
            filter = 'all',      // 'all', 'corrupted', 'healthy', 'warning'
            search = '',
            fileIds = []
        } = options;

        const response = await this.client.post('/api/export', {
            format: format,
            filter: filter,
            search: search,
            file_ids: fileIds
        }, {
            responseType: 'arraybuffer'
        });

        const exportData = Buffer.from(response.data);

        if (outputFile) {
            await fs.writeFile(outputFile, exportData);
        }

        return exportData;
    }

    // Maintenance operations

    // Starts a background cleanup of orphaned database entries.
    // Returns 409 (PixelProbeError) if a cleanup is already in progress;
    // monitor progress with getCleanupStatus().
    async cleanupOrphaned(filePaths = []) {
        const response = await this.client.post('/api/cleanup-orphaned', {
            file_paths: filePaths
        });
        return response.data;
    }

    async getCleanupStatus() {
        const response = await this.client.get('/api/cleanup-status');
        return response.data;
    }
    
    async vacuumDatabase() {
        const response = await this.client.post('/api/vacuum');
        return response.data;
    }
}

// CLI interface
async function main() {
    const args = process.argv.slice(2);
    const url = process.env.PIXELPROBE_URL || 'http://localhost:5000';
    
    const client = new PixelProbeClient(url);
    
    try {
        // Check health
        const health = await client.healthCheck();
        console.log(`✅ PixelProbe is ${health.status} (v${health.version})`);
        
        // Parse command line arguments
        if (args.includes('--scan')) {
            const scanIndex = args.indexOf('--scan');
            const directories = args.slice(scanIndex + 1).filter(arg => !arg.startsWith('--'));
            
            if (directories.length === 0) {
                console.error('❌ No directories specified for scanning');
                process.exit(1);
            }
            
            console.log(`\n📡 Starting scan of: ${directories.join(', ')}`);
            await client.scanDirectory(directories);
            
            // Wait with progress
            const result = await client.waitForScan(5000, (status) => {
                if (status.status === 'scanning' && status.total > 0) {
                    const pct = (status.current / status.total * 100).toFixed(1);
                    process.stdout.write(`\r⏳ Progress: ${status.current}/${status.total} (${pct}%) - ${status.file}`);
                }
            });
            
            console.log(`\n✅ Scan ${result.status}`);
        }
        
        if (args.includes('--status')) {
            const status = await client.getScanStatus();
            console.log(`\n📊 Scan Status: ${status.status}`);
            if (status.is_running) {
                console.log(`   Progress: ${status.current}/${status.total}`);
                console.log(`   Current file: ${status.file}`);
            }
        }
        
        if (args.includes('--stats')) {
            const stats = await client.getStatistics();
            console.log('\n📈 Statistics:');
            console.log(`   Total files: ${stats.total_files.toLocaleString()}`);
            console.log(`   Completed: ${stats.completed_files.toLocaleString()}`);
            console.log(`   Corrupted: ${stats.corrupted_files.toLocaleString()}`);
            console.log(`   Healthy: ${stats.healthy_files.toLocaleString()}`);
            console.log(`   Warnings: ${stats.warning_files.toLocaleString()}`);
        }
        
        if (args.includes('--corrupted')) {
            const corrupted = await client.getAllCorruptedFiles();
            console.log(`\n❌ Found ${corrupted.length} corrupted files:`);
            
            // Show first 10
            corrupted.slice(0, 10).forEach(file => {
                console.log(`   - ${file.file_path}`);
            });
            
            if (corrupted.length > 10) {
                console.log(`   ... and ${corrupted.length - 10} more`);
            }
        }
        
        if (args.includes('--export')) {
            const exportIndex = args.indexOf('--export');
            const outputFile = args[exportIndex + 1];
            
            if (!outputFile || outputFile.startsWith('--')) {
                console.error('❌ No output file specified for export');
                process.exit(1);
            }
            
            console.log(`\n💾 Exporting results to ${outputFile}`);
            await client.exportResults({ format: 'csv' }, outputFile);
            console.log('✅ Export complete');
        }
        
        if (args.length === 0 || args.includes('--help')) {
            console.log(`
Usage: node pixelprobe-client.js [options]

Options:
  --scan <dirs...>    Scan specified directories
  --status           Show current scan status
  --stats            Show statistics
  --corrupted        List corrupted files
  --export <file>    Export results to CSV file
  --help             Show this help message

Environment:
  PIXELPROBE_URL        PixelProbe API URL (default: http://localhost:5000)
  PIXELPROBE_API_TOKEN  API token (required; create via POST /api/tokens or the web UI)
`);
        }
        
    } catch (error) {
        console.error(`\n❌ Error: ${error.message}`);
        process.exit(1);
    }
}

// Export for use as module
module.exports = PixelProbeClient;

// Run CLI if called directly
if (require.main === module) {
    main().catch(console.error);
}