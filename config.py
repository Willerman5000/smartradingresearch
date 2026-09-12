from __future__ import annotations
import os


def _int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
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
MEMORY_HARD_MB = _int("RESEARCH_MEMORY_HARD_MB", 350, 128, 480)
MAX_SOURCE_ROWS = _int("RESEARCH_MAX_SOURCE_ROWS", 4000, 200, 20000)
PAGE_SIZE = _int("RESEARCH_PAGE_SIZE", 250, 50, 1000)
WINDOW_DAYS = _int("RESEARCH_WINDOW_DAYS", 180, 30, 730)
FINDING_LIMIT = _int("RESEARCH_FINDING_LIMIT", 120, 20, 500)
AUTO_RUN = _bool("RESEARCH_AUTO_RUN", True)
AUTO_INTERVAL_MINUTES = _int("RESEARCH_AUTO_INTERVAL_MINUTES", 180, 30, 1440)
BOOT_DELAY_SECONDS = _int("RESEARCH_BOOT_DELAY_SECONDS", 45, 5, 600)
AUTHORITY = "RESEARCH_ONLY"
VERSION = "RFV1_5_CONTINUOUS_FACTORY_20260912"
VALID_ENGINES = {"execution", "risk", "strategy", "traders", "validation"}

DASHBOARD_MAX_ROWS = _int("RESEARCH_DASHBOARD_MAX_ROWS", 120, 20, 300)

MIN_NET_EVIDENCE_PCT = _int("RESEARCH_MIN_NET_EVIDENCE_PCT", 80, 0, 100)
WALK_FORWARD_FOLDS = _int("RESEARCH_WALK_FORWARD_FOLDS", 3, 2, 6)

# Commit G — Continuous Strategy Factory.  The factory is deliberately bounded
# so the free Render instance never explores a combinatorial explosion.
FACTORY_MAX_CANDIDATES = _int("RESEARCH_FACTORY_MAX_CANDIDATES", 40, 10, 80)
FACTORY_MIN_SOURCE_ROWS = _int("RESEARCH_FACTORY_MIN_SOURCE_ROWS", 6, 5, 30)
