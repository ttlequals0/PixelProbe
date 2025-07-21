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
            --connect-timeout $TIMEOUT \
            "${PIXELPROBE_URL}${endpoint}"
    else
        curl -s -X "$method" \
            -H "Content-Type: application/json" \
            -H "Accept: application/json" \
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
    
    response=$(api_request POST /api/scan-all "$data")
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
    
    response=$(api_request GET /api/stats/summary)
    
    total=$(echo "$response" | jq -r '.total_files')
    scanned=$(echo "$response" | jq -r '.scanned_files')
    corrupted=$(echo "$response" | jq -r '.corrupted_files')
    rate=$(echo "$response" | jq -r '.corruption_rate')
    
    echo -e "${GREEN}Statistics:${NC}"
    echo "  Total files: $(printf "%'d" $total)"
    echo "  Scanned: $(printf "%'d" $scanned)"
    echo "  Corrupted: $(printf "%'d" $corrupted)"
    echo "  Corruption rate: ${rate}%"
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
        -d '{"filters": {}}' \
        "${PIXELPROBE_URL}/api/export/csv" \
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
    local dry_run=${1:-true}
    
    echo -e "${BLUE}🧹 Running cleanup (dry_run: $dry_run)...${NC}"
    
    data=$(jq -n --arg dr "$dry_run" '{dry_run: ($dr == "true")}')
    response=$(api_request POST /api/cleanup "$data")
    
    missing=$(echo "$response" | jq -r '.missing_files')
    cleaned=$(echo "$response" | jq -r '.cleaned_files')
    
    echo -e "${GREEN}Results:${NC}"
    echo "  Missing files: $missing"
    echo "  Cleaned files: $cleaned"
    
    if [ "$dry_run" == "true" ] && [ "$missing" -gt 0 ]; then
        echo -e "${YELLOW}ℹ️  Run with 'false' to actually clean these files${NC}"
    fi
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
  cleanup [false]   Clean missing files (dry run by default)
  cancel            Cancel current scan
  help              Show this help message

Environment:
  PIXELPROBE_URL    PixelProbe API URL (default: http://localhost:5000)

Examples:
  $0 scan /media/photos /media/videos
  $0 stats
  $0 export results.csv
  $0 cleanup false

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
        cmd_cleanup "${2:-true}"
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