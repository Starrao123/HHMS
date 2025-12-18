#!/usr/bin/env bash
set -euo pipefail

# Format and organize imports across the repo
ruff check . --fix || true
isort .
black .

echo "Formatting complete."
