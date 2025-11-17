#!/usr/bin/env bash

echo "Using python at: $(which python)"
echo "Using pip at: $(which pip)"

echo "Collecting installed packages..."
packages=$(pip list --format=freeze | grep -v "^pip=")

if [ -z "$packages" ]; then
    echo "No packages to uninstall."
    exit 0
fi

echo "Uninstalling all packages except pip..."
for pkg in $packages; do
    echo "Removing $pkg"
    pip uninstall -y "$pkg"
done

echo "Done. Only pip should remain installed."
