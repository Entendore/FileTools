#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR="${1:-.}"
GITIGNORE_PATH="$TARGET_DIR/.gitignore"

if [ ! -d "$TARGET_DIR" ]; then
  echo "Error: Directory '$TARGET_DIR' does not exist."
  exit 1
fi

echo "Overwriting .gitignore at $GITIGNORE_PATH"

# ---- Start fresh (overwrite)
cat > "$GITIGNORE_PATH" << 'EOF'
# >>> BASE RULES >>>

# OS
.DS_Store
Thumbs.db

# Logs
*.log
logs/

# Python
__pycache__/
*.py[cod]
*.pyo
*.pyd
*.so
.venv/
venv/
env/

# Node
node_modules/

# IDE
.vscode/
.idea/

# Cache / temp
.cache/
tmp/
temp/

# Build / artifacts
build/
dist/

# <<< BASE RULES <<<

# >>> AUTO DISCOVERED RULES >>>
EOF

# ---- Heuristics
SAFE_DIRS_REGEX="^(src|app|lib|include|docs|tests?|examples?)$"

for dir in "$TARGET_DIR"/*/; do
  [ -d "$dir" ] || continue

  name=$(basename "$dir")

  # Skip safe directories
  if [[ "$name" =~ $SAFE_DIRS_REGEX ]]; then
    continue
  fi

  size=$(du -sm "$dir" | cut -f1)

  if [[ "$name" == .* ]] || \
     [[ "$name" =~ (cache|tmp|temp|build|dist|output|logs?|data|models?|checkpoints?|generated) ]] || \
     [[ "$size" -gt 50 ]]; then

    echo "$name/" >> "$GITIGNORE_PATH"
    echo "Ignored: $name/ (size=${size}MB)"
  fi
done

echo "# <<< AUTO DISCOVERED RULES <<<" >> "$GITIGNORE_PATH"

echo "Done."