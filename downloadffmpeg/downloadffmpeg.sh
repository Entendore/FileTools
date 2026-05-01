#!/usr/bin/env bash
set -e

# --- Configuration ---
INSTALL_DIR="$HOME/ffmpeg"
ESSENTIALS_URL="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FULL_URL="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z"
# Shared build is required for Python 'av' compilation
SHARED_URL="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full-shared.7z"

# --- Helper Functions ---
check_deps() {
    echo "Checking dependencies..."
    command -v curl >/dev/null 2>&1 || { echo >&2 "ERROR: curl is required."; exit 1; }
    command -v unzip >/dev/null 2>&1 || { echo >&2 "ERROR: unzip is required."; exit 1; }
    
    # Check for 7z in system PATH or standard Windows location
    if command -v 7z >/dev/null 2>&1; then
        SEVEN_ZIP_CMD="7z"
    elif [ -f "/c/Program Files/7-Zip/7z.exe" ]; then
        SEVEN_ZIP_CMD="/c/Program Files/7-Zip/7z.exe"
        echo "Found 7-Zip in Program Files."
    else
        echo "ERROR: 7-Zip is required for Full/Shared builds."
        echo "Please install it from https://7-zip.org/download.html"
        exit 1
    fi
}

# --- Main Logic ---
check_deps

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# --- FIX: Delete old folders to avoid "Directory not empty" errors ---
echo "Cleaning up previous installations..."
rm -rf "$INSTALL_DIR/essentials"
rm -rf "$INSTALL_DIR/full"
rm -rf "$INSTALL_DIR/shared"

echo "Downloading FFmpeg builds..."

# Clean up previous archives to ensure fresh download
rm -f ffmpeg-essentials.zip ffmpeg-full.7z ffmpeg-shared.7z

# Download files
curl -LfsS "$ESSENTIALS_URL" -o ffmpeg-essentials.zip
curl -LfsS "$FULL_URL" -o ffmpeg-full.7z
curl -LfsS "$SHARED_URL" -o ffmpeg-shared.7z

echo "Verifying downloads..."
ls -lh ffmpeg-*

# Sanity check
FILE_SIZE=$(stat -c%s "ffmpeg-shared.7z")
if [ "$FILE_SIZE" -lt 50000000 ]; then
    echo "ERROR: Shared build file size too small. Download might be corrupt."
    exit 1
fi

# --- Extract Essentials ---
echo "Extracting Essentials..."
# Create folder fresh
mkdir -p "$INSTALL_DIR/essentials"
unzip -q -o ffmpeg-essentials.zip

EXTRACTED_DIR=$(find . -maxdepth 1 -type d -name "*essentials_build" -print -quit)
if [ -d "$EXTRACTED_DIR" ]; then
    mv "$EXTRACTED_DIR"/* "$INSTALL_DIR/essentials/"
    rmdir "$EXTRACTED_DIR"
fi

# --- Extract Full ---
echo "Extracting Full Build..."
mkdir -p "$INSTALL_DIR/full"
"$SEVEN_ZIP_CMD" x ffmpeg-full.7z -y -aoa > /dev/null

EXTRACTED_DIR=$(find . -maxdepth 1 -type d -name "*full_build" -print -quit)
if [ -d "$EXTRACTED_DIR" ]; then
    mv "$EXTRACTED_DIR"/* "$INSTALL_DIR/full/"
    rmdir "$EXTRACTED_DIR"
fi

# --- Extract Shared ---
echo "Extracting Shared Build (for Python development)..."
mkdir -p "$INSTALL_DIR/shared"
"$SEVEN_ZIP_CMD" x ffmpeg-shared.7z -y -aoa > /dev/null

EXTRACTED_DIR=$(find . -maxdepth 1 -type d -name "*full_shared" -print -quit)
if [ -d "$EXTRACTED_DIR" ]; then
    mv "$EXTRACTED_DIR"/* "$INSTALL_DIR/shared/"
    rmdir "$EXTRACTED_DIR"
fi

echo "Cleaning up archives..."
rm -f ffmpeg-essentials.zip ffmpeg-full.7z ffmpeg-shared.7z

echo "--------------------------------------------------"
echo "Done!"
echo "Essentials Binaries: $INSTALL_DIR/essentials/bin/"
echo "Full Binaries:       $INSTALL_DIR/full/bin/"
echo "Shared (Dev) Files:  $INSTALL_DIR/shared/"
echo "--------------------------------------------------"