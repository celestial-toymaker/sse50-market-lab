#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
export UV_CACHE_DIR="$PROJECT_ROOT/work/uv-cache"
export CARGO_HOME="$PROJECT_ROOT/work/cargo-cache"
export VIRTUAL_ENV="$PROJECT_ROOT/.venv"
export PYO3_PYTHON="$PROJECT_ROOT/.venv/bin/python"
unset CONDA_PREFIX
exec .venv/bin/maturin develop --release
