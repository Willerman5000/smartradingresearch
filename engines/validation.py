from __future__ import annotations
from typing import Any, Dict, List
import config
from config import VERSION
from db import utc_now
from metrics import num
ENGINE="validation"


def _targets(scope, fast=False):
    tf=str((scope or {}).get("timeframe") or "").upper()
    normal={"5M":15,"15M":15,"30M":15,"1H":12,"2H":12,"4H":10,"12H":8,"1D":6,"1W":3}.get(tf,15)
    if fast:
        normal=max(2,round(normal*0.65))
    canary=max(2,round(normal*0.50))
    return int(normal),int(canary)


def _stability(metrics: Dict[str, Any]) -> tuple[int, float | None]:
    wf = (metrics or {}).get("walk_forward") or {}
    return int(wf.get("valid_folds") or 0), num(wf.get("positive_fold_ratio"))


def analyze_findings(rows: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    promotions=[]
    for row in rows:
        if str(row.get("engine") or "")==ENGINE:
            continue
        if str(row.get("research_version") or "") != VERSION:
            continue
        row_meta = row.get("meta") or {}
        if row_meta.get("is_current") is False:
            continue
        metrics=row.get("metrics") or {}
        allm=metrics.get("all") or {}
        val=metrics.get("validation") or {}
        scope=row.get("scope") or {}
        n=int(allm.get("resolved") or 0)
        vn=int(val.get("resolved") or 0)
        vexp=num(val.get("expectancy_r"))
        vpf=num(val.get("profit_factor"))
        pf_degenerate=bool(val.get("profit_factor_degenerate"))
        net_pct=num(allm.get("net_evidence_pct"),0.0) or 0.0
        symbols=allm.get("symbols") or []
        fam=str(scope.get("market_family") or "")
        valid_folds, positive_ratio = _stability(metrics)
        stable = valid_folds >= 2 and positive_ratio is not None and positive_ratio >= 0.67
        contract = row_meta.get("runtime_contract") or {}
        runtime_trackable = bool(contract.get("runtime_trackable", False))
        shadow_target=canary_target=None
        experiment = str(row.get("experiment") or "")
        is_ai_proposal = experiment == "AI_STRATEGY_PROPOSAL"
        is_factory_strategy = experiment == "FACTORY_STRATEGY" or bool(row_meta.get("factory_strategy"))
        strict_generated = bool(is_ai_proposal or is_factory_strategy)

        if vn>=10 and vexp is not None and vexp<=-0.20:
            stage="REJECTED_OOS"
            reason="Holdout temporal negativo; puede usarse para proteger/vetar, no para promover."
        elif (not strict_generated) and n>=60 and vn>=20 and vexp is not None and vexp>0.20 and vpf is not None and vpf>1.25 and not pf_degenerate and stable and (len(symbols)>=3 or fam in {"PAXG_USDT","PAXG_BTC"}) and net_pct>=max(90, config.MIN_NET_EVIDENCE_PCT) and runtime_trackable:
            stage="SHADOW_READY_FAST"
            reason="Evidencia temporal fuerte, estable, neta y reproducible en runtime; requiere Shadow live antes de Canary."
            shadow_target,canary_target=_targets(scope,True)
        elif n >= (30 if strict_generated else 25) and vn>=10 and vexp is not None and vexp>(0.10 if strict_generated else 0.05) and vpf is not None and vpf>(1.15 if strict_generated else 1.10) and not pf_degenerate and stable and net_pct >= (max(90, config.MIN_NET_EVIDENCE_PCT) if strict_generated else config.MIN_NET_EVIDENCE_PCT):
            if not runtime_trackable:
                stage="VALIDATION_REQUIRED"
                reason="Edge temporal positivo, pero el runtime central no puede reproducir todos los campos del candidato."
            elif len(symbols)>=2 or fam in {"PAXG_USDT","PAXG_BTC"}:
                stage="SHADOW_READY"
                reason="Edge temporal positivo, estable y con cobertura económica; requiere confirmación live proporcional al timeframe."
                shadow_target,canary_target=_targets(scope,False)
            else:
                stage="VALIDATED_SINGLE_ASSET"
                reason="Edge temporal positivo; falta generalización o confirmación live suficiente."
        elif n>=10:
            stage="VALIDATION_REQUIRED"
            missing=[]
            if vn < 10: missing.append("holdout")
            if not stable: missing.append("walk-forward")
            if net_pct < config.MIN_NET_EVIDENCE_PCT: missing.append("costes")
            if pf_degenerate: missing.append("PF degenerado")
            if not runtime_trackable: missing.append("paridad runtime")
            reason="Hay muestra de descubrimiento; falta evidencia robusta: " + ", ".join(missing or ["más validación"]) + "."
        else:
            stage="OBSERVE"
            reason="Muestra insuficiente."

        final_meta=dict(row_meta)
        final_meta.update({
            "net_evidence_pct":net_pct,
            'production_changes_allowed': False,
            "recommended_shadow_target":shadow_target,
            "recommended_canary_target":canary_target,
            "timeframe_aware_shadow":True,
            "guarantees_future_profit":False,
            "runtime_trackable":runtime_trackable,
            "walk_forward_valid_folds":valid_folds,
            "walk_forward_positive_ratio":positive_ratio,
            "is_current":True,
            "ai_proposal": is_ai_proposal,
            "factory_strategy": is_factory_strategy,
            "generated_strategy_requires_strict_evidence": strict_generated,
            "ai_proposal_requires_strict_net_evidence": bool(is_ai_proposal),
        })
        promotions.append({
            "candidate_key":str(row.get("feature_key") or "")[:500],
            "source_engine":str(row.get("engine") or "")[:40],
            "experiment":str(row.get("experiment") or "")[:100],
            "stage":stage,
            'authority': 'RESEARCH_ONLY',
            "reason":reason,
            "metrics":metrics,
            "scope":scope,
            "research_version":VERSION,
            "updated_at":utc_now(),
            "meta":final_meta,
        })
    return promotions
