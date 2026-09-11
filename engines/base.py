from __future__ import annotations
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from config import FINDING_LIMIT, VERSION
from db import utc_now
from metrics import summarize_oos, evidence_stage


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


def finding(engine: str, experiment: str, scope: Dict[str, Any], rows: List[Dict[str, Any]], *, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    summary = summarize_oos(rows)
    stage = evidence_stage(summary)
    scope_key = '|'.join(f"{k}={scope[k]}" for k in sorted(scope))
    feature_key = f"{engine}:{experiment}:{scope_key}"[:500]
    return {
        "engine": engine,
        "experiment": experiment,
        "feature_key": feature_key,
        "scope": scope,
        "stage": stage,
        "authority": "RESEARCH_ONLY",
        "metrics": summary,
        "meta": meta or {},
        "research_version": VERSION,
        "updated_at": utc_now(),
    }


def top_findings(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def score(item):
        metrics = ((item.get('metrics') or {}).get('validation') or {})
        exp = metrics.get('expectancy_r')
        n = metrics.get('resolved') or 0
        try: exp = float(exp)
        except Exception: exp = -999.0
        return (exp, int(n))
    positives = sorted(items, key=score, reverse=True)
    degraded = sorted(items, key=score)
    merged = []
    seen = set()
    for item in positives[:FINDING_LIMIT//2] + degraded[:FINDING_LIMIT//3] + items[:FINDING_LIMIT//4]:
        key = item.get('feature_key')
        if key not in seen:
            seen.add(key); merged.append(item)
        if len(merged) >= FINDING_LIMIT:
            break
    return merged
