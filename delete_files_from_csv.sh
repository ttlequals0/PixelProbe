#!/bin/bash

# Script to delete files listed in a PixelProbe CSV export
# Usage: ./delete_files_from_csv.sh <csv_file> [--dry-run] [--no-confirm]

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if CSV file is provided
if [ $# -lt 1 ]; then
    echo "Usage: $0 <csv_file> [--dry-run] [--no-confirm]"
    echo "  --dry-run: Show what would be deleted without actually deleting"
    echo "  --no-confirm: Skip confirmation prompt (use with caution!)"
    exit 1
fi

CSV_FILE="$1"
DRY_RUN=false
NO_CONFIRM=false

# Parse additional arguments
for arg in "${@:2}"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            ;;
        --no-confirm)
            NO_CONFIRM=true
            ;;
        *)
            echo "Unknown option: $arg"
            exit 1
            ;;
    esac
done

# Check if CSV file exists
if [ ! -f "$CSV_FILE" ]; then
    echo -e "${RED}Error: CSV file '$CSV_FILE' not found${NC}"
    exit 1
fi

# Count total files (excluding header)
TOTAL_FILES=$(tail -n +2 "$CSV_FILE" | wc -l)
echo -e "${YELLOW}Found $TOTAL_FILES entries in CSV file${NC}"

# If dry run, notify user
if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}DRY RUN MODE - No files will be deleted${NC}"
    echo ""
fi

# Extract file paths (column 2) and count files that exist
EXISTING_FILES=0
MISSING_FILES=0

echo "Checking file existence..."
while IFS=',' read -r id filepath rest; do
    # Remove quotes from filepath
    filepath=$(echo "$filepath" | sed 's/^"//;s/"$//')
    
    if [ -f "$filepath" ]; then
        ((EXISTING_FILES++))
    else
        ((MISSING_FILES++))
    fi
done < <(tail -n +2 "$CSV_FILE")

echo -e "${GREEN}Files found: $EXISTING_FILES${NC}"
echo -e "${RED}Files missing: $MISSING_FILES${NC}"
echo ""

# Confirm with user unless --no-confirm is set
if [ "$NO_CONFIRM" = false ] && [ "$DRY_RUN" = false ]; then
    echo -e "${RED}WARNING: This will permanently delete $EXISTING_FILES files!${NC}"
    read -p "Are you sure you want to continue? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo "Operation cancelled"
        exit 0
    fi
fi

# Process files
DELETED_COUNT=0
FAILED_COUNT=0
echo ""
echo "Processing files..."

while IFS=',' read -r id filepath rest; do
    # Remove quotes from filepath
    filepath=$(echo "$filepath" | sed 's/^"//;s/"$//')
    
    if [ -f "$filepath" ]; then
        if [ "$DRY_RUN" = true ]; then
            echo "[DRY RUN] Would delete: $filepath"
            ((DELETED_COUNT++))
        else
            if rm "$filepath" 2>/dev/null; then
                echo -e "${GREEN}✓${NC} Deleted: $filepath"
                ((DELETED_COUNT++))
            else
                echo -e "${RED}✗${NC} Failed to delete: $filepath"
                ((FAILED_COUNT++))
            fi
        fi
    fi
done < <(tail -n +2 "$CSV_FILE")

# Summary
echo ""
echo "========== Summary =========="
if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}DRY RUN RESULTS:${NC}"
    echo "Would delete: $DELETED_COUNT files"
    echo "Missing files: $MISSING_FILES"
else
    echo -e "${GREEN}Deleted: $DELETED_COUNT files${NC}"
    if [ $FAILED_COUNT -gt 0 ]; then
        echo -e "${RED}Failed: $FAILED_COUNT files${NC}"
    fi
    echo -e "Missing: $MISSING_FILES files"
fi

# Optionally clean up empty directories
if [ "$DRY_RUN" = false ] && [ "$NO_CONFIRM" = false ] && [ $DELETED_COUNT -gt 0 ]; then
    echo ""
    read -p "Do you want to remove empty directories? (yes/no): " CLEANUP
    if [ "$CLEANUP" = "yes" ]; then
        # Get unique directories from deleted files
        DIRS=$(tail -n +2 "$CSV_FILE" | cut -d',' -f2 | sed 's/^"//;s/"$//' | xargs -I {} dirname {} | sort -u)
        
        REMOVED_DIRS=0
        for dir in $DIRS; do
            if [ -d "$dir" ] && [ -z "$(ls -A "$dir" 2>/dev/null)" ]; then
                if rmdir "$dir" 2>/dev/null; then
                    echo -e "${GREEN}✓${NC} Removed empty directory: $dir"
                    ((REMOVED_DIRS++))
                fi
            fi
        done
        
        if [ $REMOVED_DIRS -gt 0 ]; then
            echo -e "${GREEN}Removed $REMOVED_DIRS empty directories${NC}"
        else
            echo "No empty directories found"
        fi
    fi
fi