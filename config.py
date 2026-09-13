from __future__ import annotations
import os


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

ENGINE = str(os.getenv("RESEARCH_ENGINE", "execution")).strip().lower()
CENTRAL_URL = str(os.getenv("CENTRAL_SUPABASE_URL", "")).rstrip("/")
CENTRAL_KEY = str(os.getenv("CENTRAL_SUPABASE_SERVICE_KEY", "")).strip()
PRIVATE_URL = str(os.getenv("RESEARCH_SUPABASE_URL", "")).rstrip("/") or CENTRAL_URL
PRIVATE_KEY = str(os.getenv("RESEARCH_SUPABASE_SERVICE_KEY", "")).strip() or CENTRAL_KEY
RUN_TOKEN = str(os.getenv("RESEARCH_RUN_TOKEN", "")).strip()

# Commit I deliberately uses more of the 512 MB Render research instances, but
# keeps ~70-90 MB headroom for Gunicorn/requests/JSON/GC spikes.
MEMORY_HARD_MB = _int("RESEARCH_MEMORY_HARD_MB", 430, 192, 460)
CAUSAL_MEMORY_TARGET_MB = _int("RESEARCH_CAUSAL_MEMORY_TARGET_MB", 390, 160, MEMORY_HARD_MB)
MAX_SOURCE_ROWS = _int("RESEARCH_MAX_SOURCE_ROWS", 5000, 200, 20000)
PAGE_SIZE = _int("RESEARCH_PAGE_SIZE", 400, 50, 1000)
WINDOW_DAYS = _int("RESEARCH_WINDOW_DAYS", 180, 30, 730)
FINDING_LIMIT = _int("RESEARCH_FINDING_LIMIT", 180, 20, 500)
AUTO_RUN = _bool("RESEARCH_AUTO_RUN", True)
AUTO_INTERVAL_MINUTES = _int("RESEARCH_AUTO_INTERVAL_MINUTES", 180, 30, 1440)
BOOT_DELAY_SECONDS = _int("RESEARCH_BOOT_DELAY_SECONDS", 45, 5, 600)
AUTHORITY = "RESEARCH_ONLY"
VERSION = "RFV1_6_CAUSAL_COVERAGE_20260913"
VALID_ENGINES = {"execution", "risk", "strategy", "traders", "validation"}
DASHBOARD_MAX_ROWS = _int("RESEARCH_DASHBOARD_MAX_ROWS", 180, 20, 300)

MIN_NET_EVIDENCE_PCT = _int("RESEARCH_MIN_NET_EVIDENCE_PCT", 80, 0, 100)
WALK_FORWARD_FOLDS = _int("RESEARCH_WALK_FORWARD_FOLDS", 3, 2, 6)

# Commit G — Continuous Strategy Factory.
FACTORY_MAX_CANDIDATES = _int("RESEARCH_FACTORY_MAX_CANDIDATES", 48, 10, 80)
FACTORY_MIN_SOURCE_ROWS = _int("RESEARCH_FACTORY_MIN_SOURCE_ROWS", 6, 5, 30)

# Commit I — distributed causal backtesting. Each analytical engine owns a
# different market×timeframe lane, so RAM/API work is not duplicated.
CAUSAL_ENABLED = _bool("RESEARCH_CAUSAL_ENABLED", True)
CAUSAL_MAX_BARS = _int("RESEARCH_CAUSAL_MAX_BARS", 9000, 500, 20000)
CAUSAL_CACHE_MB = _int("RESEARCH_CAUSAL_CACHE_MB", 240, 32, 340)
CAUSAL_HTTP_PAUSE_MS = _int("RESEARCH_CAUSAL_HTTP_PAUSE_MS", 80, 0, 1000)
CAUSAL_REFINE_SEEDS = _int("RESEARCH_CAUSAL_REFINE_SEEDS", 4, 2, 8)
CAUSAL_MAX_REFINED_PER_CELL = _int("RESEARCH_CAUSAL_MAX_REFINED_PER_CELL", 72, 12, 160)
CAUSAL_SPOT_ROUNDTRIP_BPS = _float("RESEARCH_CAUSAL_SPOT_ROUNDTRIP_BPS", 20.0, 0.0, 100.0)
CAUSAL_SPOT_SLIPPAGE_BPS = _float("RESEARCH_CAUSAL_SPOT_SLIPPAGE_BPS", 4.0, 0.0, 50.0)
CAUSAL_FUTURES_ROUNDTRIP_BPS = _float("RESEARCH_CAUSAL_FUTURES_ROUNDTRIP_BPS", 12.0, 0.0, 100.0)
CAUSAL_FUTURES_SLIPPAGE_BPS = _float("RESEARCH_CAUSAL_FUTURES_SLIPPAGE_BPS", 6.0, 0.0, 50.0)
CAUSAL_FUNDING_STRESS_BPS_8H = _float("RESEARCH_CAUSAL_FUNDING_STRESS_BPS_8H", 1.0, 0.0, 20.0)

# Current Exchange Flow context is observational only until a trustworthy
# historical provider exists. It cannot become SHADOW_READY by itself.
EXCHANGE_FLOW_ENABLED = _bool("RESEARCH_EXCHANGE_FLOW_ENABLED", True)
