#!/usr/bin/env bash
set -euo pipefail

ruff check .

echo "Linting complete."
