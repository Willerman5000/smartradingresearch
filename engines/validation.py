from __future__ import annotations
from typing import Any, Dict, List, Tuple
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


def _causal_thresholds(scope: Dict[str, Any]) -> Tuple[int, int, float, float]:
    """Discovery/OOS thresholds scale with timeframe instead of one N for all."""
    tf=str((scope or {}).get("timeframe") or "").upper()
    table={
        "5M":(60,20,0.10,1.15), "15M":(50,15,0.10,1.15), "30M":(40,12,0.10,1.15),
        "1H":(30,10,0.10,1.15), "2H":(25,8,0.10,1.15), "4H":(20,6,0.10,1.15),
        "12H":(15,5,0.08,1.12), "1D":(12,4,0.08,1.12), "1W":(8,3,0.05,1.10),
    }
    return table.get(tf,(30,10,0.10,1.15))


def _stability(metrics: Dict[str, Any]) -> tuple[int, float | None]:
    wf = (metrics or {}).get("walk_forward") or {}
    return int(wf.get("valid_folds") or 0), num(wf.get("positive_fold_ratio"))


def _causal_cross_asset_ok(row: Dict[str, Any], metrics: Dict[str, Any], scope: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """I.2 validates each Futures symbol×TF cell on its own evidence.

    Cross-asset robustness is useful for a generic pooled strategy, but the user
    explicitly requires a specialist for BTC, ETH, SOL, XRP, ADA, LINK and BNB
    independently. Requiring three symbols inside a single-symbol replay would
    make every valid specialist impossible by construction.
    """
    fam=str(scope.get("market_family") or "")
    symbol=str(scope.get("symbol") or "ALL").upper()
    if fam != "CRYPTO_FUTURES" or symbol not in {"", "ALL"}:
        return True, {"required":False,"mode":"SYMBOL_SPECIALIST","symbol":symbol}
    val=(metrics.get("validation") or {})
    by_symbol=val.get("by_symbol") or {}
    tested=len(by_symbol)
    positive=[k for k,v in by_symbol.items() if num((v or {}).get("expectancy_r"),-999) > 0]
    needed=max(2, min(4, (tested + 1)//2))
    ok=tested>=3 and len(positive)>=needed
    return ok, {"required":True,"mode":"POOLED_FUTURES","symbols_tested_oos":tested,"positive_symbols_oos":len(positive),"positive_names_oos":positive,"minimum_positive":needed}


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
        runtime_trackable = bool(contract.get("runtime_trackable", row_meta.get("runtime_trackable", False)))
        shadow_target=canary_target=None
        experiment = str(row.get("experiment") or "")
        is_ai_proposal = experiment == "AI_STRATEGY_PROPOSAL"
        is_factory_strategy = experiment == "FACTORY_STRATEGY" or bool(row_meta.get("factory_strategy"))
        is_causal = experiment == "CAUSAL_COVERAGE_STRATEGY" or bool(row_meta.get("causal_candle_replay"))
        causal_finalist_rank = int(row_meta.get("finalist_rank_selection_only") or 1)
        causal_oos_promotable = (not is_causal) or causal_finalist_rank == 1
        strict_generated = bool(is_ai_proposal or is_factory_strategy or is_causal)
        cross_ok, cross_meta = _causal_cross_asset_ok(row, metrics, scope) if is_causal else (True,{"required":False})

        # Exchange-flow current context can inform future research but never enter
        # promotion without historical timestamps/replay.
        if experiment == "EXCHANGE_FLOW_CONTEXT":
            stage="OBSERVE"
            reason="Contexto CEX actual; sin histórico causal no puede promover ni vetar producción."
        elif is_causal:
            min_n,min_vn,min_exp,min_pf=_causal_thresholds(scope)
            if vn>=min_vn and vexp is not None and vexp<=-0.15:
                stage="REJECTED_OOS"
                reason="Replay causal: OOS final negativo; puede degradar/vetar la hipótesis, no promover."
            elif n>=min_n and vn>=min_vn and vexp is not None and vexp>min_exp and vpf is not None and vpf>min_pf and not pf_degenerate and stable and net_pct>=90 and runtime_trackable and cross_ok and causal_oos_promotable:
                strong = n >= int(min_n*1.8) and vn >= int(min_vn*1.5) and vexp > max(0.18,min_exp+0.05) and vpf > max(1.25,min_pf+0.08) and positive_ratio is not None and positive_ratio >= 0.80
                stage="SHADOW_READY_FAST" if strong else "SHADOW_READY"
                reason=("Replay causal rentable en Discovery/selección y OOS final, walk-forward estable, costes modelados y contrato runtime reproducible. "
                        "Aún requiere Shadow live antes de Canary.")
                shadow_target,canary_target=_targets(scope,strong)
            elif n>=max(6,min_n//3):
                stage="VALIDATION_REQUIRED"
                missing=[]
                if n<min_n: missing.append(f"Discovery {n}/{min_n}")
                if vn<min_vn: missing.append(f"OOS {vn}/{min_vn}")
                if not stable: missing.append("walk-forward")
                if net_pct<90: missing.append("costes")
                if not runtime_trackable: missing.append("paridad runtime")
                if not cross_ok: missing.append("robustez cross-asset")
                if pf_degenerate: missing.append("PF degenerado")
                if not causal_oos_promotable: missing.append("finalista backup; requiere nueva generación OOS")
                reason="Replay causal ejecutado; falta robustez: " + ", ".join(missing or ["expectancy/PF OOS"]) + "."
            else:
                stage="OBSERVE"
                reason="Replay causal con muestra todavía insuficiente."
        elif vn>=10 and vexp is not None and vexp<=-0.20:
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
            "timeframe_aware_historical_gate":bool(is_causal),
            "guarantees_future_profit":False,
            "runtime_trackable":runtime_trackable,
            "walk_forward_valid_folds":valid_folds,
            "walk_forward_positive_ratio":positive_ratio,
            "is_current":True,
            "ai_proposal": is_ai_proposal,
            "factory_strategy": is_factory_strategy,
            "causal_strategy": is_causal,
            "causal_cross_asset": cross_meta,
            "generated_strategy_requires_strict_evidence": strict_generated,
            "ai_proposal_requires_strict_net_evidence": bool(is_ai_proposal),
            "symbol_timeframe_specialist": bool(is_causal and str(scope.get("symbol") or "ALL").upper() not in {"", "ALL"}),
            "finalist_rank_selection_only": row_meta.get("finalist_rank_selection_only"),
            "finalists_predeclared_for_oos": row_meta.get("finalists_predeclared_for_oos"),
            "oos_promotable_selection_winner": bool(causal_oos_promotable),
            "final_oos_locked": bool(row_meta.get("final_oos_locked", is_causal)),
            "oos_generation_rule": row_meta.get("oos_generation_rule"),
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
