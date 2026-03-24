#!/usr/bin/env bash
# Lumen — One-command installer
# Usage: bash scripts/install.sh

set -euo pipefail

LUMEN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$LUMEN_DIR/.venv"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[lumen]${NC} $1"; }
warn()  { echo -e "${YELLOW}[lumen]${NC} $1"; }
error() { echo -e "${RED}[lumen]${NC} $1"; exit 1; }

echo ""
echo "  ╦  ╦ ╦╔╦╗╔═╗╔╗╔"
echo "  ║  ║ ║║║║║╣ ║║║"
echo "  ╩═╝╚═╝╩ ╩╚═╝╝╚╝"
echo "  Personal AI Assistant"
echo ""

# --------------------------------------------------
# 1. Check system dependencies
# --------------------------------------------------
info "Checking dependencies..."

# Python 3.12+
if ! command -v python3 &>/dev/null; then
    error "Python 3 not found. Install via: brew install python"
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
    error "Python 3.12+ required (found $PY_VERSION). Update via: brew upgrade python"
fi
info "Python $PY_VERSION ✓"

# Rust
if ! command -v rustc &>/dev/null; then
    warn "Rust not found. Installing via rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
fi
RUST_VERSION=$(rustc --version | awk '{print $2}')
info "Rust $RUST_VERSION ✓"

# Ollama
if ! command -v ollama &>/dev/null; then
    error "Ollama not found. Install from: https://ollama.com/download"
fi
info "Ollama ✓"

# --------------------------------------------------
# 2. Python virtual environment
# --------------------------------------------------
if [ ! -d "$VENV_DIR" ]; then
    info "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
info "Virtual environment activated"

info "Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet \
    fastapi \
    uvicorn[standard] \
    httpx \
    pyyaml \
    aiosqlite \
    aiofiles \
    maturin \
    sounddevice \
    numpy

info "Python dependencies ✓"

# --------------------------------------------------
# 3. Build Rust core library
# --------------------------------------------------
info "Building Rust core library (lumen-core)..."
cd "$LUMEN_DIR/crates/lumen-core"
maturin develop --release 2>&1 | tail -1
cd "$LUMEN_DIR"

# Verify import
python3 -c "import lumen_core; print('  lumen_core imported successfully')" || \
    error "Failed to import lumen_core. Check Rust build output above."
info "Rust core ✓"

# --------------------------------------------------
# 4. Pull Ollama models
# --------------------------------------------------
info "Pulling Ollama models (this may take a while on first run)..."

pull_model() {
    local model="$1"
    if ollama list 2>/dev/null | grep -q "^${model}"; then
        info "  $model — already pulled"
    else
        info "  $model — pulling..."
        ollama pull "$model"
    fi
}

pull_model "qwen3.5:2b"
pull_model "qwen3.5:4b"
pull_model "qwen3.5:9b"

# qwen3guard may not be available yet — warn but don't fail
if ! ollama list 2>/dev/null | grep -q "^qwen3guard"; then
    warn "  qwen3guard:0.6b — not available in Ollama yet. Guardrails will use rule-based fallback."
fi

info "Ollama models ✓"

# --------------------------------------------------
# 5. Initialize configuration
# --------------------------------------------------
if [ ! -f "$LUMEN_DIR/config/lumen.yaml" ]; then
    info "Creating default configuration..."
    cp "$LUMEN_DIR/config/lumen.yaml.example" "$LUMEN_DIR/config/lumen.yaml"
    warn "Edit config/lumen.yaml to customize (name, timezone, teams, watchlist)"
else
    info "Configuration exists ✓"
fi

# --------------------------------------------------
# 6. Initialize database
# --------------------------------------------------
DB_PATH="$LUMEN_DIR/data/lumen.db"
if [ ! -f "$DB_PATH" ]; then
    info "Initializing SQLite database..."
    MIGRATION="$LUMEN_DIR/data/migrations/001_initial.sql"
    if [ -f "$MIGRATION" ]; then
        sqlite3 "$DB_PATH" < "$MIGRATION"
        info "Database initialized ✓"
    else
        warn "Migration file not found at $MIGRATION — database will be created on first server start."
    fi
else
    info "Database exists ✓"
fi

# --------------------------------------------------
# 7. Check optional: ANTHROPIC_API_KEY
# --------------------------------------------------
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    warn "ANTHROPIC_API_KEY not set. Claude API fallback will be unavailable."
    warn "Set it in your shell profile or .env file if you want Claude escalation."
fi

# --------------------------------------------------
# Done
# --------------------------------------------------
echo ""
info "=========================================="
info "  Lumen is ready!"
info "=========================================="
info ""
info "  Start:   make start"
info "  Stop:    make stop"
info "  Logs:    make logs"
info "  Test:    make test"
info ""
info "  Config:  config/lumen.yaml"
info "  Data:    data/lumen.db"
info ""
