from __future__ import annotations

"""RC4 research-only full-stack profitability certificate.

This module deliberately does not create a strategy and does not rank candidates.
It joins the evidence already produced for one market×symbol×timeframe finalist so
operators can see where edge is preserved or destroyed: strategy -> entry ->
costs -> Guardian -> runtime committee/live parity.
"""
from typing import Any, Dict

VERSION = "FINAL_V1_FULL_STACK_CERT_V2"


def _num(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def build_full_stack_certification(*, system_type: str, symbol: str, timeframe: str,
                                   strategy_family: str, strategy_spec: Dict[str, Any],
                                   metrics: Dict[str, Any], execution_stats: Dict[str, Any]) -> Dict[str, Any]:
    final_oos = (metrics or {}).get("validation") or {}
    all_metrics = (metrics or {}).get("all") or {}
    # temporal_metrics names the untouched 20% split 'validation' in this codebase.
    resolved = int(final_oos.get("resolved") or 0)
    exp = _num(final_oos.get("expectancy_r"))
    pf = _num(final_oos.get("profit_factor"))
    signals = int((execution_stats or {}).get("signals") or 0)
    activated = int((execution_stats or {}).get("activated") or 0)
    activation_pct = round(activated / signals * 100.0, 2) if signals else None
    entry_audit = final_oos.get("entry_execution_audit") or all_metrics.get("entry_execution_audit") or {}
    guardian = final_oos.get("guardian_operational_replay") or all_metrics.get("guardian_operational_replay") or {}
    guardian_delta = _num(guardian.get("delta_expectancy_r_vs_original"))

    strategy_positive = bool(resolved > 0 and exp is not None and exp > 0 and (pf is None or pf > 1.0))
    entry_observed = activated > 0 and activation_pct is not None
    # Full production committee parity requires live/runtime attribution; historical
    # strategy replay must never fabricate the 10-trader decision.
    committee_state = "RUNTIME_ATTRIBUTION_REQUIRED"
    if strategy_positive and entry_observed:
        state = "HISTORICAL_BACKTEST_POSITIVE_PENDING_REAL_EXECUTION"
    elif strategy_positive:
        state = "HISTORICAL_EDGE_POSITIVE_ENTRY_EVIDENCE_INCOMPLETE"
    else:
        state = "NOT_CERTIFIED"

    return {
        "version": VERSION,
        "authority": "RESEARCH_ONLY",
        "selection_uses_certificate": False,
        "market": str(system_type).upper(),
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": strategy_family,
        "strategy_oos": {"n": resolved, "expectancy_r": exp, "profit_factor": pf, "positive": strategy_positive},
        "entry": {
            "style": (strategy_spec or {}).get("entry_style"),
            "entry_atr": (strategy_spec or {}).get("entry_atr"),
            "signals": signals, "activated": activated, "activation_pct": activation_pct,
            "audit": entry_audit,
        },
        "risk_geometry": {"sl_atr": (strategy_spec or {}).get("sl_atr"), "rr": (strategy_spec or {}).get("rr")},
        "economics": {"cost_model": final_oos.get("cost_model") or all_metrics.get("cost_model"), "costs_included": True},
        "guardian": {"delta_expectancy_r": guardian_delta, "replay": guardian},
        "committee": {"state": committee_state, "historical_direction_fabricated": False},
        "certification_state": state,
        "production_parity": False,
        "production_parity_reason": "Research uses causal/proxy geometry; Shadow/LIVE must confirm the real committee and production Entry/SL/TP.",
        "production_authority": False,
        "guarantees_future_profit": False,
    }
