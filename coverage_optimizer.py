from __future__ import annotations

"""Commit I — Causal Profitability Coverage Accelerator.

Goal: actively research every market×timeframe cell instead of letting one hot
30m cluster monopolize Strategy Factory. Results remain RESEARCH_ONLY.
"""

import hashlib
import json
import time
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import config
from causal_backtest import StrategySpec, replay_series, selection_score, temporal_metrics
from db import utc_now
from engines.base import runtime_contract, rss_mb
from historical_market import fetch_market, cache_stats

EXPERIMENT = "CAUSAL_COVERAGE_STRATEGY"
FUTURES_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT", "LINK-USDT", "BNB-USDT")
SPOT_SYMBOLS = ("BTC-USDT", "PAXG-USDT", "PAXG-BTC")

# Distributed ownership uses all four analytical Render services without
# duplicating the same historical cell. Validation remains aggregation-only.
LANES = {
    "execution": [("futures", "CRYPTO_FUTURES", "ALL", "5M"), ("futures", "CRYPTO_FUTURES", "ALL", "15M")],
    "risk": [("futures", "CRYPTO_FUTURES", "ALL", "30M"), ("futures", "CRYPTO_FUTURES", "ALL", "1H")],
    "strategy": [
        ("futures", "CRYPTO_FUTURES", "ALL", "2H"), ("futures", "CRYPTO_FUTURES", "ALL", "4H"),
        *[("spot", "CRYPTO_SPOT", "BTC-USDT", tf) for tf in ("4H", "12H", "1D", "1W")],
    ],
    "traders": [
        *[("spot", "PAXG_USDT", "PAXG-USDT", tf) for tf in ("4H", "12H", "1D", "1W")],
        *[("spot", "PAXG_BTC", "PAXG-BTC", tf) for tf in ("4H", "12H", "1D", "1W")],
    ],
}


def _bars_for(tf: str) -> int:
    defaults = {"5M": 9000, "15M": 8000, "30M": 7000, "1H": 6500, "2H": 5000, "4H": 4000, "12H": 2600, "1D": 1800, "1W": 600}
    hard = int(getattr(config, "CAUSAL_MAX_BARS", 9000))
    return min(hard, defaults.get(tf, 4000))


def _hold_bars(tf: str) -> int:
    return {"5M": 36, "15M": 28, "30M": 24, "1H": 18, "2H": 14, "4H": 10, "12H": 8, "1D": 6, "1W": 4}.get(tf, 18)


def _wait_bars(tf: str) -> int:
    return {"5M": 4, "15M": 4, "30M": 3, "1H": 3, "2H": 3, "4H": 2, "12H": 2, "1D": 2, "1W": 1}.get(tf, 3)


def _coarse_specs(tf: str) -> List[StrategySpec]:
    h = _hold_bars(tf); w = _wait_bars(tf)
    specs: List[StrategySpec] = []
    # Coarse -> fine. Keep rules interpretable and bounded.
    for direction in ("LONG", "SHORT"):
        for fast, slow in ((9, 21), (12, 36), (21, 50)):
            specs.append(StrategySpec("TREND_CONTINUATION", direction, fast=fast, slow=slow, sl_atr=1.5, rr=2.0, max_wait=w, max_hold=h))
        for fast, slow in ((9, 21), (12, 36)):
            specs.append(StrategySpec("TREND_PULLBACK", direction, fast=fast, slow=slow, entry_style="PULLBACK", entry_atr=0.25, sl_atr=1.5, rr=2.0, max_wait=w, max_hold=h))
        for lb in (12, 20, 30):
            specs.append(StrategySpec("BREAKOUT_RETEST", direction, fast=12, slow=36, lookback=lb, volume_mult=1.0, sl_atr=1.5, rr=2.0, max_wait=w, max_hold=h))
        for low, high in ((28, 72), (32, 68), (35, 65)):
            specs.append(StrategySpec("MEAN_REVERSION", direction, fast=12, slow=36, rsi_low=low, rsi_high=high, sl_atr=1.25, rr=1.75, max_wait=w, max_hold=h))
        for lb in (10, 20):
            specs.append(StrategySpec("SWEEP_REVERSAL", direction, fast=12, slow=36, lookback=lb, sl_atr=1.25, rr=2.0, max_wait=w, max_hold=h))
    return specs


def _refine(seed: StrategySpec) -> Iterable[StrategySpec]:
    for entry_style, entry_atr in (("NEXT_OPEN", 0.0), ("PULLBACK", 0.20), ("PULLBACK", 0.35)):
        for sl in (1.0, 1.35, 1.7, 2.0):
            for rr in (1.5, 2.0, 2.5, 3.0):
                yield StrategySpec(
                    seed.family, seed.direction_mode, fast=seed.fast, slow=seed.slow,
                    rsi_len=seed.rsi_len, rsi_low=seed.rsi_low, rsi_high=seed.rsi_high,
                    lookback=seed.lookback, volume_mult=seed.volume_mult,
                    entry_style=entry_style, entry_atr=entry_atr,
                    sl_atr=sl, rr=rr, max_wait=seed.max_wait, max_hold=seed.max_hold,
                )


def _strategy_id(cell: Tuple[str, str, str, str], spec: StrategySpec) -> str:
    raw = json.dumps({"cell": cell, "spec": spec.key()}, sort_keys=True, separators=(",", ":"))
    return "CI_" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _scope(cell: Tuple[str, str, str, str], spec: StrategySpec) -> Dict[str, str]:
    system_type, family, symbol, tf = cell
    scope = {"market_family": family, "timeframe": tf}
    if symbol != "ALL":
        scope["symbol"] = symbol
    # Runtime central can reproduce these coarse contextual gates exactly.
    if spec.direction_mode in {"LONG", "SHORT"}:
        scope["direction"] = spec.direction_mode
    if spec.family in {"TREND_CONTINUATION", "TREND_PULLBACK"}:
        scope["regime"] = "TREND_UP" if spec.direction_mode == "LONG" else ("TREND_DOWN" if spec.direction_mode == "SHORT" else "UNKNOWN")
    elif spec.family == "MEAN_REVERSION":
        scope["regime"] = "BALANCE"
    if spec.family in {"TREND_PULLBACK", "BREAKOUT_RETEST"}:
        scope["has_pullback"] = "YES"
    if spec.family == "SWEEP_REVERSAL":
        scope["has_sweep"] = "YES"
    # UNKNOWN is removed because it is not a runtime discriminator.
    return {k: v for k, v in scope.items() if v != "UNKNOWN"}


def _load_cell(cell: Tuple[str, str, str, str]) -> Dict[str, Sequence[Any]]:
    system_type, _family, symbol, tf = cell
    bars = _bars_for(tf)
    symbols = FUTURES_SYMBOLS if system_type == "futures" else (symbol,)
    data = {}
    for sym in symbols:
        try:
            candles = fetch_market(system_type, sym, tf, bars)
        except Exception as exc:
            print(f"⚠️ [I CAUSAL] {sym} {tf}: {str(exc)[:140]}", flush=True)
            candles = []
        if len(candles) >= 120:
            data[sym] = candles
        if rss_mb() >= getattr(config, "CAUSAL_MEMORY_TARGET_MB", 390):
            print(f"🧠 [I CAUSAL] memory target reached {rss_mb():.1f}MB; stop loading more symbols", flush=True)
            break
    return data


def _evaluate(data: Dict[str, Sequence[Any]], system_type: str, spec: StrategySpec) -> Tuple[Dict[str, Any], Dict[str, int]]:
    all_trades = []
    signals = activated = 0
    for symbol, candles in data.items():
        trades, stats = replay_series(symbol, system_type, candles, spec)
        all_trades.extend(trades)
        signals += int(stats.get("signals") or 0)
        activated += int(stats.get("activated") or 0)
    metrics = temporal_metrics(all_trades)
    return metrics, {"signals": signals, "activated": activated}


def _robustness(metrics: Dict[str, Any], system_type: str) -> Dict[str, Any]:
    allm = metrics.get("all") or {}
    by_symbol = allm.get("by_symbol") or {}
    positive = [s for s, x in by_symbol.items() if (x.get("expectancy_r") is not None and x.get("expectancy_r") > 0)]
    total = max(1, len(by_symbol))
    return {
        "symbols_tested": len(by_symbol),
        "positive_symbols": len(positive),
        "positive_symbol_ratio": round(len(positive) / total, 4),
        "positive_symbol_names": positive,
        "cross_asset_required": system_type == "futures",
    }


def _is_simulated_profitable(metrics: Dict[str, Any]) -> bool:
    h = metrics.get("selection_holdout") or {}
    return int(h.get("resolved") or 0) >= 3 and (h.get("expectancy_r") is not None and h.get("expectancy_r") > 0) and (h.get("profit_factor") is None or h.get("profit_factor") > 1.05)


def optimize_cell(cell: Tuple[str, str, str, str]) -> Dict[str, Any]:
    started = time.time()
    system_type, family, symbol, tf = cell
    data = _load_cell(cell)
    if not data:
        return _finding(cell, None, {}, {}, "NO_HISTORY", started, data)

    coarse = []
    for spec in _coarse_specs(tf):
        metrics, exec_stats = _evaluate(data, system_type, spec)
        coarse.append((selection_score(metrics), spec, metrics, exec_stats))
        if rss_mb() >= getattr(config, "MEMORY_HARD_MB", 430):
            break
    coarse.sort(key=lambda x: x[0], reverse=True)
    seeds = coarse[: max(2, int(getattr(config, "CAUSAL_REFINE_SEEDS", 4)))]

    refined = list(coarse[:6])
    seen = {x[1].key() for x in refined}
    max_refined = max(12, int(getattr(config, "CAUSAL_MAX_REFINED_PER_CELL", 72)))
    for _, seed, _, _ in seeds:
        for spec in _refine(seed):
            if spec.key() in seen:
                continue
            seen.add(spec.key())
            metrics, exec_stats = _evaluate(data, system_type, spec)
            refined.append((selection_score(metrics), spec, metrics, exec_stats))
            if len(refined) >= max_refined or rss_mb() >= getattr(config, "MEMORY_HARD_MB", 430):
                break
        if len(refined) >= max_refined or rss_mb() >= getattr(config, "MEMORY_HARD_MB", 430):
            break
    refined.sort(key=lambda x: x[0], reverse=True)
    best = refined[0] if refined else (None, None, {}, {})
    _score, spec, metrics, exec_stats = best
    status = "SIMULATED_PROFITABLE" if spec and _is_simulated_profitable(metrics) else "NO_EDGE_FOUND"
    return _finding(cell, spec, metrics, exec_stats, status, started, data, candidates_tested=len(refined))


def _finding(cell, spec, metrics, exec_stats, status, started, data, candidates_tested=0):
    system_type, family, symbol, tf = cell
    if spec is None:
        spec = StrategySpec("NONE", "BOTH", max_hold=_hold_bars(tf), max_wait=_wait_bars(tf))
    scope = _scope(cell, spec)
    contract = runtime_contract(scope)
    robustness = _robustness(metrics, system_type) if metrics else {"symbols_tested": len(data), "positive_symbols": 0, "positive_symbol_ratio": 0.0, "cross_asset_required": system_type == "futures"}
    strategy_id = _strategy_id(cell, spec)
    return {
        "engine": config.ENGINE,
        "experiment": EXPERIMENT,
        "feature_key": f"{config.ENGINE}:{EXPERIMENT}:{family}:{symbol}:{tf}:{strategy_id}"[:500],
        "scope": scope,
        "stage": status,
        "authority": "RESEARCH_ONLY",
        "metrics": metrics or {"all": {"resolved": 0, "net_evidence_pct": 0.0}, "validation": {"resolved": 0}, "walk_forward": {}},
        "meta": {
            "is_causal_coverage": True,
            "causal_candle_replay": True,
            "coverage_cell": {"system_type": system_type, "market_family": family, "symbol": symbol, "timeframe": tf},
            "coverage_status": status,
            "causal_strategy_id": strategy_id,
            "causal_strategy_family": spec.family,
            "causal_strategy_spec": spec.__dict__,
            "selection_uses_final_oos": False,
            "same_bar_tp_sl": "CONSERVATIVE_SL_FIRST",
            "cost_model": "MODELED_FEES_SLIPPAGE_AND_FUNDING_STRESS",
            "runtime_contract": contract,
            "runtime_trackable": bool(contract.get("runtime_trackable")),
            "historical_feature_proxy": True,
            "live_shadow_confirms_runtime_parity": True,
            "cross_asset": robustness,
            "signals_seen": int((exec_stats or {}).get("signals") or 0),
            "entries_activated": int((exec_stats or {}).get("activated") or 0),
            "candidates_tested": int(candidates_tested),
            "source_series": len(data),
            "source_bars": sum(len(x) for x in data.values()),
            "elapsed_seconds": round(time.time() - started, 2),
            "cache": cache_stats(),
            "rss_mb": round(rss_mb(), 2),
            "production_changes_allowed": False,
            "changes_entry_sl_tp": False,
            "changes_safety": False,
            "changes_leverage": False,
            "guarantees_future_profit": False,
        },
        "research_version": config.VERSION,
        "updated_at": utc_now(),
    }


def analyze_coverage_for_engine(engine: str) -> List[Dict[str, Any]]:
    if not bool(getattr(config, "CAUSAL_ENABLED", True)):
        return []
    cells = list(LANES.get(str(engine or "").lower(), []))
    out = []
    for cell in cells:
        if rss_mb() >= getattr(config, "MEMORY_HARD_MB", 430):
            print(f"🧠 [I CAUSAL] hard memory gate at {rss_mb():.1f}MB", flush=True)
            break
        print(f"🧪 [I CAUSAL] {engine} -> {cell[1]} {cell[2]} {cell[3]}", flush=True)
        out.append(optimize_cell(cell))
    return out
