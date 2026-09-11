from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import strategy_tokens
from metrics import realized_r, regime, summarize_oos

ENGINE = "strategy"


def analyze(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    futures = [r for r in rows if str(r.get('system_type') or '').lower() == 'futures']
    token_rows = defaultdict(list)
    baseline_by_context = defaultdict(list)
    for row in futures:
        direction = str(row.get('action_normalized') or 'UNKNOWN').upper()
        rg = regime(row)
        tf = str(row.get('timeframe') or 'UNKNOWN').upper()
        ctx = (direction, rg, tf)
        baseline_by_context[ctx].append(row)
        for token in strategy_tokens(row):
            token_rows[(ctx, token)].append(row)
    findings=[]
    for (ctx, token), grows in token_rows.items():
        if len(grows) < 5:
            continue
        direction, rg, tf = ctx
        base = baseline_by_context[ctx]
        item = finding(ENGINE, 'COMPONENT_ATTRIBUTION', {
            'direction':direction, 'regime':rg, 'timeframe':tf, 'component':token[:180]
        }, grows)
        comp_exp = (((item.get('metrics') or {}).get('all') or {}).get('expectancy_r'))
        base_summary = summarize_oos(base)
        base_exp = ((base_summary.get('all') or {}).get('expectancy_r'))
        try:
            delta = float(comp_exp) - float(base_exp)
        except Exception:
            delta = None
        item['meta'] = {
            'baseline_expectancy_r': base_exp,
            'marginal_association_r': round(delta,5) if delta is not None else None,
            'note': 'Asociación histórica; no prueba causal ni modifica producción.'
        }
        findings.append(item)
    return top_findings(findings)
