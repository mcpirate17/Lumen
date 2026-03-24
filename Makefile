.PHONY: start stop restart logs test build install clean status tts-start tts-stop

LUMEN_DIR := $(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
VENV := $(LUMEN_DIR)/.venv
PYTHON := $(VENV)/bin/python3
PID_FILE := $(LUMEN_DIR)/data/lumen.pid
LOG_FILE := $(LUMEN_DIR)/data/lumen.log

# Start the Lumen server
start:
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "[lumen] Already running (PID $$(cat $(PID_FILE)))"; \
	else \
		echo "[lumen] Starting server on port 3000..."; \
		$(PYTHON) -m uvicorn server.app:app \
			--host 127.0.0.1 \
			--port 3000 \
			--app-dir $(LUMEN_DIR) \
			>> $(LOG_FILE) 2>&1 & \
		echo $$! > $(PID_FILE); \
		echo "[lumen] Started (PID $$(cat $(PID_FILE)))"; \
	fi

# Stop the server
stop:
	@if [ -f $(PID_FILE) ]; then \
		PID=$$(cat $(PID_FILE)); \
		if kill -0 $$PID 2>/dev/null; then \
			kill $$PID; \
			echo "[lumen] Stopped (PID $$PID)"; \
		else \
			echo "[lumen] Process $$PID not running"; \
		fi; \
		rm -f $(PID_FILE); \
	else \
		echo "[lumen] Not running (no PID file)"; \
	fi

# Restart
restart: stop start

# Tail logs
logs:
	@tail -f $(LOG_FILE) 2>/dev/null || echo "[lumen] No log file yet"

# Run tests
test:
	@$(PYTHON) -m pytest $(LUMEN_DIR)/tests/ -v 2>/dev/null || echo "[lumen] No tests found yet"

# Build Rust core
build:
	@echo "[lumen] Building lumen-core..."
	@cd $(LUMEN_DIR)/crates/lumen-core && $(VENV)/bin/maturin develop --release
	@echo "[lumen] Build complete"

# Full install
install:
	@bash $(LUMEN_DIR)/scripts/install.sh

# Show status
status:
	@echo "=== Lumen Status ==="
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		echo "Server:  running (PID $$(cat $(PID_FILE)))"; \
	else \
		echo "Server:  stopped"; \
	fi
	@curl -s http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
		&& echo "Ollama:  running" \
		|| echo "Ollama:  not running"
	@curl -s http://127.0.0.1:5050/ping 2>/dev/null | grep -q '"ok":true' \
		&& echo "TTS:     running" \
		|| echo "TTS:     not running"
	@[ -f $(LUMEN_DIR)/data/lumen.db ] \
		&& echo "Database: exists ($$(du -h $(LUMEN_DIR)/data/lumen.db | cut -f1))" \
		|| echo "Database: not created"

TTS_PID_FILE := $(LUMEN_DIR)/data/tts.pid
TTS_LOG_FILE := $(LUMEN_DIR)/data/tts.log

# Start the TTS server
tts-start:
	@if [ -f $(TTS_PID_FILE) ] && kill -0 $$(cat $(TTS_PID_FILE)) 2>/dev/null; then \
		echo "[lumen] TTS already running (PID $$(cat $(TTS_PID_FILE)))"; \
	else \
		echo "[lumen] Starting TTS server on port 5050..."; \
		$(PYTHON) -m server.tts_server \
			>> $(TTS_LOG_FILE) 2>&1 & \
		echo $$! > $(TTS_PID_FILE); \
		echo "[lumen] TTS started (PID $$(cat $(TTS_PID_FILE)))"; \
	fi

# Stop the TTS server
tts-stop:
	@if [ -f $(TTS_PID_FILE) ]; then \
		PID=$$(cat $(TTS_PID_FILE)); \
		if kill -0 $$PID 2>/dev/null; then \
			kill $$PID; \
			echo "[lumen] TTS stopped (PID $$PID)"; \
		else \
			echo "[lumen] TTS process $$PID not running"; \
		fi; \
		rm -f $(TTS_PID_FILE); \
	else \
		echo "[lumen] TTS not running (no PID file)"; \
	fi

# Clean build artifacts (keeps data and config)
clean:
	@echo "[lumen] Cleaning build artifacts..."
	@rm -rf $(LUMEN_DIR)/crates/lumen-core/target
	@find $(LUMEN_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@echo "[lumen] Clean"
