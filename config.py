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

# Research stays inside the same 512 MB envelope; the representative contract
# reduces heavy causal cells from 92 to 60 rather than increasing memory.
MEMORY_HARD_MB = _int("RESEARCH_MEMORY_HARD_MB", 430, 192, 460)
CAUSAL_MEMORY_TARGET_MB = _int("RESEARCH_CAUSAL_MEMORY_TARGET_MB", 390, 160, MEMORY_HARD_MB)
MAX_SOURCE_ROWS = _int("RESEARCH_MAX_SOURCE_ROWS", 1200, 200, 5000)
PAGE_SIZE = _int("RESEARCH_PAGE_SIZE", 200, 50, 500)
WINDOW_DAYS = _int("RESEARCH_WINDOW_DAYS", 180, 30, 730)
FINDING_LIMIT = _int("RESEARCH_FINDING_LIMIT", 180, 20, 500)
AUTO_RUN = _bool("RESEARCH_AUTO_RUN", True)
AUTO_INTERVAL_MINUTES = _int("RESEARCH_AUTO_INTERVAL_MINUTES", 180, 10, 1440)

# Commit 9.6 — Fast-20. Environment values left at the old 5-minute cadence are
# intentionally clamped to 20, while watermark/event checks can still skip the
# heavy job when no new evidence or candle exists.
BOOTSTRAP_ACCELERATED = _bool("RESEARCH_BOOTSTRAP_ACCELERATED", True)
BOOTSTRAP_FAST_MINUTES = _int("RESEARCH_BOOTSTRAP_FAST_MINUTES", 20, 20, 180)
BOOTSTRAP_BACKOFF_MINUTES = _int("RESEARCH_BOOTSTRAP_BACKOFF_MINUTES", 60, 20, 360)
BOOTSTRAP_STALL_CYCLES = _int("RESEARCH_BOOTSTRAP_STALL_CYCLES", 3, 1, 12)
BOOTSTRAP_VALIDATION_DELAY_SECONDS = _int("RESEARCH_BOOTSTRAP_VALIDATION_DELAY_SECONDS", 90, 30, 300)
BOOT_DELAY_SECONDS = _int("RESEARCH_BOOT_DELAY_SECONDS", 45, 5, 600)

AUTHORITY = "RESEARCH_ONLY"

# Commit 9.6 final learning contract. Heavy backtest/OOS is the primary source
# for strategy/Entry/SL/TP/TF calibration. Shadow/live is preserved only as
# out-of-time continuity evidence and alpha-decay governance; samples are never
# merged.
LEARNING_MODE = "BACKTEST_OOS_PRIMARY_LIVE_ALPHA_DECAY"
REVIEWTRADER_PRIMARY_SOURCE = "RESEARCH_BACKTEST_OOS"
LIVE_ROLE = "ALPHA_DECAY_AND_EXECUTION_VALIDATION"
VERSION = "RFV1_15_RC9_7_1_60CELL_RUNTIME_FIX_20260918"
SCHEDULER_POLICY_VERSION = "RC9_7_5_FINAL_POLISH_VALIDATION_EGRESS"
RELEASE_LABEL = "V1.1 · BACKTEST-PRIMARY EDGE · FINAL FREEZE"
VALID_ENGINES = {"execution", "risk", "strategy", "traders", "validation"}
DASHBOARD_MAX_ROWS = _int("RESEARCH_DASHBOARD_MAX_ROWS", 180, 20, 300)

FREE_PLAN_MODE = _bool("RESEARCH_FREE_PLAN_MODE", True)
OBSERVATIONAL_MIN_REFRESH_MINUTES = _int("RESEARCH_OBSERVATIONAL_MIN_REFRESH_MINUTES", 60, 15, 720)
OBSERVATIONAL_MAX_REFRESH_MINUTES = _int("RESEARCH_OBSERVATIONAL_MAX_REFRESH_MINUTES", 240, 60, 1440)
# RC9.7.5 final polish: keep a hard Free-plan budget while preventing the
# validation service from starving itself. Evidence engines get 4 MB/day and
# Validation 16 MB/day. Worst-case application reads stay around 32 MB/day
# (~0.96 GB/30d), leaving wide headroom under Supabase Free's 5 GB egress.
# In Free mode this safety budget is authoritative so stale Render env values
# such as 2/4 MB cannot silently disable Validation again.
_FREE_EGRESS_CAP_MB = 16 if ENGINE == "validation" else 4
EGRESS_DAILY_MB = _FREE_EGRESS_CAP_MB if FREE_PLAN_MODE else _int("RESEARCH_EGRESS_DAILY_MB", 10, 1, 64)
MIN_NET_EVIDENCE_PCT = _int("RESEARCH_MIN_NET_EVIDENCE_PCT", 80, 0, 100)
WALK_FORWARD_FOLDS = _int("RESEARCH_WALK_FORWARD_FOLDS", 3, 2, 6)

FACTORY_MAX_CANDIDATES = _int("RESEARCH_FACTORY_MAX_CANDIDATES", 48, 10, 80)
FACTORY_MIN_SOURCE_ROWS = _int("RESEARCH_FACTORY_MIN_SOURCE_ROWS", 6, 5, 30)

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

# Commit 9.6 heavy Research: 24 Spot + 36 representative Futures = 60.
# Production itself is broader (150 action cells); group priors never become
# local Champions without symbol-specific Shadow/live evidence.
CAUSAL_REQUIRED_CELLS = 60
CAUSAL_FETCH_WORKERS = _int("RESEARCH_CAUSAL_FETCH_WORKERS", 3, 1, 5)
# Validation must validate evidence, not duplicate the four heavy search lanes.
# Old Render envs may still contain RESEARCH_CAUSAL_VALIDATION_RESCUE=1; on the
# Free plan RC9.7.4 deliberately ignores that legacy value to protect bandwidth.
CAUSAL_VALIDATION_RESCUE = _bool("RESEARCH_CAUSAL_VALIDATION_RESCUE", False) and not FREE_PLAN_MODE
CAUSAL_VALIDATION_RESCUE_MAX_CELLS = 0 if FREE_PLAN_MODE else _int("RESEARCH_CAUSAL_VALIDATION_RESCUE_MAX_CELLS", 2, 0, 8)
CAUSAL_REGISTRY_RETEST_LIMIT = _int("RESEARCH_CAUSAL_REGISTRY_RETEST_LIMIT", 16, 0, 60)
CAUSAL_FINALISTS_PER_CELL = _int("RESEARCH_CAUSAL_FINALISTS_PER_CELL", 4, 1, 6)

RC5_SEARCH_WAVES = _int("RESEARCH_RC5_SEARCH_WAVES", 8, 4, 12)
RC5_BROAD_SEARCH_GUARD = _bool("RESEARCH_RC5_BROAD_SEARCH_GUARD", True)
RC5_MIN_POSITIVE_FOLD_RATIO = _float("RESEARCH_RC5_MIN_POSITIVE_FOLD_RATIO", 0.75, 0.60, 1.0)
RC5_DECLARED_CANDIDATE_BUDGET = _int("RESEARCH_RC5_DECLARED_CANDIDATE_BUDGET", 180, 40, 300)

# Current Exchange Flow remains observational until it proves incremental edge.
EXCHANGE_FLOW_ENABLED = _bool("RESEARCH_EXCHANGE_FLOW_ENABLED", True)
