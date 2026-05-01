#!/usr/bin/env bash
set -e

# --- Configuration ---
INSTALL_DIR="$HOME/ffmpeg"
ESSENTIALS_URL="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
SHARED_URL="https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full-shared.7z"

# --- Helper Functions ---
check_deps() {
    echo "Checking dependencies..."
    command -v curl >/dev/null 2>&1 || { echo >&2 "ERROR: curl is required."; exit 1; }
    command -v unzip >/dev/null 2>&1 || { echo >&2 "ERROR: unzip is required."; exit 1; }
    
    if command -v 7z >/dev/null 2>&1; then
        SEVEN_ZIP_CMD="7z"
    elif [ -f "/c/Program Files/7-Zip/7z.exe" ]; then
        SEVEN_ZIP_CMD="/c/Program Files/7-Zip/7z.exe"
    else
        echo "ERROR: 7-Zip is required."
        exit 1
    fi
}

# --- Main Logic ---
check_deps

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

echo "Cleaning up..."
rm -rf "$INSTALL_DIR/essentials"
rm -rf "$INSTALL_DIR/shared"
# Clean up old extracted directories
rm -rf ffmpeg-*full_build-shared ffmpeg-*essentials_build

echo "Downloading..."
curl -LfsS "$ESSENTIALS_URL" -o ffmpeg-essentials.zip
curl -LfsS "$SHARED_URL" -o ffmpeg-shared.7z

# --- Essentials ---
echo "Extracting Essentials..."
mkdir -p "$INSTALL_DIR/essentials"
unzip -q -o ffmpeg-essentials.zip
EXTRACTED_DIR=$(find . -maxdepth 1 -type d -name "*essentials_build" -print -quit)
if [ -d "$EXTRACTED_DIR" ]; then
    mv "$EXTRACTED_DIR"/* "$INSTALL_DIR/essentials/"
    rmdir "$EXTRACTED_DIR"
fi

# --- Shared ---
echo "Extracting Shared..."
mkdir -p "$INSTALL_DIR/shared"
"$SEVEN_ZIP_CMD" x ffmpeg-shared.7z -y -aoa > /dev/null

# FIX: Updated to find 'full_build-shared' OR 'full_shared'
EXTRACTED_DIR=$(find . -maxdepth 1 -type d -name "*shared*" -print -quit)

if [ -d "$EXTRACTED_DIR" ]; then
    mv "$EXTRACTED_DIR"/* "$INSTALL_DIR/shared/"
    rmdir "$EXTRACTED_DIR"
fi

# Ensure lib folder is populated
mkdir -p "$INSTALL_DIR/shared/lib"
if ls "$INSTALL_DIR/shared/bin/"*.lib 1> /dev/null 2>&1; then
    mv "$INSTALL_DIR/shared/bin/"*.lib "$INSTALL_DIR/shared/lib/"
fi

echo "Cleaning up archives..."
rm -f ffmpeg-essentials.zip ffmpeg-shared.7z

echo "Done. Libs verified:"
ls "$INSTALL_DIR/shared/lib/*.lib"