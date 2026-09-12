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
    "orderbook_imbalance_band", "recent_buy_share_band", "component",
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
