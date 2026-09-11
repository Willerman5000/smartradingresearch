from __future__ import annotations
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from config import VERSION
from db import utc_now
from metrics import num

ENGINE = "validation"


def analyze_findings(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Governance over research evidence. Never grants production authority."""
    promotions=[]
    for row in rows:
        if str(row.get('engine') or '') == ENGINE:
            continue
        metrics = row.get('metrics') or {}
        allm = metrics.get('all') or {}
        val = metrics.get('validation') or {}
        n = int(allm.get('resolved') or 0)
        vn = int(val.get('resolved') or 0)
        vexp = num(val.get('expectancy_r'))
        vpf = num(val.get('profit_factor'))
        net_pct = num(allm.get('net_evidence_pct'), 0.0) or 0.0
        symbols = allm.get('symbols') or []
        shadow_target = None
        canary_target = None
        if vn >= 10 and vexp is not None and vexp <= -0.20:
            stage='REJECTED_OOS'; reason='Validación temporal negativa.'
        elif n >= 60 and vn >= 20 and vexp is not None and vexp > 0.20 and (vpf is None or vpf > 1.25) and len(symbols) >= 3 and net_pct >= 50:
            stage='SHADOW_READY_FAST'; reason='Evidencia OOS fuerte, multi-activo y con cobertura económica suficiente; Shadow corto antes de Canary.'
            shadow_target=10; canary_target=5
        elif n >= 25 and vn >= 10 and vexp is not None and vexp > 0.05 and (vpf is None or vpf > 1.10):
            if len(symbols) >= 2:
                stage='SHADOW_READY'; reason='Edge positivo OOS y evidencia multi-activo; requiere confirmación live.'
                shadow_target=15; canary_target=8
            else:
                stage='VALIDATED_SINGLE_ASSET'; reason='Edge OOS positivo, falta generalización multi-activo.'
        elif n >= 10:
            stage='VALIDATION_REQUIRED'; reason='Hay muestra de descubrimiento, falta OOS suficiente.'
        else:
            stage='OBSERVE'; reason='Muestra insuficiente.'
        promotions.append({
            'candidate_key': str(row.get('feature_key') or '')[:500],
            'source_engine': str(row.get('engine') or '')[:40],
            'experiment': str(row.get('experiment') or '')[:100],
            'stage': stage,
            'authority': 'RESEARCH_ONLY',
            'reason': reason,
            'metrics': metrics,
            'scope': row.get('scope') or {},
            'research_version': VERSION,
            'updated_at': utc_now(),
            'meta': {
                'net_evidence_pct': net_pct,
                'production_changes_allowed': False,
                'recommended_shadow_target': shadow_target,
                'recommended_canary_target': canary_target,
                'guarantees_future_profit': False,
            },
        })
    return promotions
