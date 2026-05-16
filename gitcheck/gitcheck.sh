#!/bin/bash

set -u
set -o pipefail

# =========================================================
# CONFIG
# =========================================================

SEARCH_DIR="${1:-.}"
PARALLEL_JOBS="${PARALLEL_JOBS:-$(nproc 2>/dev/null || echo 10)}"
DRY_RUN="${DRY_RUN:-0}"
MAX_RETRIES="${MAX_RETRIES:-3}"
COMMIT_MSG_PREFIX="${COMMIT_MSG_PREFIX:-Auto}"
VERBOSE="${VERBOSE:-0}"
EXIT_ON_ERROR="${EXIT_ON_ERROR:-0}"
# How deep to look for project folders (1 means immediate subdirectories)
SEARCH_DEPTH="${SEARCH_DEPTH:-1}"

# Handle Windows paths (E:\folder → /e/folder for Git Bash/MSYS)
if [[ "$SEARCH_DIR" =~ ^([a-zA-Z]):[\\/] ]]; then
    drive_letter=$(echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')
    rest_path="${SEARCH_DIR:3}"
    rest_path="${rest_path//\\//}"
    SEARCH_DIR="/${drive_letter}/${rest_path}"
fi

BASE_DIR="$(pwd)"
SEARCH_DIR="$(realpath "$SEARCH_DIR" 2>/dev/null || echo "$SEARCH_DIR")"

if [[ ! -d "$SEARCH_DIR" ]]; then
    echo "ERROR: Directory does not exist: $SEARCH_DIR"
    exit 1
fi

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")

# Single Master Log file directly in the current directory
MASTER_LOG="$BASE_DIR/git_master_$TIMESTAMP.log"

DONE_FILE=$(mktemp)
REPO_LIST=$(mktemp)

# Cleanup temp files on exit, but KEEP the master log file
trap 'rm -f "$DONE_FILE" "$REPO_LIST"' EXIT

: > "$MASTER_LOG"
: > "$DONE_FILE"

# Color codes
if [[ -t 1 ]]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; NC=''
fi

# =========================================================
# INIT LOGS
# =========================================================

# Print to terminal AND save to the log file
log()        { echo -e "$1" | tee -a "$MASTER_LOG"; }
log_info()    { log "${BLUE}[INFO]${NC} $1"; }
log_success() { log "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { log "${YELLOW}[WARNING]${NC} $1"; }
log_error()   { log "${RED}[ERROR]${NC} $1"; }

log "=================================================="
log "Git Parallel Automation System"
log "=================================================="
log_info "Search Dir   : $SEARCH_DIR"
log_info "Parallel Jobs: $PARALLEL_JOBS"
log_info "Dry Run      : $DRY_RUN"
log_info "Max Retries  : $MAX_RETRIES"
log_info "Search Depth : $SEARCH_DEPTH"
log "=================================================="

# =========================================================
# PRUNE RULES — excludes from directory traversal
# =========================================================

PRUNE_DIRS=(
    "node_modules"
    "target"
    "build"
    "dist"
    "out"
    "bin"
    "obj"
    "vendor"
    "coverage"
    ".next"
    ".nuxt"
    ".cache"
    "__pycache__"
    ".pytest_cache"
    ".mypy_cache"
    ".tox"
    ".venv"
    "venv"
    "env"
    ".idea"
    ".vscode"
    "tmp"
    "temp"
    ".terraform"
)

PRUNE_EXPR=()
for d in "${PRUNE_DIRS[@]}"; do
    PRUNE_EXPR+=( -name "$d" -prune -o )
done

# =========================================================
# FIND REPOS AND NOGIT FOLDERS
# =========================================================

declare -A REPOS=()
declare -A NOGIT_FOLDERS=()

log_info "Scanning for directories in $SEARCH_DIR..."

while IFS= read -r -d '' dir; do
    dir=$(realpath "$dir" 2>/dev/null || echo "$dir")
    
    if [[ -d "$dir/.git" ]]; then
        REPOS["$dir"]=1
        if [[ "$VERBOSE" -eq 1 ]]; then
            log_info "  Found Git Repo: $dir"
        fi
    else
        NOGIT_FOLDERS["$dir"]=1
        if [[ "$VERBOSE" -eq 1 ]]; then
            log_info "  Found Non-Git Dir: $dir"
        fi
    fi
done < <(
    find "$SEARCH_DIR" -mindepth 1 -maxdepth "$SEARCH_DEPTH" \
        \( "${PRUNE_EXPR[@]}" -type d -print0 \) \
        2>/dev/null || true
)

# Pre-log NOGIT folders and mark them as done for the progress bar
for dir in "${!NOGIT_FOLDERS[@]}"; do
    echo "NOGIT | $dir | | " >> "$MASTER_LOG"
    echo "$dir" >> "$DONE_FILE"
done

TOTAL=$(( ${#REPOS[@]} + ${#NOGIT_FOLDERS[@]} ))

if [[ $TOTAL -eq 0 ]]; then
    log_error "No directories found in $SEARCH_DIR"
    exit 1
fi

log_success "Found ${#REPOS[@]} Git repositories and ${#NOGIT_FOLDERS[@]} Non-Git directories"

# =========================================================
# PROGRESS BAR
# =========================================================

progress_bar() {
    local start_time=$(date +%s)

    while true; do
        local DONE=$(wc -l < "$DONE_FILE" 2>/dev/null | tr -d ' ')
        DONE=${DONE:-0}

        if [[ $DONE -ge $TOTAL ]]; then
            echo "" >&2
            break
        fi

        local PCT=$(( DONE * 100 / TOTAL ))
        local current_time=$(date +%s)
        local elapsed=$((current_time - start_time))
        local ETA_STR="starting..."

        if [[ $elapsed -gt 0 && $DONE -gt 0 ]]; then
            local rate=$(echo "scale=2; $DONE / $elapsed" | bc 2>/dev/null || echo "0")
            if [[ $(echo "$rate > 0" | bc 2>/dev/null || echo 0) -eq 1 ]]; then
                local remaining=$(echo "scale=0; ($TOTAL - $DONE) / $rate" | bc)
                local eta_min=$((remaining / 60))
                local eta_sec=$((remaining % 60))
                ETA_STR="${eta_min}m${eta_sec}s"
            else
                ETA_STR="calculating..."
            fi
        fi

        local bar_len=40
        local filled=$(( PCT * bar_len / 100 ))
        local bar=$(printf "%${filled}s" | tr ' ' '#')
        local empty=$(printf "%$((bar_len - filled))s" | tr ' ' '.')

        printf "\r${CYAN}Progress:${NC} [%s%s] ${GREEN}%3d%%${NC} (%d/%d) ${BLUE}ETA:${NC} %-12s" \
            "$bar" "$empty" "$PCT" "$DONE" "$TOTAL" "$ETA_STR" >&2

        sleep 0.2
    done
}

# =========================================================
# REPO WORKER — add, commit, push only. No history changes.
# =========================================================

process_repo() {
    local repo="$1"

    # We already confirmed .git exists during the scan, but cd can still fail
    cd "$repo" || {
        echo "FAIL (cd error) | $repo | | " >> "$MASTER_LOG"
        echo "$repo" >> "$DONE_FILE"
        return 1
    }

    # Validate it's a functioning git repo
    if ! git rev-parse --git-dir >/dev/null 2>&1; then
        echo "FAIL (invalid .git) | $repo | | " >> "$MASTER_LOG"
        echo "$repo" >> "$DONE_FILE"
        return 1
    fi

    # ── Check for changes ─────────────────────────────────
    local branch
    branch=$(git branch --show-current 2>/dev/null || echo "detached")

    if [[ -z "$(git status --porcelain 2>/dev/null)" ]]; then
        echo "SKIP (clean) | $repo | $branch | " >> "$MASTER_LOG"
        echo "$repo" >> "$DONE_FILE"
        return 0
    fi

    local changes_count
    changes_count=$(git status --porcelain 2>/dev/null | wc -l)

    # ── Dry run ───────────────────────────────────────────
    if [[ "$DRY_RUN" -eq 1 ]]; then
        echo "DRYRUN | $repo | $branch | $changes_count changes" >> "$MASTER_LOG"
        echo "$repo" >> "$DONE_FILE"
        return 0
    fi

    # ── git add . ─────────────────────────────────────────
    if ! git add . >/dev/null 2>&1; then
        echo "FAIL (git add) | $repo | $branch | " >> "$MASTER_LOG"
        echo "$repo" >> "$DONE_FILE"
        return 1
    fi

    # ── git commit ────────────────────────────────────────
    local commit_msg="${COMMIT_MSG_PREFIX}: $(date +'%Y-%m-%d %H:%M:%S')"

    if ! git commit -m "$commit_msg" >/dev/null 2>&1; then
        echo "FAIL (git commit) | $repo | $branch | " >> "$MASTER_LOG"
        echo "$repo" >> "$DONE_FILE"
        return 1
    fi

    # ── git push with retry ───────────────────────────────
    local push_target="HEAD"
    if [[ "$branch" != "detached" && -n "$branch" ]]; then
        push_target="$branch"
    fi

    # Check if a remote is configured
    if git remote >/dev/null 2>&1 && [[ -n "$(git remote 2>/dev/null)" ]]; then
        local attempt=0
        local push_ok=0

        while [[ $attempt -lt $MAX_RETRIES ]]; do
            if git push origin "$push_target" >/dev/null 2>&1; then
                push_ok=1
                break
            fi
            attempt=$((attempt + 1))
            if [[ $attempt -lt $MAX_RETRIES ]]; then
                sleep $((attempt * 2))
            fi
        done

        if [[ $push_ok -eq 1 ]]; then
            echo "SUCCESS (pushed) | $repo | $branch | $changes_count changes" >> "$MASTER_LOG"
        else
            echo "FAIL (push failed) | $repo | $branch | " >> "$MASTER_LOG"
        fi
    else
        # No remote — commit succeeded, push not possible
        echo "SUCCESS (no remote) | $repo | $branch | $changes_count changes" >> "$MASTER_LOG"
    fi

    echo "$repo" >> "$DONE_FILE"
    return 0
}

export -f process_repo
export DONE_FILE DRY_RUN MAX_RETRIES COMMIT_MSG_PREFIX VERBOSE MASTER_LOG

# =========================================================
# START
# =========================================================

# Start progress bar in background
progress_bar &
PB_PID=$!

log_info "Starting parallel processing with $PARALLEL_JOBS jobs..."

printf "%s\n" "${!REPOS[@]}" > "$REPO_LIST"

if [[ "$EXIT_ON_ERROR" -eq 1 ]]; then
    xargs -I{} -P "$PARALLEL_JOBS" -d '\n' bash -c 'process_repo "$@"' _ {} < "$REPO_LIST"
else
    xargs -I{} -P "$PARALLEL_JOBS" -d '\n' bash -c 'process_repo "$@" || true' _ {} < "$REPO_LIST"
fi

wait "$PB_PID" 2>/dev/null || true

# =========================================================
# SUMMARY TABLE
# =========================================================

# Extract counts cleanly from the log file
SUCCESS_COUNT=$(grep -cE '^SUCCESS' "$MASTER_LOG" 2>/dev/null || true)
SKIP_COUNT=$(grep -cE '^SKIP' "$MASTER_LOG" 2>/dev/null || true)
DRYRUN_COUNT=$(grep -cE '^DRYRUN' "$MASTER_LOG" 2>/dev/null || true)
FAIL_COUNT=$(grep -cE '^FAIL' "$MASTER_LOG" 2>/dev/null || true)
NOGIT_COUNT=$(grep -cE '^NOGIT' "$MASTER_LOG" 2>/dev/null || true)

# Fallbacks
SUCCESS_COUNT=${SUCCESS_COUNT:-0}
SKIP_COUNT=${SKIP_COUNT:-0}
DRYRUN_COUNT=${DRYRUN_COUNT:-0}
FAIL_COUNT=${FAIL_COUNT:-0}
NOGIT_COUNT=${NOGIT_COUNT:-0}

TOTAL_PROCESSED=$((SUCCESS_COUNT + SKIP_COUNT + DRYRUN_COUNT + FAIL_COUNT + NOGIT_COUNT))

log ""
log "=================================================="
log "SUMMARY"
log "=================================================="
log_info "Processed: $TOTAL_PROCESSED directories"

# ── Success & Skipped Table ──────────────────────────────
log ""
log_success "✅ SUCCESSES & SKIPPED:"
if [[ $((SUCCESS_COUNT + SKIP_COUNT + DRYRUN_COUNT)) -gt 0 ]]; then
    grep -E '^(SUCCESS|SKIP|DRYRUN)' "$MASTER_LOG" | column -t -s '|' | while IFS= read -r line; do
        log "  $line"
    done
else
    log "  None"
fi

# ── Failed Table ─────────────────────────────────────────
log ""
if [[ $FAIL_COUNT -gt 0 ]]; then
    log_error "❌ FAILED:"
    grep '^FAIL' "$MASTER_LOG" | column -t -s '|' | while IFS= read -r line; do
        log "  $line"
    done
fi

# ── No Git Directory Table ───────────────────────────────
log ""
if [[ $NOGIT_COUNT -gt 0 ]]; then
    log_warning "🚫 NO GIT DIRECTORY:"
    grep '^NOGIT' "$MASTER_LOG" | column -t -s '|' | while IFS= read -r line; do
        log "  $line"
    done
fi

log ""
log "=================================================="
log_info "📁 Log File: $MASTER_LOG"
log_success "✨ Done. ✨"

[[ $FAIL_COUNT -gt 0 ]] && exit 1
exit 0