from __future__ import annotations
import uuid
from typing import Any, Dict, List
from config import FINDING_LIMIT, VERSION
from db import utc_now
from metrics import summarize_oos, evidence_stage


_RUNTIME_FIELDS = {
    "market_family", "symbol", "timeframe", "direction", "regime",
    "entry_source", "micro_alignment", "sl_quality", "sl_quality_band",
    "tp_quality", "tp_quality_band", "defensibility_band",
    "reachability_band", "has_order_block", "has_sweep", "has_pullback",
    "orderbook_imbalance_band", "recent_buy_share_band",
    "oi_change_band", "funding_band", "basis_band", "liquidity_band",
    "component",
}


def rss_mb() -> float:
    try:
        with open('/proc/self/status', 'r', encoding='utf-8') as fh:
            for line in fh:
                if line.startswith('VmRSS:'):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


def run_id() -> str:
    return str(uuid.uuid4())


def runtime_contract(scope: Dict[str, Any]) -> Dict[str, Any]:
    required = sorted(str(k) for k in (scope or {}).keys())
    untrackable = []
    for key in required:
        if key not in _RUNTIME_FIELDS:
            untrackable.append(key)
    component = str((scope or {}).get("component") or "").upper()
    if component and not component.startswith("STRATEGY:"):
        untrackable.append("component")
    untrackable = sorted(set(untrackable))
    return {
        "required_fields": required,
        "runtime_trackable": not untrackable,
        "untrackable_fields": untrackable,
        "contract_version": "RUNTIME_CONTRACT_V1",
    }



TIMEFRAME_ORDER = ("4H", "12H", "1D", "1W", "5M", "15M", "30M", "1H", "2H", "ALL")

def normalized_timeframe(value: Any) -> str:
    raw = str(value or "ALL").strip()
    if raw.lower() in {"1d", "1w"}:
        return raw.upper()
    return raw.upper() or "ALL"

def source_coverage(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_timeframe: Dict[str, int] = {}
    by_system: Dict[str, int] = {}
    by_system_timeframe: Dict[str, int] = {}
    for row in rows or []:
        tf = normalized_timeframe(row.get("timeframe"))
        system = str(row.get("system_type") or "UNKNOWN").strip().upper() or "UNKNOWN"
        by_timeframe[tf] = by_timeframe.get(tf, 0) + 1
        by_system[system] = by_system.get(system, 0) + 1
        key = f"{system}:{tf}"
        by_system_timeframe[key] = by_system_timeframe.get(key, 0) + 1
    strategic = {tf: int(by_timeframe.get(tf, 0)) for tf in ("4H", "12H", "1D", "1W")}
    return {
        "rows": len(rows or []),
        "by_timeframe": dict(sorted(by_timeframe.items())),
        "by_system": dict(sorted(by_system.items())),
        "by_system_timeframe": dict(sorted(by_system_timeframe.items())),
        "strategic_timeframes": strategic,
        "missing_strategic_timeframes": [tf for tf, n in strategic.items() if n <= 0],
    }

def balanced_current_rows(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """Return a compact dashboard sample without letting one TF/engine monopolize it."""
    limit = max(1, int(limit or 1))
    current = [
        row for row in (rows or [])
        if (row.get("meta") or {}).get("is_current") is not False
        and str(row.get("stage") or "") != "STALE"
    ]
    if len(current) <= limit:
        return current

    picked: List[Dict[str, Any]] = []
    seen = set()

    def add(row):
        key = str(row.get("candidate_key") or row.get("feature_key") or id(row))
        if key in seen or len(picked) >= limit:
            return
        seen.add(key)
        picked.append(row)

    # First guarantee visibility per engine x timeframe where evidence exists.
    for tf in TIMEFRAME_ORDER:
        for row in current:
            scope = row.get("scope") or {}
            if normalized_timeframe(scope.get("timeframe")) != tf:
                continue
            engine = str(row.get("source_engine") or row.get("engine") or "UNKNOWN")
            marker = (engine, tf)
            if marker in {(str(x.get("source_engine") or x.get("engine") or "UNKNOWN"), normalized_timeframe((x.get("scope") or {}).get("timeframe"))) for x in picked}:
                continue
            add(row)
            if len(picked) >= limit:
                return picked

    # Then round-robin by timeframe, preserving newest-first order within each bucket.
    buckets = {tf: [] for tf in TIMEFRAME_ORDER}
    other = []
    for row in current:
        tf = normalized_timeframe((row.get("scope") or {}).get("timeframe"))
        (buckets.get(tf) if tf in buckets else other).append(row)
    active = [tf for tf in TIMEFRAME_ORDER if buckets[tf]]
    idx = 0
    while active and len(picked) < limit:
        tf = active[idx % len(active)]
        bucket = buckets[tf]
        while bucket and str(bucket[0].get("candidate_key") or bucket[0].get("feature_key") or id(bucket[0])) in seen:
            bucket.pop(0)
        if bucket:
            add(bucket.pop(0))
        if not bucket:
            active.remove(tf)
            idx = 0
        else:
            idx += 1
    for row in other + current:
        add(row)
        if len(picked) >= limit:
            break
    return picked

def finding(engine: str, experiment: str, scope: Dict[str, Any], rows: List[Dict[str, Any]], *, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    summary = summarize_oos(rows)
    stage = evidence_stage(summary)
    scope_key = '|'.join(f"{k}={scope[k]}" for k in sorted(scope))
    feature_key = f"{engine}:{experiment}:{scope_key}"[:500]
    final_meta = dict(meta or {})
    final_meta.update({
        "runtime_contract": runtime_contract(scope),
        "methodology": "OBSERVATIONAL_ATTRIBUTION_WITH_TEMPORAL_HOLDOUT",
        "causal_candle_replay": False,
    })
    return {
        "engine": engine,
        "experiment": experiment,
        "feature_key": feature_key,
        "scope": scope,
        "stage": stage,
        "authority": "RESEARCH_ONLY",
        "metrics": summary,
        "meta": final_meta,
        "research_version": VERSION,
        "updated_at": utc_now(),
    }


def top_findings(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def score(item):
        metrics = ((item.get('metrics') or {}).get('validation') or {})
        exp = metrics.get('expectancy_r')
        n = metrics.get('resolved') or 0
        try:
            exp = float(exp)
        except Exception:
            exp = -999.0
        return (exp, int(n))
    positives = sorted(items, key=score, reverse=True)
    degraded = sorted(items, key=score)
    merged = []
    seen = set()
    for item in positives[:FINDING_LIMIT//2] + degraded[:FINDING_LIMIT//3] + items[:FINDING_LIMIT//4]:
        key = item.get('feature_key')
        if key not in seen:
            seen.add(key)
            merged.append(item)
        if len(merged) >= FINDING_LIMIT:
            break
    return merged
