from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import execution_features, market_family, scoped_symbol
from metrics import forensics
ENGINE="risk"

def _flag(fx,*names):
    for name in names:
        value=fx.get(name)
        if value is True:return "YES"
        if value is False:return "NO"
    return "UNKNOWN"

def analyze(rows: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    groups=defaultdict(list)
    for row in rows:
        st=str(row.get("system_type") or "").lower()
        if st not in {"futures","spot"}: continue
        f=execution_features(row); fx=forensics(row); fam=market_family(row); sym=scoped_symbol(row)
        base={"market_family":fam,"timeframe":f["timeframe"],"direction":f["direction"],"regime":f["regime"]}
        if sym!="ALL_CRYPTO": base["symbol"]=sym
        wick=_flag(fx,"wick_out","wick_out_observed","false_invalidation"); reclaimed=_flag(fx,"reclaimed_entry","post_sl_reclaimed_entry"); tp_after=_flag(fx,"tp_reached_after_stop","would_hit_original_tp_after_sl")
        scopes=[("SL_QUALITY",{**base,"sl_quality":f["sl_quality_band"]}),("SL_MICRO",{**base,"micro_alignment":f["micro_alignment"],"sl_quality":f["sl_quality_band"]}),("WICK_OUT",{**base,"wick_out":wick,"entry_source":f["entry_source"]}),("RECLAIM_AFTER_SL",{**base,"reclaimed":reclaimed,"tp_after_sl":tp_after})]
        for exp,scope in scopes: groups[(exp,tuple(sorted(scope.items())))].append(row)
    return top_findings([finding(ENGINE,e,dict(s),g) for (e,s),g in groups.items() if len(g)>=5])
