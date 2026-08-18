# PixelProbe integration guide

Examples for integrating PixelProbe into your applications and workflows.

## Table of contents
1. [Authentication](#authentication)
2. [Basic integration](#basic-integration)
3. [Automated scanning](#automated-scanning)
4. [Monitoring integration](#monitoring-integration)
5. [CI/CD integration](#cicd-integration)
6. [Backup system integration](#backup-system-integration)
7. [Media server integration](#media-server-integration)

## Authentication

All API endpoints require authentication. For programmatic access, create an API token
(via the web UI under Account -> API Tokens, or `POST /api/tokens` with an authenticated
session) and send it as a Bearer token on every request:

```
Authorization: Bearer your-api-token
```

All examples below assume a valid API token. The only endpoints that do not require
authentication are `GET /healthz` (liveness probe), `GET /api/auth/status`,
`POST /api/auth/setup` (first-run only), `POST /api/auth/login`, and
`GET /api/openapi.yaml` / `GET /api/openapi.json`.

Note: `/api/scan-status` never reports `error` or `cancelled` in its `status`
field - finished scans surface as `completed` or `idle`. Wait loops must treat
`idle` as terminal and read the `phase` field (`error`, `cancelled`, `crashed`)
to classify failures. The wait loops below do this.

## Basic integration

### Python client class

```python
import requests
import time
from typing import Dict, List, Optional

class PixelProbeClient:
    """Client for interacting with PixelProbe API"""
    
    def __init__(self, base_url: str = "http://localhost:5000", api_token: str = ""):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_token}"})
    
    def scan_file(self, file_path: str) -> Dict:
        """Scan a single file"""
        response = self.session.post(
            f"{self.base_url}/api/scan-file",
            json={"file_path": file_path}
        )
        response.raise_for_status()
        return response.json()
    
    def scan_directory(self, directories: List[str], force_rescan: bool = False) -> Dict:
        """Scan multiple directories"""
        response = self.session.post(
            f"{self.base_url}/api/scan",
            json={
                "directories": directories,
                "force_rescan": force_rescan
            }
        )
        response.raise_for_status()
        return response.json()
    
    def get_scan_status(self) -> Dict:
        """Get current scan status"""
        response = self.session.get(f"{self.base_url}/api/scan-status")
        response.raise_for_status()
        return response.json()
    
    def wait_for_scan(self, check_interval: int = 5) -> Dict:
        """Wait for scan to complete.

        status never reports 'error' or 'cancelled' - finished scans surface
        as 'completed' or 'idle'. Read 'phase' to classify failures.
        """
        while True:
            status = self.get_scan_status()
            if not status.get('is_active') and status['status'] in ('completed', 'idle'):
                return status

            print(f"Scan progress: {status['current']}/{status['total']}")
            time.sleep(check_interval)
    
    def get_corrupted_files(self, page: int = 1, per_page: int = 100) -> Dict:
        """Get list of corrupted files"""
        response = self.session.get(
            f"{self.base_url}/api/scan-results",
            params={
                "page": page,
                "per_page": per_page,
                "is_corrupted": "true"
            }
        )
        response.raise_for_status()
        return response.json()
    
    def get_statistics(self) -> Dict:
        """Get overall statistics"""
        response = self.session.get(f"{self.base_url}/api/stats")
        response.raise_for_status()
        return response.json()

# Example usage
if __name__ == "__main__":
    client = PixelProbeClient(api_token="your-api-token")
    
    # Start a scan
    print("Starting scan...")
    client.scan_directory(["/media/photos"])
    
    # Wait for completion
    result = client.wait_for_scan()
    if result['phase'] == 'completed':
        print("Scan completed")
    else:
        # phase is 'error', 'cancelled', or 'crashed'
        print(f"Scan did not complete cleanly (phase: {result['phase']})")
    
    # Get corrupted files
    corrupted = client.get_corrupted_files()
    print(f"Found {corrupted['total']} corrupted files")
    
    for file in corrupted['results']:
        print(f"- {file['file_path']}: {file['error_message']}")
```

### Node.js client

```javascript
const axios = require('axios');

class PixelProbeClient {
    constructor(baseUrl = 'http://localhost:5000', apiToken = '') {
        this.baseUrl = baseUrl;
        this.client = axios.create({
            baseURL: baseUrl,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${apiToken}`
            }
        });
    }
    
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
    
    async getScanStatus() {
        const response = await this.client.get('/api/scan-status');
        return response.data;
    }
    
    async waitForScan(checkInterval = 5000) {
        // status never reports 'error' or 'cancelled' - finished scans
        // surface as 'completed' or 'idle'. Read phase to classify failures.
        return new Promise((resolve) => {
            const checkStatus = async () => {
                const status = await this.getScanStatus();

                if (!status.is_active && ['completed', 'idle'].includes(status.status)) {
                    resolve(status);
                    return;
                }

                console.log(`Scan progress: ${status.current}/${status.total}`);
                setTimeout(checkStatus, checkInterval);
            };
            
            checkStatus();
        });
    }
    
    async getCorruptedFiles(page = 1, perPage = 100) {
        const response = await this.client.get('/api/scan-results', {
            params: {
                page: page,
                per_page: perPage,
                is_corrupted: 'true'
            }
        });
        return response.data;
    }
}

// Example usage
(async () => {
    const client = new PixelProbeClient('http://localhost:5000', 'your-api-token');
    
    try {
        // Start scan
        console.log('Starting scan...');
        await client.scanDirectory(['/media/photos']);
        
        // Wait for completion
        const result = await client.waitForScan();
        if (result.phase === 'completed') {
            console.log('Scan completed');
        } else {
            // phase is 'error', 'cancelled', or 'crashed'
            console.log(`Scan did not complete cleanly (phase: ${result.phase})`);
        }
        
        // Get corrupted files
        const corrupted = await client.getCorruptedFiles();
        console.log(`Found ${corrupted.total} corrupted files`);
        
        corrupted.results.forEach(file => {
            console.log(`- ${file.file_path}: ${file.error_message}`);
        });
    } catch (error) {
        console.error('Error:', error.message);
    }
})();
```

## Automated scanning

### Daily scan script

```bash
#!/bin/bash
# daily-scan.sh - Run daily media scan and email results

PIXELPROBE_URL="http://localhost:5000"
API_TOKEN="your-api-token"
AUTH_HEADER="Authorization: Bearer $API_TOKEN"
EMAIL="admin@example.com"
LOG_FILE="/var/log/pixelprobe-daily.log"

# Start scan
echo "$(date): Starting daily scan" >> "$LOG_FILE"
SCAN_RESPONSE=$(curl -s -X POST "$PIXELPROBE_URL/api/scan" \
    -H "$AUTH_HEADER" \
    -H "Content-Type: application/json" \
    -d '{"force_rescan": false}')

# Wait for completion. status never reports 'error' or 'cancelled':
# finished scans surface as 'completed' or 'idle'; read .phase to classify.
while true; do
    STATUS=$(curl -s -H "$AUTH_HEADER" "$PIXELPROBE_URL/api/scan-status")
    CURRENT_STATUS=$(echo "$STATUS" | jq -r '.status')
    IS_ACTIVE=$(echo "$STATUS" | jq -r '.is_active')

    if [[ "$IS_ACTIVE" != "true" ]] && { [[ "$CURRENT_STATUS" == "completed" ]] || [[ "$CURRENT_STATUS" == "idle" ]]; }; then
        break
    fi

    sleep 30
done

PHASE=$(echo "$STATUS" | jq -r '.phase')
if [[ "$PHASE" != "completed" ]]; then
    echo "$(date): Scan did not complete cleanly (phase: $PHASE)" >> "$LOG_FILE"
    exit 1
fi

# Get statistics
STATS=$(curl -s -H "$AUTH_HEADER" "$PIXELPROBE_URL/api/stats")
CORRUPTED_COUNT=$(echo "$STATS" | jq -r '.corrupted_files')

# Send email if corrupted files found
if [[ "$CORRUPTED_COUNT" -gt 0 ]]; then
    # Export corrupted files to CSV
    curl -X POST "$PIXELPROBE_URL/api/export" \
        -H "$AUTH_HEADER" \
        -H "Content-Type: application/json" \
        -d '{"format": "csv", "filter": "corrupted"}' \
        -o /tmp/corrupted_files.csv
    
    # Send email
    echo "Found $CORRUPTED_COUNT corrupted files. See attached CSV." | \
        mail -s "PixelProbe: Corrupted Files Found" \
        -a /tmp/corrupted_files.csv \
        "$EMAIL"
fi

echo "$(date): Scan completed. Corrupted files: $CORRUPTED_COUNT" >> "$LOG_FILE"
```

### Cron configuration

```bash
# Run daily scan at 2 AM
0 2 * * * /usr/local/bin/daily-scan.sh

# Run weekly deep scan on Sunday at 3 AM
0 3 * * 0 /usr/local/bin/weekly-deep-scan.sh

# Clean up orphaned database entries monthly
0 4 1 * * curl -X POST http://localhost:5000/api/cleanup-orphaned -H "Authorization: Bearer your-api-token"
```

## Monitoring integration

### Prometheus metrics exporter

```python
from prometheus_client import Gauge, Counter, generate_latest
from flask import Response
import requests

# Define metrics
files_total = Gauge('pixelprobe_files_total', 'Total number of files')
files_scanned = Gauge('pixelprobe_files_scanned', 'Number of scanned files')
files_corrupted = Gauge('pixelprobe_files_corrupted', 'Number of corrupted files')
scan_duration = Gauge('pixelprobe_scan_duration_seconds', 'Last scan duration')
corruption_rate = Gauge('pixelprobe_corruption_rate', 'Corruption rate percentage')

def update_metrics():
    """Update Prometheus metrics from PixelProbe API"""
    try:
        # Get statistics
        response = requests.get(
            'http://localhost:5000/api/stats',
            headers={'Authorization': 'Bearer your-api-token'}
        )
        stats = response.json()
        
        # Update metrics
        files_total.set(stats['total_files'])
        files_scanned.set(stats['completed_files'])
        files_corrupted.set(stats['corrupted_files'])
        if stats['completed_files']:
            corruption_rate.set(stats['corrupted_files'] / stats['completed_files'] * 100)
        
    except Exception as e:
        print(f"Error updating metrics: {e}")

@app.route('/metrics')
def metrics():
    """Prometheus metrics endpoint"""
    update_metrics()
    return Response(generate_latest(), mimetype='text/plain')
```

### Grafana dashboard JSON

```json
{
  "dashboard": {
    "title": "PixelProbe Media Health",
    "panels": [
      {
        "title": "Corruption Rate",
        "targets": [
          {
            "expr": "pixelprobe_corruption_rate"
          }
        ],
        "type": "gauge"
      },
      {
        "title": "Files Status",
        "targets": [
          {
            "expr": "pixelprobe_files_total",
            "legendFormat": "Total"
          },
          {
            "expr": "pixelprobe_files_scanned",
            "legendFormat": "Scanned"
          },
          {
            "expr": "pixelprobe_files_corrupted",
            "legendFormat": "Corrupted"
          }
        ],
        "type": "graph"
      }
    ]
  }
}
```

## CI/CD integration

### GitHub Actions workflow

```yaml
name: Media Integrity Check

on:
  push:
    paths:
      - 'media/**'
  schedule:
    - cron: '0 0 * * *'  # Daily at midnight

jobs:
  scan-media:
    runs-on: ubuntu-latest
    
    steps:
    - uses: actions/checkout@v3
    
    - name: Start PixelProbe
      run: |
        docker run -d \
          --name pixelprobe \
          -p 5000:5000 \
          -v ${{ github.workspace }}/media:/media:ro \
          pixelprobe:latest
    
    - name: Wait for service
      run: |
        until curl -s http://localhost:5000/healthz; do
          echo "Waiting for PixelProbe..."
          sleep 5
        done
    
    - name: Scan media files
      env:
        API_TOKEN: ${{ secrets.PIXELPROBE_API_TOKEN }}
      run: |
        curl -X POST http://localhost:5000/api/scan \
          -H "Authorization: Bearer $API_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"directories": ["/media"]}'
    
    - name: Wait for scan completion
      env:
        API_TOKEN: ${{ secrets.PIXELPROBE_API_TOKEN }}
      run: |
        # status never reports 'error' or 'cancelled': finished scans surface
        # as 'completed' or 'idle'; read .phase to classify failures.
        while true; do
          STATUS_JSON=$(curl -s -H "Authorization: Bearer $API_TOKEN" http://localhost:5000/api/scan-status)
          STATUS=$(echo "$STATUS_JSON" | jq -r '.status')
          IS_ACTIVE=$(echo "$STATUS_JSON" | jq -r '.is_active')
          if [[ "$IS_ACTIVE" != "true" && ( "$STATUS" == "completed" || "$STATUS" == "idle" ) ]]; then
            break
          fi
          sleep 10
        done
        PHASE=$(echo "$STATUS_JSON" | jq -r '.phase')
        if [[ "$PHASE" != "completed" ]]; then
          echo "Scan did not complete cleanly (phase: $PHASE)"
          exit 1
        fi
    
    - name: Check for corrupted files
      env:
        API_TOKEN: ${{ secrets.PIXELPROBE_API_TOKEN }}
      run: |
        CORRUPTED=$(curl -s -H "Authorization: Bearer $API_TOKEN" http://localhost:5000/api/stats | jq -r '.corrupted_files')
        if [[ "$CORRUPTED" -gt 0 ]]; then
          echo "[ERROR] Found $CORRUPTED corrupted files!"
          curl -s -H "Authorization: Bearer $API_TOKEN" "http://localhost:5000/api/scan-results?is_corrupted=true" | jq -r '.results[].file_path'
          exit 1
        else
          echo " No corrupted files found!"
        fi
```

### GitLab CI pipeline

```yaml
stages:
  - test
  - scan

media-integrity:
  stage: scan
  image: docker:latest
  services:
    - docker:dind
  script:
    - docker run -d --name pixelprobe -p 5000:5000 -v $CI_PROJECT_DIR/media:/media:ro pixelprobe:latest
    - sleep 10
    - |
      curl -X POST http://localhost:5000/api/scan \
        -H "Authorization: Bearer $PIXELPROBE_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d '{"directories": ["/media"]}'
    - |
      # status never reports 'error' or 'cancelled': finished scans surface
      # as 'completed' or 'idle'; read .phase to classify failures.
      while true; do
        STATUS_JSON=$(curl -s -H "Authorization: Bearer $PIXELPROBE_API_TOKEN" http://localhost:5000/api/scan-status)
        STATUS=$(echo "$STATUS_JSON" | jq -r '.status')
        IS_ACTIVE=$(echo "$STATUS_JSON" | jq -r '.is_active')
        if [ "$IS_ACTIVE" != "true" ] && { [ "$STATUS" = "completed" ] || [ "$STATUS" = "idle" ]; }; then
          break
        fi
        sleep 10
      done
      PHASE=$(echo "$STATUS_JSON" | jq -r '.phase')
      if [ "$PHASE" != "completed" ]; then
        echo "Scan did not complete cleanly (phase: $PHASE)"
        exit 1
      fi
    - |
      CORRUPTED=$(curl -s -H "Authorization: Bearer $PIXELPROBE_API_TOKEN" http://localhost:5000/api/stats | jq -r '.corrupted_files')
      if [ "$CORRUPTED" -gt 0 ]; then
        echo "Found $CORRUPTED corrupted files"
        exit 1
      fi
  artifacts:
    reports:
      junit: scan-report.xml
  only:
    - merge_requests
    - main
```

## Backup system integration

### Pre-backup validation script

```python
#!/usr/bin/env python3
"""
pre-backup-scan.py - Validate media files before backup
"""
import sys
import json
from pixelprobe_client import PixelProbeClient

def validate_before_backup(directories):
    """Scan directories and abort backup if corruption found"""
    client = PixelProbeClient(api_token="your-api-token")
    
    print("Starting pre-backup media validation...")
    
    # Start scan
    client.scan_directory(directories, force_rescan=False)
    
    # Wait for completion
    result = client.wait_for_scan()

    if result['phase'] != 'completed':
        # phase is 'error', 'cancelled', or 'crashed'
        print(f"Scan did not complete cleanly (phase: {result['phase']})")
        return False
    
    # Check for corrupted files
    stats = client.get_statistics()
    corrupted_count = stats['corrupted_files']
    
    if corrupted_count > 0:
        print(f"[ERROR] Found {corrupted_count} corrupted files!")
        
        # Get details of corrupted files
        corrupted = client.get_corrupted_files()
        for file in corrupted['results']:
            print(f"  - {file['file_path']}")
        
        print("\nBackup aborted to prevent backing up corrupted files.")
        print("Please review and fix corrupted files before backup.")
        return False
    
    print(f" All {stats['completed_files']} files validated successfully!")
    return True

if __name__ == "__main__":
    directories = sys.argv[1:] if len(sys.argv) > 1 else ["/media"]
    
    if validate_before_backup(directories):
        sys.exit(0)  # Success
    else:
        sys.exit(1)  # Failure
```

### Rsync wrapper with validation

```bash
#!/bin/bash
# safe-rsync.sh - Rsync with media validation

SOURCE_DIR="$1"
DEST_DIR="$2"

# Validate media files first
python3 /usr/local/bin/pre-backup-scan.py "$SOURCE_DIR"

if [ $? -eq 0 ]; then
    echo "Media validation passed. Starting rsync..."
    rsync -avz --progress "$SOURCE_DIR" "$DEST_DIR"
else
    echo "Media validation failed. Rsync aborted."
    exit 1
fi
```

## Media server integration

### Jellyfin/Plex post-processing

```python
#!/usr/bin/env python3
"""
media-server-scan.py - Scan new media files added to media server
"""
import os
import sys
from pixelprobe_client import PixelProbeClient

def scan_new_media(file_path):
    """Scan newly added media file"""
    client = PixelProbeClient(api_token="your-api-token")
    
    print(f"Scanning new media: {file_path}")
    
    try:
        # Scan the file
        result = client.scan_file(file_path)
        
        # Wait a moment for scan to process
        import time
        time.sleep(2)
        
        # Check if file is corrupted. The search parameter is a
        # case-insensitive substring (ILIKE) match on file_path, and
        # results are sorted by scan_date descending by default, so the
        # most recently scanned match comes first.
        scan_results = client.session.get(
            f"{client.base_url}/api/scan-results",
            params={"search": file_path}
        ).json()
        
        if scan_results['results']:
            file_result = scan_results['results'][0]
            
            if file_result['is_corrupted']:
                print(f"  WARNING: File is corrupted!")
                print(f"Error: {file_result['error_message']}")
                
                # Move to quarantine
                quarantine_dir = "/media/quarantine"
                os.makedirs(quarantine_dir, exist_ok=True)
                
                quarantine_path = os.path.join(
                    quarantine_dir, 
                    os.path.basename(file_path)
                )
                os.rename(file_path, quarantine_path)
                print(f"Moved to quarantine: {quarantine_path}")
                
                return False
            else:
                print(" File is healthy!")
                return True
    
    except Exception as e:
        print(f"Error scanning file: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: media-server-scan.py <file_path>")
        sys.exit(1)
    
    file_path = sys.argv[1]
    if scan_new_media(file_path):
        sys.exit(0)
    else:
        sys.exit(1)
```

### Nextcloud external script

```php
<?php
/**
 * Nextcloud app to validate uploaded media files
 */

namespace OCA\PixelProbeIntegration;

use OCP\Files\Node;
use OCP\Files\IRootFolder;

class MediaValidator {
    private $pixelprobeUrl = 'http://localhost:5000';
    private $apiToken = 'your-api-token';
    
    public function validateFile(Node $file) {
        $filePath = $file->getInternalPath();
        
        // Call PixelProbe API
        $ch = curl_init($this->pixelprobeUrl . '/api/scan-file');
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode([
            'file_path' => $filePath
        ]));
        curl_setopt($ch, CURLOPT_HTTPHEADER, [
            'Content-Type: application/json',
            'Authorization: Bearer ' . $this->apiToken
        ]);
        
        $response = curl_exec($ch);
        $httpCode = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);
        
        if ($httpCode !== 200) {
            throw new \Exception('Failed to scan file');
        }
        
        // Wait for scan to complete
        sleep(2);
        
        // Check result
        $ch = curl_init($this->pixelprobeUrl . '/api/scan-results?search=' . urlencode($filePath));
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_HTTPHEADER, [
            'Authorization: Bearer ' . $this->apiToken
        ]);
        $response = curl_exec($ch);
        curl_close($ch);
        
        $results = json_decode($response, true);
        if (!empty($results['results'])) {
            $result = $results['results'][0];
            
            if ($result['is_corrupted']) {
                // Tag file as corrupted
                $file->addTag('corrupted');
                
                // Notify user
                $this->notifyUser($file->getOwner(), $file->getName(), $result['error_message']);
                
                return false;
            }
        }
        
        return true;
    }
}
```

## Docker Compose integration

```yaml
version: '3.8'

services:
  pixelprobe:
    image: pixelprobe:latest
    container_name: pixelprobe
    ports:
      - "5000:5000"
    volumes:
      - /media:/media:ro
      - pixelprobe_data:/app/data
    environment:
      - SECRET_KEY=${SECRET_KEY}
      - TZ=${TZ:-UTC}
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
  
  # Media server integration
  jellyfin:
    image: jellyfin/jellyfin
    volumes:
      - /media:/media
    depends_on:
      - pixelprobe
  
  # Monitoring
  prometheus:
    image: prom/prometheus
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
  
  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    volumes:
      - grafana_data:/var/lib/grafana

volumes:
  pixelprobe_data:
  grafana_data:
```

## Webhook integration

PixelProbe can push notifications to your own HTTP endpoint through its
notification system. Configure a webhook provider and an event rule:

```bash
# 1. Create a webhook provider
curl -X POST http://localhost:5000/api/notifications/providers \
  -H "Authorization: Bearer your-api-token" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-webhook", "provider_type": "webhook", "configuration": {"webhook_url": "https://your-endpoint.example/pixelprobe-webhook"}}'

# 2. Create a rule binding an event to the provider
curl -X POST http://localhost:5000/api/notifications/rules \
  -H "Authorization: Bearer your-api-token" \
  -H "Content-Type: application/json" \
  -d '{"event_type": "bitrot_suspected", "provider_id": 1}'
```

The only event dispatched today is `bitrot_suspected`, sent when a
file-changes scan finds files whose content hash changed while the
modification time did not. There is no `scan_completed` webhook event; to
act on scan completion, poll `/api/scan-status` as shown above.

The generic webhook payload looks like:

```json
{
  "title": "Bitrot suspected on 2 file(s)",
  "message": "Content hash changed while file modification time did not - ...",
  "priority": "high",
  "timestamp": "2025-01-20T12:00:00+00:00",
  "source": "PixelProbe",
  "data": {
    "count": 2,
    "file_paths": ["/media/photos/a.jpg", "/media/photos/b.jpg"]
  }
}
```

### Webhook receiver

```python
from flask import Flask, request

app = Flask(__name__)

@app.route('/pixelprobe-webhook', methods=['POST'])
def handle_webhook():
    """Handle a PixelProbe notification webhook"""
    data = request.json

    print(f"[{data['priority']}] {data['title']}: {data['message']}")
    for path in data.get('data', {}).get('file_paths', []):
        print(f"  - {path}")

    return "OK", 200
```

## Best practices

- Respect the API rate limits; back off and retry on 429 responses.
- Alert on corruption findings (webhook or Prometheus, both shown above) instead of checking manually.
- Use HTTPS in production.