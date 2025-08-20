# API Endpoint Consolidation Plan

## Current State (Too Many Similar Endpoints)

### Scan Management Endpoints (KEEP & CONSOLIDATE)
- `/scan-all` → **`/scan`** (main endpoint, supports all scan types via parameters)
- `/scan-parallel` → **REMOVE** (merge into `/scan` with `parallel: true`)
- `/scan-file` → **KEEP** (specific file scanning is different enough)
- `/force-scan-pending` → **REMOVE** (merge into `/scan` with `pending_only: true`)

### Stuck Scan Recovery (TOO MANY - CONSOLIDATE)
- `/reset-stuck-scans` → **REMOVE**
- `/recover-stuck-scan` → **REMOVE** 
- `/force-cleanup-scan` → **REMOVE**
- **NEW**: `/scan/recovery` (single endpoint with action parameter)

### Status & Info (KEEP AS IS)
- `/scan-status` → **KEEP** (essential)
- `/scan-results` → **KEEP** (view results)
- `/worker-status` → **KEEP** (monitoring)

### Data Management (CONSOLIDATE)
- `/reset-for-rescan` → **REMOVE**
- `/reset-files-by-path` → **REMOVE**
- **NEW**: `/scan/reset` (single endpoint with scope parameter)

## Proposed New Structure

### Core Endpoints (v3.0)
```
POST /api/scan                 # Start any type of scan
  Parameters:
    - type: "full" | "parallel" | "pending" | "discovery"  
    - directories: []
    - file_paths: []
    - force_rescan: bool
    - num_workers: int (for parallel)
    - deep_scan: bool

POST /api/scan/file            # Scan specific file(s)
POST /api/scan/cancel          # Cancel running scan
POST /api/scan/recovery        # Recover from stuck state
  Parameters:
    - action: "cleanup" | "reset" | "force"

POST /api/scan/reset           # Reset scan data
  Parameters:
    - scope: "all" | "path" | "pending"
    - path: string (when scope="path")

GET  /api/scan/status          # Current scan status
GET  /api/scan/results         # View results
GET  /api/worker/status        # Worker health
```

## Migration Strategy

1. **Phase 1 (v2.2.36)**: Mark old endpoints as deprecated but keep working
2. **Phase 2 (v2.3.0)**: Add new consolidated endpoints  
3. **Phase 3 (v3.0.0)**: Remove deprecated endpoints

## Benefits
- Reduced from 15+ scan endpoints to ~8
- Clearer API structure
- Easier to maintain
- More RESTful design
- Backward compatibility during transition