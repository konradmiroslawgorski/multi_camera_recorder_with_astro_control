#!/bin/bash

set -e

PROJECT_DIR="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_DIR"

exec "$PROJECT_DIR/venv_motion/bin/python" -m mcrwac.core.recorder
