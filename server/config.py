"""Configuration loader for Lumen. Reads from lumen.yaml with env overrides."""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field

CONFIG_DIR = Path(__file__).parent.parent / "config"
DEFAULT_CONFIG = CONFIG_DIR / "lumen.yaml"
EXAMPLE_CONFIG = CONFIG_DIR / "lumen.yaml.example"


@dataclass
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model_fast: str = "qwen3.5:2b"
    model_general: str = "qwen3.5:4b"
    model_analysis: str = "qwen3.5:9b"
    model_guard: str = "qwen3guard:0.6b"


@dataclass
class ClaudeConfig:
    api_key: str = ""
    model: str = "claude-sonnet-4-6"
    max_monthly_budget: float = 2.00


@dataclass
class TTSConfig:
    engine: str = "kokoro"
    voice: str = "bm_george"
    host: str = "127.0.0.1"
    port: int = 5050


@dataclass
class EmotionConfig:
    enabled: bool = True
    tinybert_model: str = "AdamCodd/tinybert-emotion-balanced"
    nrclex_enabled: bool = True
    min_confidence: float = 0.5  # below this, fall back to VADER mood
    warmup_on_start: bool = True


@dataclass
class ProfileConfig:
    enabled: bool = True
    deep_analysis_interval: str = "daily"
    condense_interval: str = "weekly"
    max_profile_size_kb: int = 50


@dataclass
class GuardrailConfig:
    enabled: bool = True
    min_words: int = 4
    min_chars: int = 20
    self_certainty_check: bool = True
    override_passcode: str = ""  # set in lumen.yaml to bypass app-layer guardrails


@dataclass
class UserConfig:
    name: str = "User"
    timezone: str = "America/New_York"
    teams: list[str] = field(default_factory=lambda: [
        "Eagles", "Phillies", "Sixers", "Flyers", "Union"
    ])


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 3000


@dataclass
class LumenConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    emotion: EmotionConfig = field(default_factory=EmotionConfig)
    profile: ProfileConfig = field(default_factory=ProfileConfig)
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    user: UserConfig = field(default_factory=UserConfig)


def load_config() -> LumenConfig:
    """Load config from YAML file with environment variable overrides."""
    cfg = LumenConfig()

    # Load YAML if it exists
    config_path = DEFAULT_CONFIG
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}

        # Handle nested "models" structure from YAML (models.ollama / models.claude)
        if "models" in raw:
            models = raw.pop("models")
            if "ollama" in models:
                ollama_raw = models["ollama"]
                if "base_url" in ollama_raw:
                    cfg.ollama.base_url = ollama_raw["base_url"]
                if "models" in ollama_raw:
                    m = ollama_raw["models"]
                    cfg.ollama.model_fast = m.get("fast", cfg.ollama.model_fast)
                    cfg.ollama.model_general = m.get("general", cfg.ollama.model_general)
                    cfg.ollama.model_analysis = m.get("analysis", cfg.ollama.model_analysis)
                    cfg.ollama.model_guard = m.get("guard", cfg.ollama.model_guard)
            if "claude" in models:
                _apply_dict(cfg.claude, models["claude"])

        # Handle flat structure (ollama. / claude. at top level)
        _apply_dict(cfg, raw)

        # Handle nested guardrails.quality_checks
        if "guardrails" in raw and isinstance(raw["guardrails"], dict):
            qc = raw["guardrails"].get("quality_checks", {})
            if "min_words" in qc:
                cfg.guardrails.min_words = qc["min_words"]
            if "min_chars" in qc:
                cfg.guardrails.min_chars = qc["min_chars"]
            if "coherence_check" in qc:
                cfg.guardrails.self_certainty_check = qc["coherence_check"]

    # Environment overrides (higher priority)
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        cfg.claude.api_key = key
    if url := os.environ.get("OLLAMA_BASE_URL"):
        cfg.ollama.base_url = url
    if host := os.environ.get("LUMEN_HOST"):
        cfg.server.host = host
    if port := os.environ.get("LUMEN_PORT"):
        cfg.server.port = int(port)

    return cfg


def _apply_dict(obj, d: dict):
    """Recursively apply dict values to a dataclass instance."""
    for key, val in d.items():
        key_under = key.replace("-", "_")
        if hasattr(obj, key_under):
            attr = getattr(obj, key_under)
            if hasattr(attr, "__dataclass_fields__") and isinstance(val, dict):
                _apply_dict(attr, val)
            else:
                setattr(obj, key_under, val)
