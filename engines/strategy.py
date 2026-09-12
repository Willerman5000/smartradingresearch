from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import strategy_tokens, market_family, scoped_symbol
from metrics import regime, summarize_oos
ENGINE="strategy"

def analyze(rows: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    token_rows=defaultdict(list); baseline=defaultdict(list)
    for row in rows:
        st=str(row.get("system_type") or "").lower()
        if st not in {"futures","spot"}: continue
        fam=market_family(row); sym=scoped_symbol(row); direction=str(row.get("action_normalized") or "UNKNOWN").upper(); rg=regime(row); tf=str(row.get("timeframe") or "UNKNOWN").upper()
        ctx=(fam,sym,direction,rg,tf); baseline[ctx].append(row)
        for token in strategy_tokens(row): token_rows[(ctx,token)].append(row)
    findings=[]
    for (ctx,token),grows in token_rows.items():
        if len(grows)<5: continue
        fam,sym,direction,rg,tf=ctx; scope={"market_family":fam,"direction":direction,"regime":rg,"timeframe":tf,"component":token[:180]}
        if sym!="ALL_CRYPTO": scope["symbol"]=sym
        item=finding(ENGINE,"COMPONENT_ATTRIBUTION",scope,grows); comp=((item.get("metrics") or {}).get("all") or {}).get("expectancy_r"); base=((summarize_oos(baseline[ctx]).get("all") or {}).get("expectancy_r"))
        try: delta=float(comp)-float(base)
        except Exception: delta=None
        item["meta"]={"baseline_expectancy_r":base,"marginal_association_r":round(delta,5) if delta is not None else None,"note":"Asociación histórica; no prueba causal."}
        findings.append(item)
    return top_findings(findings)
