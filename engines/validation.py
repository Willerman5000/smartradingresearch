from __future__ import annotations
from typing import Any, Dict, List
from config import VERSION
from db import utc_now
from metrics import num
ENGINE="validation"

def _targets(scope,fast=False):
    tf=str((scope or {}).get("timeframe") or "").upper(); fam=str((scope or {}).get("market_family") or "")
    normal={"5M":15,"15M":15,"30M":15,"1H":12,"2H":12,"4H":10,"12H":8,"1D":6,"1W":3}.get(tf,15)
    if fast: normal=max(2,round(normal*0.65))
    canary=max(2,round(normal*0.50))
    return int(normal),int(canary)

def analyze_findings(rows: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    promotions=[]
    for row in rows:
        if str(row.get("engine") or "")==ENGINE: continue
        metrics=row.get("metrics") or {}; allm=metrics.get("all") or {}; val=metrics.get("validation") or {}; scope=row.get("scope") or {}
        n=int(allm.get("resolved") or 0); vn=int(val.get("resolved") or 0); vexp=num(val.get("expectancy_r")); vpf=num(val.get("profit_factor")); net_pct=num(allm.get("net_evidence_pct"),0.0) or 0.0; symbols=allm.get("symbols") or []; fam=str(scope.get("market_family") or "")
        shadow_target=canary_target=None
        if vn>=10 and vexp is not None and vexp<=-0.20:
            stage="REJECTED_OOS"; reason="Validación temporal negativa."
        elif n>=60 and vn>=20 and vexp is not None and vexp>0.20 and (vpf is None or vpf>1.25) and (len(symbols)>=3 or fam in {"PAXG_USDT","PAXG_BTC"}) and net_pct>=50:
            stage="SHADOW_READY_FAST"; reason="Evidencia OOS fuerte; Shadow live corto y adaptado a temporalidad antes de Canary."; shadow_target,canary_target=_targets(scope,True)
        elif n>=25 and vn>=10 and vexp is not None and vexp>0.05 and (vpf is None or vpf>1.10):
            if len(symbols)>=2 or fam in {"PAXG_USDT","PAXG_BTC"}:
                stage="SHADOW_READY"; reason="Edge positivo OOS; requiere confirmación live proporcional a la temporalidad."; shadow_target,canary_target=_targets(scope,False)
            else:
                stage="VALIDATED_SINGLE_ASSET"; reason="Edge OOS positivo; falta generalización o confirmación live suficiente."
        elif n>=10:
            stage="VALIDATION_REQUIRED"; reason="Hay muestra de descubrimiento, falta OOS suficiente."
        else:
            stage="OBSERVE"; reason="Muestra insuficiente."
        promotions.append({"candidate_key":str(row.get("feature_key") or "")[:500],"source_engine":str(row.get("engine") or "")[:40],"experiment":str(row.get("experiment") or "")[:100],"stage":stage,'authority': 'RESEARCH_ONLY',"reason":reason,"metrics":metrics,"scope":scope,"research_version":VERSION,"updated_at":utc_now(),"meta":{"net_evidence_pct":net_pct,'production_changes_allowed': False,"recommended_shadow_target":shadow_target,"recommended_canary_target":canary_target,"timeframe_aware_shadow":True,'guarantees_future_profit': False}})
    return promotions
