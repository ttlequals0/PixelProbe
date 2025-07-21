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
 *   const client = new PixelProbeClient('http://localhost:5000');
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
     */
    constructor(baseUrl = 'http://localhost:5000', timeout = 30000) {
        this.baseUrl = baseUrl.replace(/\/$/, '');
        this.timeout = timeout;
        
        this.client = axios.create({
            baseURL: this.baseUrl,
            timeout: this.timeout,
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
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
        const response = await this.client.post('/api/scan-all', {
            directories: directories,
            force_rescan: forceRescan
        });
        return response.data;
    }
    
    async scanParallel(directories, numWorkers = 4, forceRescan = false) {
        const response = await this.client.post('/api/scan-parallel', {
            directories: directories,
            num_workers: numWorkers,
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
        const response = await this.client.get('/api/stats/summary');
        return response.data;
    }
    
    async getCorruptionByType() {
        const response = await this.client.get('/api/stats/corruption-by-type');
        return response.data;
    }
    
    async getScanHistory(days = 30) {
        const response = await this.client.get('/api/stats/scan-history', {
            params: { days }
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
    
    async exportCSV(filters = {}, outputFile = null) {
        const response = await this.client.post('/api/export/csv', {
            filters: filters
        }, {
            responseType: 'arraybuffer'
        });
        
        const csvData = Buffer.from(response.data);
        
        if (outputFile) {
            await fs.writeFile(outputFile, csvData);
        }
        
        return csvData;
    }
    
    // Maintenance operations
    
    async cleanupMissingFiles(dryRun = true, directories = []) {
        const response = await this.client.post('/api/cleanup', {
            dry_run: dryRun,
            directories: directories
        });
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
            console.log(`   Scanned: ${stats.scanned_files.toLocaleString()}`);
            console.log(`   Corrupted: ${stats.corrupted_files.toLocaleString()}`);
            console.log(`   Corruption rate: ${stats.corruption_rate.toFixed(2)}%`);
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
            await client.exportCSV({}, outputFile);
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
  PIXELPROBE_URL     PixelProbe API URL (default: http://localhost:5000)
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