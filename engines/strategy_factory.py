from __future__ import annotations

"""Continuous Strategy Factory (Research-only).

Builds a bounded set of interpretable strategy hypotheses from features that
already exist in persisted SmartradingReview signals.  It never changes
production rules, Entry/SL/TP, Safety or leverage.  New combinations are
re-created each research cycle as fresh data arrives and Validation remains
responsible for rejecting/promoting evidence.
"""

import hashlib
import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

import config
from engines.base import finding
from features import execution_features, market_family, scoped_symbol
from metrics import num

EXPERIMENT = "FACTORY_STRATEGY"


def _clean_scope(scope: Dict[str, Any]) -> Dict[str, str]:
    return {
        str(k): str(v)[:180]
        for k, v in (scope or {}).items()
        if v is not None and str(v).strip() and str(v).upper() not in {"NO_DATA", "UNKNOWN", "UNAVAILABLE"}
    }


def _strategy_id(family: str, variant: str, scope: Dict[str, Any]) -> str:
    raw = json.dumps(
        {"family": family, "variant": variant, "scope": _clean_scope(scope)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "SF_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:18]


def _quality_scope(base: Dict[str, Any], f: Dict[str, Any]) -> Dict[str, Any]:
    """Cross-cutting Entry/SL/TP quality without changing any level."""
    out = dict(base)
    for key in (
        "defensibility_band",
        "reachability_band",
        "sl_quality_band",
        "tp_quality_band",
    ):
        value = str(f.get(key) or "NO_DATA").upper()
        if value in {"MEDIUM", "HIGH", "VERY_HIGH"}:
            out[key] = value
    return out


def _row_specs(row: Dict[str, Any]) -> Iterable[Tuple[str, str, Dict[str, Any], str]]:
    st = str(row.get("system_type") or "").lower()
    if st not in {"futures", "spot"}:
        return []

    f = execution_features(row)
    direction = str(f.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return []

    regime = str(f.get("regime") or "UNKNOWN").upper()
    tf = str(f.get("timeframe") or "UNKNOWN").upper()
    fam = market_family(row)
    sym = scoped_symbol(row)

    base: Dict[str, Any] = {
        "market_family": fam,
        "timeframe": tf,
        "direction": direction,
        "regime": regime,
    }
    if sym != "ALL_CRYPTO":
        base["symbol"] = sym

    specs: List[Tuple[str, str, Dict[str, Any], str]] = []

    # 1) Tendencia: sólo continuidad coherente con el régimen.
    trend_aligned = (
        (regime == "TREND_UP" and direction == "LONG")
        or (regime == "TREND_DOWN" and direction == "SHORT")
    )
    if trend_aligned:
        coarse = dict(base)
        if str(f.get("has_pullback")) == "YES":
            coarse["has_pullback"] = "YES"
        specs.append((
            "TREND_CONTINUATION", "BASE", coarse,
            "Continuación en tendencia; busca entrar con el régimen, no contra él.",
        ))
        quality = _quality_scope(coarse, f)
        if quality != coarse:
            specs.append((
                "TREND_CONTINUATION", "QUALITY", quality,
                "Continuación con filtro de calidad de Entry/SL/TP.",
            ))

    # 2) Reversión: sólo mercado equilibrado. La dirección se conserva como
    # hipótesis observada; no se deduce de RSI por texto libre.
    if regime == "BALANCE":
        mean_scope = dict(base)
        micro = str(f.get("micro_alignment") or "UNAVAILABLE").upper()
        if micro in {"ALIGNED", "NEUTRAL", "CONFLICT"}:
            mean_scope["micro_alignment"] = micro
        specs.append((
            "MEAN_REVERSION", "BASE", mean_scope,
            "Reversión/retorno al valor únicamente en mercado equilibrado.",
        ))
        quality = _quality_scope(mean_scope, f)
        if quality != mean_scope:
            specs.append((
                "MEAN_REVERSION", "QUALITY", quality,
                "Reversión en balance con geometría de ejecución más exigente.",
            ))

    # 3) Breakout + retest: reutiliza evidencia observable (sweep/pullback).
    # No inventa una ruptura si el snapshot no la registró.
    has_pullback = str(f.get("has_pullback") or "NO") == "YES"
    has_sweep = str(f.get("has_sweep") or "NO") == "YES"
    if has_pullback or has_sweep:
        breakout_scope = dict(base)
        if has_pullback:
            breakout_scope["has_pullback"] = "YES"
        if has_sweep:
            breakout_scope["has_sweep"] = "YES"
        specs.append((
            "BREAKOUT_RETEST", "STRUCTURE", breakout_scope,
            "Ruptura/retest inferida sólo desde evidencias estructurales persistidas.",
        ))

    # 4) SMC/Liquidez: combina las piezas del orden definido por el sistema.
    has_ob = str(f.get("has_order_block") or "NO") == "YES"
    if has_ob or has_sweep:
        smc_scope = dict(base)
        if has_ob:
            smc_scope["has_order_block"] = "YES"
        if has_sweep:
            smc_scope["has_sweep"] = "YES"
        if has_pullback:
            smc_scope["has_pullback"] = "YES"
        specs.append((
            "SMC_LIQUIDITY", "STRUCTURE", smc_scope,
            "Liquidity/Sweep/POI como familia; un Order Block aislado no equivale a edge.",
        ))
        micro = str(f.get("micro_alignment") or "UNAVAILABLE").upper()
        if micro in {"ALIGNED", "NEUTRAL", "CONFLICT"}:
            smc_micro = dict(smc_scope)
            smc_micro["micro_alignment"] = micro
            specs.append((
                "SMC_LIQUIDITY", "MICRO", smc_micro,
                "SMC con confirmación/contradicción de microestructura observada.",
            ))

    # 5) Posicionamiento Futures: OI + funding + basis + libro + liquidez.
    # Se generan versiones coarse/fine para evitar fragmentar demasiado la muestra.
    if st == "futures":
        oi = str(f.get("oi_change_band") or "NO_DATA").upper()
        funding = str(f.get("funding_band") or "NO_DATA").upper()
        basis = str(f.get("basis_band") or "NO_DATA").upper()
        book = str(f.get("orderbook_imbalance_band") or "NO_DATA").upper()
        liquidity = str(f.get("liquidity_band") or "NO_DATA").upper()

        positioning_scope = dict(base)
        if oi != "NO_DATA":
            positioning_scope["oi_change_band"] = oi
        if funding != "NO_DATA":
            positioning_scope["funding_band"] = funding
        if len(positioning_scope) > len(base):
            specs.append((
                "DERIVATIVES_POSITIONING", "POSITIONING", positioning_scope,
                "Posicionamiento por OI/funding; OI aislado no define dirección.",
            ))

        market_scope = dict(base)
        for key, value in (
            ("basis_band", basis),
            ("orderbook_imbalance_band", book),
            ("liquidity_band", liquidity),
        ):
            if value != "NO_DATA":
                market_scope[key] = value
        if len(market_scope) > len(base):
            specs.append((
                "DERIVATIVES_POSITIONING", "MICRO_MARKET", market_scope,
                "Basis/libro/liquidez como contexto de derivados, no como señal única.",
            ))

        combined = dict(positioning_scope)
        for key, value in (
            ("basis_band", basis),
            ("orderbook_imbalance_band", book),
            ("liquidity_band", liquidity),
        ):
            if value != "NO_DATA":
                combined[key] = value
        # Sólo crea la variante fina cuando aporta al menos tres dimensiones de
        # derivados; esto evita fabricar combinaciones triviales.
        derivative_dims = [
            k for k in combined
            if k in {"oi_change_band", "funding_band", "basis_band", "orderbook_imbalance_band", "liquidity_band"}
        ]
        if len(derivative_dims) >= 3:
            specs.append((
                "DERIVATIVES_POSITIONING", "CONFLUENCE", combined,
                "Confluencia de posicionamiento y microestructura para estudiar crowding/continuación/reversión.",
            ))

    return specs


def _candidate_score(item: Dict[str, Any]) -> float:
    metrics = item.get("metrics") or {}
    allm = metrics.get("all") or {}
    val = metrics.get("validation") or {}
    n = float(allm.get("resolved") or 0)
    vn = float(val.get("resolved") or 0)
    all_exp = num(allm.get("expectancy_r"), -2.0) or -2.0
    val_exp = num(val.get("expectancy_r"))
    # Holdout pesa más cuando existe; sample weighting prevents N=1 from
    # outranking an actually tested candidate.
    holdout_component = (val_exp if val_exp is not None else -0.15) * min(1.0, vn / 10.0)
    discovery_component = all_exp * min(1.0, n / 25.0) * 0.35
    return holdout_component + discovery_component + min(0.15, n / 1000.0)


def analyze_factory(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str, Tuple[Tuple[str, str], ...], str], List[Dict[str, Any]]] = defaultdict(list)

    for row in rows or []:
        for family, variant, raw_scope, thesis in _row_specs(row):
            scope = _clean_scope(raw_scope)
            # Need real discrimination beyond market family/direction/timeframe.
            if len(scope) < 4:
                continue
            key = (family, variant, tuple(sorted(scope.items())), thesis)
            groups[key].append(row)

    candidates: List[Dict[str, Any]] = []
    min_rows = int(getattr(config, "FACTORY_MIN_SOURCE_ROWS", 6))
    for (family, variant, scope_items, thesis), grows in groups.items():
        if len(grows) < min_rows:
            continue
        scope = dict(scope_items)
        strategy_id = _strategy_id(family, variant, scope)
        item = finding(
            "strategy",
            EXPERIMENT,
            scope,
            grows,
            meta={
                "factory_strategy": True,
                "factory_strategy_id": strategy_id,
                "factory_family": family,
                "factory_variant": variant,
                "factory_generation": config.VERSION,
                "factory_thesis": thesis,
                "continuous_generation": True,
                "selection_goal": "NET_EXPECTANCY_PF_DRAWDOWN_WITH_WR_SECONDARY",
                "production_changes_allowed": False,
                "changes_entry_sl_tp": False,
                "changes_safety": False,
                "changes_leverage": False,
                "note": (
                    "Hipótesis generada de forma determinista desde features persistidas. "
                    "Debe sobrevivir Holdout/walk-forward/costes/Shadow antes de cualquier autoridad."
                ),
            },
        )
        candidates.append(item)

    candidates.sort(
        key=lambda item: (
            _candidate_score(item),
            int(((item.get("metrics") or {}).get("all") or {}).get("resolved") or 0),
        ),
        reverse=True,
    )
    limit = int(getattr(config, "FACTORY_MAX_CANDIDATES", 40))
    return candidates[: max(5, limit)]
