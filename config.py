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
AUTO_INTERVAL_MINUTES = _int("RESEARCH_AUTO_INTERVAL_MINUTES", 180, 10, 1440)
# RC5 — accelerated iterative search. Research stays fast while any active
# market×symbol×TF cell lacks a CURRENT SHADOW_READY/SHADOW_READY_FAST specialist.
# Default cadence is 15 minutes (10 is allowed by env). Only after 46/46 cells
# have a validated Shadow-ready specialist does the system return to 180 minutes.
# Memory pressure can temporarily back off without lowering any validation gate.
BOOTSTRAP_ACCELERATED = _bool("RESEARCH_BOOTSTRAP_ACCELERATED", True)
BOOTSTRAP_FAST_MINUTES = _int("RESEARCH_BOOTSTRAP_FAST_MINUTES", 10, 10, 180)
BOOTSTRAP_BACKOFF_MINUTES = _int("RESEARCH_BOOTSTRAP_BACKOFF_MINUTES", 60, 15, 360)
BOOTSTRAP_STALL_CYCLES = _int("RESEARCH_BOOTSTRAP_STALL_CYCLES", 3, 1, 12)
BOOTSTRAP_VALIDATION_DELAY_SECONDS = _int("RESEARCH_BOOTSTRAP_VALIDATION_DELAY_SECONDS", 90, 30, 300)
BOOT_DELAY_SECONDS = _int("RESEARCH_BOOT_DELAY_SECONDS", 45, 5, 600)
AUTHORITY = "RESEARCH_ONLY"
VERSION = "RFV1_12_RC5_ITERATIVE_EDGE_46CELL_20260915"
SCHEDULER_POLICY_VERSION = "RC6_FAST10_PRESERVE_CHAMPIONS"
RELEASE_LABEL = "V1 · ITERATIVE EDGE"
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
CAUSAL_MAX_REFINED_PER_CELL = _int("RESEARCH_CAUSAL_MAX_REFINED_PER_CELL", 84, 16, 180)
CAUSAL_SPOT_ROUNDTRIP_BPS = _float("RESEARCH_CAUSAL_SPOT_ROUNDTRIP_BPS", 20.0, 0.0, 100.0)
CAUSAL_SPOT_SLIPPAGE_BPS = _float("RESEARCH_CAUSAL_SPOT_SLIPPAGE_BPS", 4.0, 0.0, 50.0)
CAUSAL_FUTURES_ROUNDTRIP_BPS = _float("RESEARCH_CAUSAL_FUTURES_ROUNDTRIP_BPS", 12.0, 0.0, 100.0)
CAUSAL_FUTURES_SLIPPAGE_BPS = _float("RESEARCH_CAUSAL_FUTURES_SLIPPAGE_BPS", 6.0, 0.0, 50.0)
CAUSAL_FUNDING_STRESS_BPS_8H = _float("RESEARCH_CAUSAL_FUNDING_STRESS_BPS_8H", 1.0, 0.0, 20.0)


# Commit I.1 — cierre determinista de la matriz 18/18.
CAUSAL_REQUIRED_CELLS = 46
CAUSAL_FETCH_WORKERS = _int("RESEARCH_CAUSAL_FETCH_WORKERS", 3, 1, 5)
CAUSAL_VALIDATION_RESCUE = _bool("RESEARCH_CAUSAL_VALIDATION_RESCUE", True)
CAUSAL_VALIDATION_RESCUE_MAX_CELLS = _int("RESEARCH_CAUSAL_VALIDATION_RESCUE_MAX_CELLS", 12, 0, 40)
CAUSAL_REGISTRY_RETEST_LIMIT = _int("RESEARCH_CAUSAL_REGISTRY_RETEST_LIMIT", 16, 0, 60)
CAUSAL_FINALISTS_PER_CELL = _int("RESEARCH_CAUSAL_FINALISTS_PER_CELL", 4, 1, 6)

# RC5 — bounded exhaustive logical search. Each pending cell rotates through a
# pre-declared indicator/strategy grammar every fast cycle. This is deliberately
# NOT a random Cartesian search: Final OOS never selects the next wave.
RC5_SEARCH_WAVES = _int("RESEARCH_RC5_SEARCH_WAVES", 8, 4, 12)
RC5_BROAD_SEARCH_GUARD = _bool("RESEARCH_RC5_BROAD_SEARCH_GUARD", True)
RC5_MIN_POSITIVE_FOLD_RATIO = _float("RESEARCH_RC5_MIN_POSITIVE_FOLD_RATIO", 0.75, 0.60, 1.0)
RC5_DECLARED_CANDIDATE_BUDGET = _int("RESEARCH_RC5_DECLARED_CANDIDATE_BUDGET", 180, 40, 300)

# Current Exchange Flow context is observational only until a trustworthy
# historical provider exists. It cannot become SHADOW_READY by itself.
EXCHANGE_FLOW_ENABLED = _bool("RESEARCH_EXCHANGE_FLOW_ENABLED", True)
