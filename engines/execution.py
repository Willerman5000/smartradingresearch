from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import execution_features, market_family, scoped_symbol

ENGINE = "execution"

def analyze(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups=defaultdict(list)
    for row in rows:
        st=str(row.get("system_type") or "").lower()
        if st not in {"futures","spot"}: continue
        f=execution_features(row); fam=market_family(row); sym=scoped_symbol(row)
        common={"market_family":fam,"timeframe":f["timeframe"],"direction":f["direction"],"regime":f["regime"]}
        keys=[
            ("REGIME_DIRECTION_TF",dict(common)),
            ("ENTRY_SOURCE",{**common,"entry_source":f["entry_source"]}),
            ("OB_MICRO",{**common,"has_order_block":f["has_order_block"],"micro_alignment":f["micro_alignment"]}),
            ("SWEEP_MICRO",{**common,"has_sweep":f["has_sweep"],"micro_alignment":f["micro_alignment"]}),
            ("PULLBACK_MICRO",{**common,"has_pullback":f["has_pullback"],"micro_alignment":f["micro_alignment"]}),
            ("ENTRY_QUALITY",{**common,"defensibility_band":f["defensibility_band"],"reachability_band":f["reachability_band"]}),
        ]
        if st=="futures":
            keys.append(("ORDERFLOW",{**common,"orderbook_imbalance_band":f["orderbook_imbalance_band"],"recent_buy_share_band":f["recent_buy_share_band"]}))
            # Comprueba si el edge general falla en una moneda concreta sin fragmentar todo.
            keys.append(("SYMBOL_CHECK",{**common,"symbol":str(row.get("symbol") or "UNKNOWN").upper().replace("/","-")}))
        elif sym!="ALL_CRYPTO":
            keys=[(name,{**scope,"symbol":sym}) for name,scope in keys]
        for experiment,scope in keys: groups[(experiment,tuple(sorted(scope.items())))].append(row)
    findings=[]
    for (experiment,scope_tuple),grows in groups.items():
        if len(grows)>=5: findings.append(finding(ENGINE,experiment,dict(scope_tuple),grows))
    return top_findings(findings)
