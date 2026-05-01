#!/usr/bin/env bash

echo "Listing installed packages..."
# Using python -m pip ensures we target the active environment
packages=$(python -m pip list --format=freeze | cut -d '=' -f1)

to_uninstall=()

echo "Filtering packages..."
for pkg in $packages; do
    # Convert to lowercase for case-insensitive matching (pip normalizes to lowercase anyway)
    lower_pkg=$(echo "$pkg" | tr '[:upper:]' '[:lower:]')
    
    if [[ "$lower_pkg" != "pip" && "$lower_pkg" != "setuptools" && "$lower_pkg" != "wheel" ]]; then
        to_uninstall+=("$pkg")
    fi
done

if [ ${#to_uninstall[@]} -eq 0 ]; then
    echo "No packages to uninstall."
else
    echo "Uninstalling ${#to_uninstall[@]} packages..."
    # Uninstall them all in one command (MUCH faster than a loop)
    python -m pip uninstall -y "${to_uninstall[@]}"
fi

echo "Cleaning up invalid distributions (folders starting with ~)..."

# We use a heredoc to run a multi-line Python script directly inside Bash.
# This script uses Windows' native 'rmdir' to force-delete stubborn folders.
python << 'EOF'
import subprocess
import pathlib

# Ask pip EXACTLY where its site-packages folder is
result = subprocess.run(['python', '-m', 'pip', 'show', 'pip'], capture_output=True, text=True)
location = ""
for line in result.stdout.splitlines():
    if line.startswith("Location:"):
        location = line.split(":", 1)[1].strip()
        break

if not location:
    print("Could not determine site-packages location.")
else:
    sp_path = pathlib.Path(location)
    print(f"Scanning: {sp_path}")
    removed = 0
    for p in sp_path.iterdir():
        # Look for any directory starting with ~ (like ~orch or ~-orch)
        if p.is_dir() and p.name.startswith('~'):
            print(f"Found invalid distribution: {p.name}")
            try:
                # Use the native Windows command to forcefully delete the directory
                # This is much more reliable on Windows than Python's shutil.rmtree
                subprocess.run(['cmd', '/c', 'rmdir', '/S', '/Q', str(p)], check=True)
                removed += 1
                print(f"Deleted {p.name}")
            except Exception as e:
                print(f"Failed to delete {p.name}: {e}")
    print(f"Removed {removed} invalid distribution(s).")
EOF

echo "Purging pip cache..."
# Fulfilling the original request to purge the cache
python -m pip cache purge

echo "Done. Only pip, setuptools, and wheel remain. Cache is purged. Invalid distributions deleted."