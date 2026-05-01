#!/bin/bash

# ==============================================================================
# delete_venvs.sh
#
# A script to safely find and delete Python virtual environments.
#
# A virtual environment is identified by the presence of a 'pyvenv.cfg' file.
#
# USAGE:
#   ./delete_venvs.sh [OPTIONS] [DIRECTORY]
#
# EXAMPLES:
#   # Dry run in the current directory (default behavior)
#   ./delete_venvs.sh
#
#   # Dry run in a specific directory
#   ./delete_venvs.sh ~/Projects
#
#   # ACTUALLY DELETE in the current directory
#   ./delete_venvs.sh --delete
#
#   # ACTUALLY DELETE in a specific directory
#   ./delete_venvs.sh --delete ~/Documents/code
#
# ==============================================================================

# --- Script Configuration ---
# Exit immediately if a command exits with a non-zero status.
set -e
# Treat unset variables as an error when substituting.
set -u
# Pipes will fail if any command in the pipeline fails, not just the last one.
set -o pipefail

# --- Default Variables ---
DELETE_MODE=false
TARGET_DIRECTORY="."

# --- Functions ---

# Function to display usage information
show_help() {
    echo "Usage: $0 [OPTIONS] [DIRECTORY]"
    echo
    echo "Find and delete Python virtual environments."
    echo "A virtual environment is identified by the presence of a 'pyvenv.cfg' file."
    echo
    echo "Arguments:"
    echo "  DIRECTORY    The directory to search in. Defaults to '.' (current directory)."
    echo
    echo "Options:"
    echo "  -d, --delete     Permanently delete found environments. If not set, a dry run is performed."
    echo "  -h, --help       Show this help message."
}

# Main function to find and delete venvs
main() {
    local target_dir="$1"
    local delete_mode="$2"

    # Ensure the target directory exists
    if [ ! -d "$target_dir" ]; then
        echo "Error: Directory '$target_dir' not found."
        exit 1
    fi

    echo "Searching for virtual environments in: $(realpath "$target_dir")"
    echo "--------------------------------------------------"

    if [ "$delete_mode" = false ]; then
        echo "DRY RUN MODE: No files will be deleted."
        echo "Use the --delete flag to perform deletion."
    else
        echo "DELETE MODE: This will permanently delete found environments."
    fi
    echo "--------------------------------------------------"

    local found_count=0
    local deleted_count=0

    # Use process substitution to avoid creating a subshell for the while loop,
    # which allows us to modify the counter variables.
    while IFS= read -r -d '' venv_path; do
        # The find command gives us the directory containing pyvenv.cfg directly
        found_count=$((found_count + 1))
        echo "[+] Found virtual environment: $venv_path"

        if [ "$delete_mode" = true ]; then
            echo "    -> Deleting $venv_path..."
            if rm -rf "$venv_path"; then
                echo "    -> Deletion successful."
                deleted_count=$((deleted_count + 1))
            else
                echo "    -> ERROR: Could not delete $venv_path."
            fi
        else
            echo "    -> (Dry run: would delete this folder)"
        fi
    done < <(find "$target_dir" -type f -name "pyvenv.cfg" -printf "%h\0")

    echo "--------------------------------------------------"
    echo "Search complete. Found $found_count virtual environment(s)."
    if [ "$delete_mode" = true ]; then
        echo "Successfully deleted $deleted_count environment(s)."
    fi
}

# --- Argument Parsing ---

while [[ "$#" -gt 0 ]]; do
    case $1 in
        -d|--delete) DELETE_MODE=true; shift ;;
        -h|--help) show_help; exit 0 ;;
        *) TARGET_DIRECTORY="$1"; shift ;;
    esac
done

# --- Execute Main Function ---
main "$TARGET_DIRECTORY" "$DELETE_MODE"