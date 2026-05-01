#!/usr/bin/env bash

# A script to safely clean the System Python installation on Windows (C: drive).
# It uninstalls packages, purges site-packages (whitelisting essentials), and clears the pip cache.

# --- Safety Check: Are we using Python on the C: drive? ---
PYTHON_PATH=$(which python)

echo "----------------------------------------------------"
echo "WARNING: You are about to clean your SYSTEM Python installation."
echo "This affects ALL projects using this Python version."
echo "----------------------------------------------------"
echo "Using python at: $PYTHON_PATH"

if [[ "$PYTHON_PATH" != /c/* && "$PYTHON_PATH" != C:* ]]; then
    echo "----------------------------------------------------"
    echo "ERROR: Python does not seem to be installed on the C: drive."
    echo "----------------------------------------------------"
    exit 1
fi

read -p "Are you sure you want to proceed? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
fi

echo "----------------------------------------------------"

# --- Configuration ---
# Set this to "true" to keep setuptools and wheel, otherwise "false"
KEEP_ESSENTIALS=true

# --- Step 1: Uninstall Packages with Pip ---
echo "STEP 1: Uninstalling packages managed by pip..."

packages_to_keep="pip"
if [ "$KEEP_ESSENTIALS" = true ]; then
    packages_to_keep="$packages_to_keep|setuptools|wheel"
fi

# Get all packages and filter out the ones we want to keep
packages_to_uninstall=$(pip list --format=freeze | grep -vE "^($packages_to_keep)=")

if [ -z "$packages_to_uninstall" ]; then
    echo "No packages to uninstall via pip."
else
    echo "The following packages will be uninstalled:"
    echo "$packages_to_uninstall"
    echo ""
    read -p "Proceed with uninstallation? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "$packages_to_uninstall" | while IFS= read -r pkg; do
            pkg_name=$(echo "$pkg" | cut -d'=' -f1)
            echo "Removing $pkg_name..."
            pip uninstall -y "$pkg_name"
        done
    else
        echo "Skipping uninstall."
    fi
fi

echo "----------------------------------------------------"

# --- Step 2: Smart Purge of the site-packages Directory ---
echo "STEP 2: Purging the site-packages directory (Respecting Keep List)..."

# Get the site-packages path
SITE_PACKAGES_PATH=$(python -c "import sysconfig; print(sysconfig.get_path('purelib'))")
SITE_PACKAGES_PATH=$(echo "$SITE_PACKAGES_PATH" | sed 's/\\/\//g')

if [ -d "$SITE_PACKAGES_PATH" ]; then
    echo "Cleaning: $SITE_PACKAGES_PATH"
    
    # Counter for deleted items
    deleted_count=0

    # Loop through every item in site-packages
    for item in "$SITE_PACKAGES_PATH"/*; do
        # Get just the folder/file name
        item_name=$(basename "$item")
        
        # Default action is to delete
        should_delete=true
        
        # If KEEP_ESSENTIALS is true, check if we should preserve this item
        if [ "$KEEP_ESSENTIALS" = true ]; then
            # Check if item is exactly pip, setuptools, or wheel
            if [[ "$item_name" == "pip" || "$item_name" == "setuptools" || "$item_name" == "wheel" ]]; then
                should_delete=false
            fi
            
            # Also check for metadata folders (e.g., pip-23.0.dist-info)
            # We use regex to match: start with pip|setuptools|wheel, followed by anything, ending in .dist-info
            if [[ "$item_name" =~ ^((pip)|(setuptools)|(wheel)).*\.dist-info$ ]]; then
                should_delete=false
            fi
        fi

        # Perform action
        if [ "$should_delete" = true ]; then
            echo "  Deleting: $item_name"
            rm -rf "$item"
            ((deleted_count++))
        else
            echo "  Keeping:  $item_name"
        fi
    done

    echo "----------------------------------------------------"
    echo "Purge complete. Removed $deleted_count items."
else
    echo "Directory not found: $SITE_PACKAGES_PATH"
fi

echo "----------------------------------------------------"

# --- Step 3: Purge the Pip Cache ---
echo "STEP 3: Purging the pip cache..."
read -p "Do you want to purge the pip cache? (y/N) " -n 1 -r
echo

if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Check if pip exists before trying to purge
    if python -m pip --version &> /dev/null; then
        pip cache purge
        echo "pip cache purged."
    else
        echo "Skipping cache purge: pip is not available."
    fi
else
    echo "Skipping pip cache purge."
fi

echo "----------------------------------------------------"
echo "✅ Cleanup Complete!"
if [ "$KEEP_ESSENTIALS" = true ]; then
    echo "Pip, setuptools, and wheel have been preserved."
fi