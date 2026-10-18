#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
mkdir -p work
export UV_CACHE_DIR="$PROJECT_ROOT/work/uv-cache"
export CARGO_HOME="$PROJECT_ROOT/work/cargo-cache"
export MPLCONFIGDIR="$PROJECT_ROOT/work/matplotlib"
unset CONDA_PREFIX
uv sync --frozen
if [[ ! -f frontend/dist/index.html ]]; then
  npm --prefix frontend ci --cache "$PROJECT_ROOT/work/npm-cache" --no-audit --no-fund
  npm --prefix frontend run build
fi
uv run --frozen sse50 prepare
exec uv run --frozen sse50 serve --port "${1:-18765}"
