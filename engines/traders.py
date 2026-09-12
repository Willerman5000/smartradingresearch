from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import trader_items, market_family, scoped_symbol
from metrics import realized_r, regime
ENGINE="traders"

def analyze(rows: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    groups=defaultdict(list); judged=defaultdict(list)
    for row in rows:
        st=str(row.get("system_type") or "").lower()
        if st not in {"futures","spot"}: continue
        fam=market_family(row); sym=scoped_symbol(row); direction=str(row.get("action_normalized") or "UNKNOWN").upper(); tf=str(row.get("timeframe") or "UNKNOWN").upper(); rg=regime(row); r,_=realized_r(row)
        for item in trader_items(row)[:40]:
            trader=str(item.get("trader") or "UNKNOWN")[:80]; relation=str(item.get("relation_to_final") or "UNKNOWN").upper(); strategy=str(item.get("strategy") or "").upper()[:140]
            base={"market_family":fam,"trader":trader,"direction":direction,"timeframe":tf,"relation":relation};
            if sym!="ALL_CRYPTO": base["symbol"]=sym
            scopes=[("TIMEFRAME",dict(base)),("REGIME",{**base,"regime":rg})]
            if strategy: scopes.append(("STRATEGY_JUDGEMENT",{**base,"regime":rg,"strategy":strategy}))
            for dim,scope in scopes:
                key=(dim,tuple(sorted(scope.items()))); groups[key].append(row)
                if r is not None:
                    value=r if relation=="SUPPORT" else (-r if relation=="OPPOSE" else None)
                    if value is not None: judged[key].append(value)
    findings=[]
    for (dim,scope_tuple),grows in groups.items():
        if len(grows)<5: continue
        item=finding(ENGINE,dim,dict(scope_tuple),grows); vals=judged.get((dim,scope_tuple)) or []; item["meta"]={"judgement_expectancy_r":round(sum(vals)/len(vals),5) if vals else None,"judgement_n":len(vals),"note":"SUPPORT usa R real; OPPOSE invierte R."}; findings.append(item)
    return top_findings(findings)
