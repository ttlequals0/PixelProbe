#!/bin/bash
#
# PixelProbe Bash Client
# A command-line client for the PixelProbe API using curl and jq
#
# Requirements:
#   - curl
#   - jq (for JSON parsing)
#
# Usage:
#   ./pixelprobe-client.sh [command] [options]

# Configuration
PIXELPROBE_URL="${PIXELPROBE_URL:-http://localhost:5000}"
PIXELPROBE_API_TOKEN="${PIXELPROBE_API_TOKEN:-}"
TIMEOUT=30

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions

api_request() {
    local method=$1
    local endpoint=$2
    local data=$3
    
    if [ -z "$data" ]; then
        curl -s -X "$method" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json" \
            -H "Authorization: Bearer $PIXELPROBE_API_TOKEN" \
            --connect-timeout $TIMEOUT \
            "${PIXELPROBE_URL}${endpoint}"
    else
        curl -s -X "$method" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json" \
            -H "Authorization: Bearer $PIXELPROBE_API_TOKEN" \
            --connect-timeout $TIMEOUT \
            -d "$data" \
            "${PIXELPROBE_URL}${endpoint}"
    fi
}

check_dependencies() {
    if ! command -v curl &> /dev/null; then
        echo -e "${RED}❌ Error: curl is not installed${NC}"
        exit 1
    fi
    
    if ! command -v jq &> /dev/null; then
        echo -e "${RED}❌ Error: jq is not installed${NC}"
        echo "Install with: apt-get install jq (Debian/Ubuntu) or brew install jq (macOS)"
        exit 1
    fi

    if [ -z "$PIXELPROBE_API_TOKEN" ]; then
        echo -e "${RED}Error: PIXELPROBE_API_TOKEN is not set${NC}"
        echo "Create an API token via POST /api/tokens or the web UI, then:"
        echo "  export PIXELPROBE_API_TOKEN=<your-token>"
        exit 1
    fi
}

health_check() {
    echo -e "${BLUE}🏥 Checking PixelProbe health...${NC}"
    
    response=$(api_request GET /health)
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to connect to PixelProbe at $PIXELPROBE_URL${NC}"
        exit 1
    fi
    
    status=$(echo "$response" | jq -r '.status')
    version=$(echo "$response" | jq -r '.version')
    
    echo -e "${GREEN}✅ PixelProbe is $status (v$version)${NC}"
}

# Command functions

cmd_scan() {
    local directories=("$@")
    
    if [ ${#directories[@]} -eq 0 ]; then
        echo -e "${RED}❌ No directories specified${NC}"
        echo "Usage: $0 scan <directory1> [directory2] ..."
        exit 1
    fi
    
    echo -e "${BLUE}📡 Starting scan of: ${directories[*]}${NC}"
    
    # Build JSON array of directories
    json_dirs=$(printf '%s\n' "${directories[@]}" | jq -R . | jq -s .)
    data=$(jq -n --argjson dirs "$json_dirs" '{directories: $dirs, force_rescan: false}')
    
    response=$(api_request POST /api/scan "$data")
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to start scan${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✅ Scan started${NC}"
    
    # Wait for scan to complete
    echo -e "${BLUE}⏳ Waiting for scan to complete...${NC}"
    
    while true; do
        status_response=$(api_request GET /api/scan-status)
        status=$(echo "$status_response" | jq -r '.status')
        current=$(echo "$status_response" | jq -r '.current')
        total=$(echo "$status_response" | jq -r '.total')
        file=$(echo "$status_response" | jq -r '.file')
        
        case "$status" in
            "scanning")
                if [ "$total" -gt 0 ]; then
                    percent=$((current * 100 / total))
                    printf "\r⏳ Progress: %d/%d (%d%%) - %s" "$current" "$total" "$percent" "$file"
                fi
                ;;
            "completed")
                echo -e "\n${GREEN}✅ Scan completed successfully${NC}"
                break
                ;;
            "error")
                echo -e "\n${RED}❌ Scan failed with error${NC}"
                exit 1
                ;;
            "cancelled")
                echo -e "\n${YELLOW}⚠️  Scan was cancelled${NC}"
                exit 1
                ;;
            "idle")
                echo -e "\n${GREEN}✅ No scan running${NC}"
                break
                ;;
        esac
        
        sleep 5
    done
}

cmd_status() {
    echo -e "${BLUE}📊 Getting scan status...${NC}"
    
    response=$(api_request GET /api/scan-status)
    
    status=$(echo "$response" | jq -r '.status')
    is_running=$(echo "$response" | jq -r '.is_running')
    
    echo -e "${GREEN}Status: $status${NC}"
    
    if [ "$is_running" == "true" ]; then
        current=$(echo "$response" | jq -r '.current')
        total=$(echo "$response" | jq -r '.total')
        file=$(echo "$response" | jq -r '.file')
        
        echo "Progress: $current/$total"
        echo "Current file: $file"
    fi
}

cmd_stats() {
    echo -e "${BLUE}📈 Getting statistics...${NC}"
    
    response=$(api_request GET /api/stats)

    total=$(echo "$response" | jq -r '.total_files')
    completed=$(echo "$response" | jq -r '.completed_files')
    corrupted=$(echo "$response" | jq -r '.corrupted_files')
    healthy=$(echo "$response" | jq -r '.healthy_files')
    warnings=$(echo "$response" | jq -r '.warning_files')

    echo -e "${GREEN}Statistics:${NC}"
    echo "  Total files: $(printf "%'d" $total)"
    echo "  Completed: $(printf "%'d" $completed)"
    echo "  Corrupted: $(printf "%'d" $corrupted)"
    echo "  Healthy: $(printf "%'d" $healthy)"
    echo "  Warnings: $(printf "%'d" $warnings)"
}

cmd_corrupted() {
    echo -e "${BLUE}❌ Getting corrupted files...${NC}"
    
    page=1
    total_shown=0
    max_show=20
    
    while true; do
        response=$(api_request GET "/api/scan-results?page=$page&per_page=100&is_corrupted=true")
        
        total=$(echo "$response" | jq -r '.total')
        pages=$(echo "$response" | jq -r '.pages')
        
        if [ $page -eq 1 ]; then
            echo -e "${YELLOW}Found $total corrupted files:${NC}"
        fi
        
        # Show files
        echo "$response" | jq -r '.results[].file_path' | while read -r file; do
            if [ $total_shown -lt $max_show ]; then
                echo "  - $file"
                ((total_shown++))
            fi
        done
        
        # Check if we should continue
        if [ $page -ge $pages ] || [ $total_shown -ge $max_show ]; then
            if [ $total -gt $max_show ]; then
                echo "  ... and $((total - max_show)) more"
            fi
            break
        fi
        
        ((page++))
    done
}

cmd_export() {
    local output_file=$1
    
    if [ -z "$output_file" ]; then
        echo -e "${RED}❌ No output file specified${NC}"
        echo "Usage: $0 export <output.csv>"
        exit 1
    fi
    
    echo -e "${BLUE}💾 Exporting results to $output_file...${NC}"
    
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -H "Accept: text/csv" \
        -H "Authorization: Bearer $PIXELPROBE_API_TOKEN" \
        -d '{"format": "csv", "filter": "all"}' \
        "${PIXELPROBE_URL}/api/export" \
        -o "$output_file"
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Export complete${NC}"
        echo "File size: $(ls -lh "$output_file" | awk '{print $5}')"
    else
        echo -e "${RED}❌ Export failed${NC}"
        exit 1
    fi
}

cmd_cleanup() {
    echo -e "${BLUE}Starting orphaned-entry cleanup...${NC}"

    response=$(api_request POST /api/cleanup-orphaned '{}')
    error=$(echo "$response" | jq -r '.error // empty')

    if [ -n "$error" ]; then
        # A 409 is returned when a cleanup is already in progress
        echo -e "${RED}$error${NC}"
        exit 1
    fi

    echo -e "${GREEN}$(echo "$response" | jq -r '.message')${NC}"

    # Monitor progress until the cleanup finishes
    while true; do
        status_response=$(api_request GET /api/cleanup-status)
        is_running=$(echo "$status_response" | jq -r '.is_running')
        phase=$(echo "$status_response" | jq -r '.phase')
        orphaned=$(echo "$status_response" | jq -r '.orphaned_found')
        percent=$(echo "$status_response" | jq -r '.progress_percentage // 0')

        if [ "$is_running" != "true" ]; then
            break
        fi

        printf "\rPhase: %s (%s%%) - orphaned found: %s" "$phase" "$percent" "$orphaned"
        sleep 5
    done

    # Re-fetch once after the loop so the final count reflects the
    # finished cleanup, not an in-progress snapshot.
    status_response=$(api_request GET /api/cleanup-status)
    orphaned=$(echo "$status_response" | jq -r '.orphaned_found')

    echo -e "\n${GREEN}Results:${NC}"
    echo "  Orphaned entries found: $orphaned"
}

cmd_cancel() {
    echo -e "${BLUE}🛑 Cancelling current scan...${NC}"
    
    response=$(api_request POST /api/cancel-scan)
    message=$(echo "$response" | jq -r '.message // .error')
    
    echo "$message"
}

# Main command dispatcher

show_help() {
    cat << EOF
PixelProbe Bash Client

Usage: $0 [command] [options]

Commands:
  scan <dirs...>     Scan specified directories
  status            Show current scan status
  stats             Show overall statistics
  corrupted         List corrupted files
  export <file>     Export results to CSV
  cleanup           Clean up orphaned database entries
  cancel            Cancel current scan
  help              Show this help message

Environment:
  PIXELPROBE_URL        PixelProbe API URL (default: http://localhost:5000)
  PIXELPROBE_API_TOKEN  API token (required; create via POST /api/tokens or the web UI)

Examples:
  $0 scan /media/photos /media/videos
  $0 stats
  $0 export results.csv
  $0 cleanup

EOF
}

# Main

check_dependencies

if [ $# -eq 0 ]; then
    show_help
    exit 0
fi

# Always check health first
health_check

# Execute command
case "$1" in
    scan)
        shift
        cmd_scan "$@"
        ;;
    status)
        cmd_status
        ;;
    stats)
        cmd_stats
        ;;
    corrupted)
        cmd_corrupted
        ;;
    export)
        cmd_export "$2"
        ;;
    cleanup)
        cmd_cleanup
        ;;
    cancel)
        cmd_cancel
        ;;
    help|-h|--help)
        show_help
        ;;
    *)
        echo -e "${RED}❌ Unknown command: $1${NC}"
        show_help
        exit 1
        ;;
esac