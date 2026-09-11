from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import trader_items
from metrics import realized_r, regime, summarize_oos

ENGINE = "traders"


def analyze(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups = defaultdict(list)
    judged_values = defaultdict(list)
    for row in rows:
        if str(row.get('system_type') or '').lower() != 'futures':
            continue
        direction = str(row.get('action_normalized') or 'UNKNOWN').upper()
        tf = str(row.get('timeframe') or 'UNKNOWN').upper()
        rg = regime(row)
        r, _ = realized_r(row)
        for item in trader_items(row)[:40]:
            trader = str(item.get('trader') or 'UNKNOWN').strip()[:80]
            relation = str(item.get('relation_to_final') or 'UNKNOWN').upper()
            strategy = str(item.get('strategy') or '').upper()[:140]
            for dim, scope in [
                ('MARKET', {'trader':trader,'direction':direction,'relation':relation}),
                ('TIMEFRAME', {'trader':trader,'direction':direction,'timeframe':tf,'relation':relation}),
                ('REGIME', {'trader':trader,'direction':direction,'timeframe':tf,'regime':rg,'relation':relation}),
            ]:
                key=(dim, tuple(sorted(scope.items())))
                groups[key].append(row)
                if r is not None:
                    judged = r if relation == 'SUPPORT' else (-r if relation == 'OPPOSE' else None)
                    if judged is not None:
                        judged_values[key].append(judged)
            if strategy:
                scope={'trader':trader,'direction':direction,'regime':rg,'relation':relation,'strategy':strategy}
                key=('STRATEGY_JUDGEMENT', tuple(sorted(scope.items())))
                groups[key].append(row)
                if r is not None:
                    judged = r if relation == 'SUPPORT' else (-r if relation == 'OPPOSE' else None)
                    if judged is not None:
                        judged_values[key].append(judged)
    findings=[]
    for (dim, scope_tuple), grows in groups.items():
        if len(grows) < 5:
            continue
        item = finding(ENGINE, dim, dict(scope_tuple), grows)
        vals = judged_values.get((dim, scope_tuple)) or []
        item['meta'] = {
            'judgement_expectancy_r': round(sum(vals)/len(vals),5) if vals else None,
            'judgement_n': len(vals),
            'note': 'SUPPORT usa R real; OPPOSE invierte R para medir calidad de veto.'
        }
        findings.append(item)
    return top_findings(findings)
