from __future__ import annotations

"""FINAL V1 RC4 — Active Symbol×Timeframe Profitability Coverage.

Research target:
- Futures core: 7 symbols × (30M,1H,2H,4H) = 28 specialist cells.
- Futures swing context: BTC/ETH/SOL × (12H,1D) = 6 specialist cells.
- Spot: BTC-USDT, PAXG-USDT, PAXG-BTC × 4 TF = 12 cells.
- Total contract = 46 cells.

5m/15m were retired from V1 after the final audit: they were not part of the
operator's intended workflow and consumed disproportionate refresh/backtest
capacity. Historical rows remain preserved but are not part of the active V1
contract.

The engine does NOT force a profitable result. It keeps each cell in the
iterative research loop until a pre-declared finalist survives untouched Final
OOS + walk-forward + costs + runtime parity, then Validation can move it to
SHADOW_READY. Final OOS never ranks candidates.
"""

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import config
from causal_backtest import StrategySpec, replay_series, selection_score, temporal_metrics
from full_stack_certification import build_full_stack_certification
from db import utc_now
from engines.base import runtime_contract, rss_mb
from historical_market import fetch_market, cache_stats

EXPERIMENT = "CAUSAL_COVERAGE_STRATEGY"
FUTURES_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT", "LINK-USDT", "BNB-USDT")
SPOT_SYMBOLS = ("BTC-USDT", "PAXG-USDT", "PAXG-BTC")
FUTURES_CORE_TFS = ("30M", "1H", "2H", "4H")
FUTURES_HIGH_TFS = ("12H", "1D")
FUTURES_HIGH_TF_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
FUTURES_TFS = FUTURES_CORE_TFS + FUTURES_HIGH_TFS
SPOT_TFS = ("4H", "12H", "1D", "1W")


def _future_cells(tfs: Sequence[str], symbols: Sequence[str] = FUTURES_SYMBOLS) -> List[Tuple[str, str, str, str]]:
    return [("futures", "CRYPTO_FUTURES", symbol, tf) for tf in tfs for symbol in symbols]


# RC4 balances the four workers while adding only six high-TF cells.
# 12H/1D are also useful as context for lower-TF production decisions, but each
# remains its own independently validated profitability cell.
LANES = {
    "execution": [*_future_cells(("30M",)), *_future_cells(("12H",), FUTURES_HIGH_TF_SYMBOLS)],  # 10
    "risk": [*_future_cells(("1H",)), *_future_cells(("1D",), FUTURES_HIGH_TF_SYMBOLS)],          # 10
    "strategy": _future_cells(("2H", "4H")),                                                    # 14
    "traders": [
        *[("spot", "CRYPTO_SPOT", "BTC-USDT", tf) for tf in SPOT_TFS],
        *[("spot", "PAXG_USDT", "PAXG-USDT", tf) for tf in SPOT_TFS],
        *[("spot", "PAXG_BTC", "PAXG-BTC", tf) for tf in SPOT_TFS],
    ],                                                    # 12
}


def all_coverage_cells() -> List[Tuple[str, str, str, str]]:
    out: List[Tuple[str, str, str, str]] = []
    for owner in ("execution", "risk", "strategy", "traders"):
        out.extend(LANES.get(owner, []))
    return out


def owner_for_cell(cell: Tuple[str, str, str, str]) -> str:
    for owner, cells in LANES.items():
        if cell in cells:
            return owner
    return "strategy"


def coverage_cell_id(cell: Tuple[str, str, str, str]) -> str:
    return "|".join(str(x).upper() for x in cell)


def _bars_for(tf: str) -> int:
    defaults = {"30M": 8000, "1H": 7000, "2H": 5500, "4H": 4500, "12H": 2800, "1D": 1900, "1W": 650}
    hard = int(getattr(config, "CAUSAL_MAX_BARS", 12000))
    return min(hard, defaults.get(tf, 4500))


def _hold_bars(tf: str) -> int:
    return {"30M": 24, "1H": 18, "2H": 14, "4H": 10, "12H": 8, "1D": 6, "1W": 4}.get(tf, 18)


def _wait_bars(tf: str) -> int:
    return {"30M": 3, "1H": 3, "2H": 3, "4H": 2, "12H": 2, "1D": 2, "1W": 1}.get(tf, 3)


def _coarse_specs(tf: str) -> List[StrategySpec]:
    """Pre-declared universe. No Final OOS value is used to create/rank it."""
    h = _hold_bars(tf); w = _wait_bars(tf)
    specs: List[StrategySpec] = []
    for direction in ("LONG", "SHORT"):
        # Trend continuation, including normal/expansion volatility specialists.
        for fast, slow in ((9, 21), (12, 36), (21, 50)):
            for strength in (0.0, 0.35):
                specs.append(StrategySpec(
                    "TREND_CONTINUATION", direction, fast=fast, slow=slow,
                    rsi_low=46, rsi_high=54, sl_atr=1.5, rr=2.0,
                    max_wait=w, max_hold=h, trend_strength_min=strength,
                ))
        # Pullback specialists; often better geometry than market entry.
        for fast, slow in ((9, 21), (12, 36), (21, 50)):
            specs.append(StrategySpec(
                "TREND_PULLBACK", direction, fast=fast, slow=slow,
                rsi_low=43, rsi_high=57, entry_style="PULLBACK", entry_atr=0.25,
                sl_atr=1.5, rr=2.0, max_wait=w, max_hold=h,
                trend_strength_min=0.25,
            ))
        # Breakout family with volume and volatility variants.
        for lb, vm, vol in ((12, 0.85, "ANY"), (20, 1.0, "NORMAL"), (30, 1.10, "EXPANSION")):
            specs.append(StrategySpec(
                "BREAKOUT_RETEST", direction, fast=12, slow=36, lookback=lb,
                volume_mult=vm, sl_atr=1.5, rr=2.0, max_wait=w, max_hold=h,
                volatility_mode=vol,
            ))
        # Range mean reversion.
        for low, high, vol in ((25, 75, "ANY"), (30, 70, "NORMAL"), (35, 65, "QUIET")):
            specs.append(StrategySpec(
                "MEAN_REVERSION", direction, fast=12, slow=36,
                rsi_low=low, rsi_high=high, sl_atr=1.25, rr=1.75,
                max_wait=w, max_hold=h, volatility_mode=vol,
            ))
        # Liquidity sweep / reversal.
        for lb in (10, 20, 30):
            specs.append(StrategySpec(
                "SWEEP_REVERSAL", direction, fast=12, slow=36,
                rsi_low=48, rsi_high=52, lookback=lb, sl_atr=1.25, rr=2.0,
                max_wait=w, max_hold=h,
            ))
        # Additional bounded families improve symbol-specific specialization.
        for fast, slow in ((9, 21), (12, 36)):
            specs.append(StrategySpec(
                "EMA_RECLAIM", direction, fast=fast, slow=slow,
                sl_atr=1.35, rr=2.0, max_wait=w, max_hold=h,
                trend_strength_min=0.20,
            ))
        for low, high in ((45, 55), (42, 58), (40, 60)):
            specs.append(StrategySpec(
                "RSI_TREND", direction, fast=12, slow=36,
                rsi_low=low, rsi_high=high, sl_atr=1.5, rr=2.0,
                max_wait=w, max_hold=h, trend_strength_min=0.25,
            ))
        for low, high, vm in ((42, 58, 0.9), (40, 60, 1.0)):
            specs.append(StrategySpec(
                "MOMENTUM_BREAKOUT", direction, fast=9, slow=21,
                rsi_low=low, rsi_high=high, volume_mult=vm,
                sl_atr=1.5, rr=2.25, max_wait=w, max_hold=h,
                volatility_mode="EXPANSION", trend_strength_min=0.25,
            ))
    return specs


def _refine(seed: StrategySpec, tf: str = "") -> Iterable[StrategySpec]:
    # Bounded geometry refinement. Still uses only Discovery + Selection score.
    high_tf = str(tf).upper() in {"12H", "1D", "1W"}
    entry_grid = (("NEXT_OPEN", 0.0), ("PULLBACK", 0.18), ("PULLBACK", 0.30), ("PULLBACK", 0.42), ("PULLBACK", 0.60)) if high_tf else (("NEXT_OPEN", 0.0), ("PULLBACK", 0.18), ("PULLBACK", 0.30), ("PULLBACK", 0.42))
    sl_grid = (1.2, 1.5, 1.8, 2.1, 2.4) if high_tf else (0.9, 1.15, 1.4, 1.7, 2.0)
    rr_grid = (1.75, 2.25, 2.75, 3.25, 4.0) if high_tf else (1.35, 1.6, 2.0, 2.5, 3.0)
    for entry_style, entry_atr in entry_grid:
        for sl in sl_grid:
            for rr in rr_grid:
                yield StrategySpec(
                    seed.family, seed.direction_mode, fast=seed.fast, slow=seed.slow,
                    rsi_len=seed.rsi_len, rsi_low=seed.rsi_low, rsi_high=seed.rsi_high,
                    lookback=seed.lookback, volume_mult=seed.volume_mult,
                    entry_style=entry_style, entry_atr=entry_atr,
                    sl_atr=sl, rr=rr, max_wait=seed.max_wait, max_hold=seed.max_hold,
                    volatility_mode=seed.volatility_mode,
                    trend_strength_min=seed.trend_strength_min,
                )


def _strategy_id(cell: Tuple[str, str, str, str], spec: StrategySpec) -> str:
    raw = json.dumps({"cell": cell, "spec": spec.key()}, sort_keys=True, separators=(",", ":"))
    return "CI_" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _scope(cell: Tuple[str, str, str, str], spec: StrategySpec) -> Dict[str, str]:
    system_type, family, symbol, tf = cell
    scope = {"market_family": family, "timeframe": tf, "symbol": symbol}
    if spec.direction_mode in {"LONG", "SHORT"}:
        scope["direction"] = spec.direction_mode
    if spec.family in {"TREND_CONTINUATION", "TREND_PULLBACK", "EMA_RECLAIM", "RSI_TREND", "MOMENTUM_BREAKOUT"}:
        scope["regime"] = "TREND_UP" if spec.direction_mode == "LONG" else "TREND_DOWN"
    elif spec.family == "MEAN_REVERSION":
        scope["regime"] = "BALANCE"
    if spec.family == "TREND_PULLBACK":
        scope["has_pullback"] = "YES"
    if spec.family == "SWEEP_REVERSAL":
        scope["has_sweep"] = "YES"
    return scope


def _load_cell(cell: Tuple[str, str, str, str]) -> Dict[str, Sequence[Any]]:
    system_type, _family, symbol, tf = cell
    bars = _bars_for(tf)
    symbols = [symbol]
    data: Dict[str, Sequence[Any]] = {}

    def one(sym: str):
        try:
            return sym, fetch_market(system_type, sym, tf, bars), None
        except Exception as exc:
            return sym, [], exc

    workers = min(len(symbols), int(getattr(config, "CAUSAL_FETCH_WORKERS", 3)))
    if workers <= 1:
        results = [one(sym) for sym in symbols]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="causal-fetch") as pool:
            future_map = {pool.submit(one, sym): sym for sym in symbols}
            for fut in as_completed(future_map):
                results.append(fut.result())

    for sym, candles, exc in results:
        if exc is not None:
            print(f"⚠️ [I.2 CAUSAL] {sym} {tf}: {str(exc)[:140]}", flush=True)
        if len(candles) >= 140:
            data[sym] = candles
        if rss_mb() >= getattr(config, "CAUSAL_MEMORY_TARGET_MB", 390):
            print(f"🧠 [I.2 CAUSAL] memory target {rss_mb():.1f}MB; loaded={len(data)}", flush=True)
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
    return {
        "symbols_tested": len(by_symbol),
        "positive_symbols": len(positive),
        "positive_symbol_ratio": round(len(positive) / max(1, len(by_symbol)), 4),
        "positive_symbol_names": positive,
        # I.2 validates Futures specialists per symbol. Cross-asset evidence can
        # still be studied separately, but it is no longer a gate for a cell.
        "cross_asset_required": False,
        "specialist_cell": True,
    }


def _selection_profitable(metrics: Dict[str, Any]) -> bool:
    h = metrics.get("selection_holdout") or {}
    return (
        int(h.get("resolved") or 0) >= 3
        and h.get("expectancy_r") is not None and float(h.get("expectancy_r")) > 0
        and (h.get("profit_factor") is None or float(h.get("profit_factor")) > 1.05)
    )


def _diverse_finalists(ranked, limit: int):
    chosen = []
    signatures = set()
    for row in ranked:
        spec = row[1]
        sig = (spec.family, spec.direction_mode)
        if sig in signatures:
            continue
        chosen.append(row); signatures.add(sig)
        if len(chosen) >= limit:
            return chosen
    for row in ranked:
        if row in chosen:
            continue
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen


def optimize_cell_candidates(cell: Tuple[str, str, str, str], owner_engine: str | None = None) -> List[Dict[str, Any]]:
    """Return pre-ranked finalists; ranking never reads Final OOS."""
    started = time.time()
    system_type, family, symbol, tf = cell
    data = _load_cell(cell)
    if not data:
        return [_finding(cell, None, {}, {}, "NO_HISTORY", started, data, owner_engine=owner_engine, finalist_rank=1)]

    coarse = []
    for spec in _coarse_specs(tf):
        metrics, exec_stats = _evaluate(data, system_type, spec)
        coarse.append((selection_score(metrics), spec, metrics, exec_stats))
        if rss_mb() >= getattr(config, "MEMORY_HARD_MB", 430):
            break
    coarse.sort(key=lambda x: x[0], reverse=True)
    seeds = coarse[: max(2, int(getattr(config, "CAUSAL_REFINE_SEEDS", 4)))]

    refined = list(coarse[:10])
    seen = {x[1].key() for x in refined}
    max_refined = max(16, int(getattr(config, "CAUSAL_MAX_REFINED_PER_CELL", 84)))
    for _, seed, _, _ in seeds:
        for spec in _refine(seed, tf):
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

    limit = max(1, int(getattr(config, "CAUSAL_FINALISTS_PER_CELL", 4)))
    finalists = _diverse_finalists(refined, limit)
    if not finalists:
        return [_finding(cell, None, {}, {}, "NO_EDGE_FOUND", started, data, owner_engine=owner_engine, finalist_rank=1)]

    out = []
    for rank, (_score, spec, metrics, exec_stats) in enumerate(finalists, 1):
        status = "SELECTION_PROFITABLE" if _selection_profitable(metrics) else "SEARCHING"
        out.append(_finding(
            cell, spec, metrics, exec_stats, status, started, data,
            candidates_tested=len(refined), owner_engine=owner_engine,
            finalist_rank=rank, finalists_tested=len(finalists),
        ))
    return out


def optimize_cell(cell: Tuple[str, str, str, str], owner_engine: str | None = None) -> Dict[str, Any]:
    """Compatibility wrapper used by older tests/rescue code."""
    return optimize_cell_candidates(cell, owner_engine=owner_engine)[0]


def _bar_signature_value(bar):
    if isinstance(bar, dict):
        return (
            bar.get("timestamp") or bar.get("time") or bar.get("datetime") or bar.get("date"),
            bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close"), bar.get("volume"),
        )
    return tuple(getattr(bar, k, None) for k in ("timestamp","time","open","high","low","close","volume"))


def _causal_dataset_signature(data: Dict[str, Sequence[Any]]) -> str:
    payload=[]
    for symbol, bars in sorted((data or {}).items()):
        seq=list(bars or [])
        tail=[_bar_signature_value(x) for x in seq[-3:]]
        payload.append((symbol, len(seq), tail))
    raw=json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _finding(cell, spec, metrics, exec_stats, status, started, data, candidates_tested=0, owner_engine=None, finalist_rank=1, finalists_tested=1):
    system_type, family, symbol, tf = cell
    if spec is None:
        spec = StrategySpec("NONE", "BOTH", max_hold=_hold_bars(tf), max_wait=_wait_bars(tf))
    scope = _scope(cell, spec)
    contract = runtime_contract(scope)
    robustness = _robustness(metrics, system_type) if metrics else {"symbols_tested": len(data), "positive_symbols": 0, "positive_symbol_ratio": 0.0, "cross_asset_required": False, "specialist_cell": True}
    strategy_id = _strategy_id(cell, spec)
    source_engine = str(owner_engine or config.ENGINE).lower()
    return {
        "engine": source_engine,
        "experiment": EXPERIMENT,
        "feature_key": f"{source_engine}:{EXPERIMENT}:{family}:{symbol}:{tf}:{strategy_id}"[:500],
        "scope": scope,
        "stage": status,
        "authority": "RESEARCH_ONLY",
        "metrics": metrics or {"all": {"resolved": 0, "net_evidence_pct": 0.0}, "validation": {"resolved": 0}, "walk_forward": {}},
        "meta": {
            "is_causal_coverage": True,
            "causal_candle_replay": True,
            "coverage_cell": {"system_type": system_type, "market_family": family, "symbol": symbol, "timeframe": tf},
            "coverage_cell_id": coverage_cell_id(cell),
            "coverage_owner_engine": source_engine,
            "coverage_status": status,
            "coverage_target_mode": "ONE_VALIDATED_SPECIALIST_PER_SYMBOL_TIMEFRAME",
            "causal_strategy_id": strategy_id,
            "causal_strategy_family": spec.family,
            "causal_strategy_spec": spec.__dict__,
            "finalist_rank_selection_only": int(finalist_rank),
            "finalists_predeclared_for_oos": int(finalists_tested),
            "selection_uses_final_oos": False,
            "same_bar_tp_sl": "CONSERVATIVE_SL_FIRST",
            "cost_model": "MODELED_FEES_SLIPPAGE_AND_FUNDING_STRESS",
            # RC3: replay operativo del Guardian sobre la geometría del
            # especialista. Es diagnóstico RESEARCH_ONLY y nunca entra en
            # selection_score ni concede autoridad productiva.
            "guardian_operational_replay": (
                ((metrics or {}).get("validation") or {}).get("guardian_operational_replay")
                or ((metrics or {}).get("all") or {}).get("guardian_operational_replay")
                or {}
            ),
            "guardian_operational_replay_selection_authority": False,
            # RC4: a single read-only certificate joins strategy edge, entry
            # execution, costs and Guardian overlay for THIS cell only. It is
            # never used for candidate ranking and never grants production
            # authority by itself.
            "full_stack_profitability_certification": build_full_stack_certification(
                system_type=system_type, symbol=symbol, timeframe=tf,
                strategy_family=spec.family, strategy_spec=spec.__dict__,
                metrics=metrics or {}, execution_stats=exec_stats or {},
            ),
            "full_stack_certification_selection_authority": False,
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
            "causal_dataset_signature": _causal_dataset_signature(data),
            "final_oos_locked": True,
            "final_oos_reused_for_selection": False,
            "oos_generation_rule": "NEW_DATA_REQUIRED_FOR_RETEST",
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


def _spec_from_meta(meta: Dict[str, Any]) -> StrategySpec | None:
    raw = (meta or {}).get("causal_strategy_spec") or {}
    if not isinstance(raw, dict) or not raw.get("family"):
        return None
    allowed = set(StrategySpec.__dataclass_fields__.keys())
    kwargs = {k: v for k, v in raw.items() if k in allowed}
    try:
        return StrategySpec(**kwargs)
    except Exception:
        return None


def retest_registry_promotions(rows: List[Dict[str, Any]], engine: str, on_finding=None) -> List[Dict[str, Any]]:
    """Rebacktest incumbents, prioritizing Shadow/live deterioration."""
    owner = str(engine or "").lower()
    out = []; seen = set()
    cap = int(getattr(config, "CAUSAL_REGISTRY_RETEST_LIMIT", 16))
    rows = sorted(
        list(rows or []),
        key=lambda r: (
            int(((r.get("meta") or {}).get("shadow_recycle_priority") or 0)),
            str(r.get("updated_at") or ""),
        ),
        reverse=True,
    )
    for row in rows:
        if len(out) >= cap:
            break
        if str(row.get("source_engine") or "").lower() != owner:
            continue
        meta = row.get("meta") or {}
        cell_raw = meta.get("coverage_cell") or {}
        cell = (
            str(cell_raw.get("system_type") or "").lower(),
            str(cell_raw.get("market_family") or ""),
            str(cell_raw.get("symbol") or "").upper(),
            str(cell_raw.get("timeframe") or "").upper(),
        )
        if cell not in LANES.get(owner, []):
            continue
        spec = _spec_from_meta(meta)
        if spec is None:
            continue
        dedupe = (coverage_cell_id(cell), spec.key())
        if dedupe in seen:
            continue
        seen.add(dedupe)
        data = _load_cell(cell)
        if not data:
            continue
        current_signature = _causal_dataset_signature(data)
        previous_signature = str(meta.get("causal_dataset_signature") or "")
        if previous_signature and previous_signature == current_signature:
            # Same historical exam = no new statistical information. Live
            # divergence remains recorded, but we wait for new candles before
            # retesting the same incumbent.
            continue
        started = time.time()
        metrics, exec_stats = _evaluate(data, cell[0], spec)
        recycle_priority = int(meta.get("shadow_recycle_priority") or 0)
        finding = _finding(
            cell, spec, metrics, exec_stats, "REGISTRY_RETEST", started, data,
            candidates_tested=1, owner_engine=owner,
            finalist_rank=int(meta.get("finalist_rank_selection_only") or 1),
            finalists_tested=int(meta.get("finalists_predeclared_for_oos") or 1),
        )
        finding["experiment"] = "CAUSAL_SHADOW_RECYCLE" if recycle_priority > 0 else "CAUSAL_REGISTRY_RETEST"
        original = str(row.get("candidate_key") or "")
        finding["feature_key"] = (f"{owner}:{finding['experiment']}:{original}" if original else finding["feature_key"] + ":RETEST")[:500]
        fmeta = dict(finding.get("meta") or {})
        fmeta.update({
            "registry_retest": True,
            "shadow_recycle": recycle_priority > 0,
            "shadow_recycle_priority": recycle_priority,
            "shadow_live_snapshot": meta.get("shadow_live_snapshot") or {},
            "original_candidate_key": original,
            "original_stage": row.get("stage"),
            "coverage_status": "RETESTED",
        })
        finding["meta"] = fmeta
        out.append(finding)
        if callable(on_finding):
            on_finding(finding)
    return out


def analyze_coverage_for_engine(engine: str, on_finding=None, priority_cell_ids=None) -> List[Dict[str, Any]]:
    if not bool(getattr(config, "CAUSAL_ENABLED", True)):
        return []
    owner = str(engine or "").lower()
    cells = list(LANES.get(owner, []))
    priority = {str(x) for x in (priority_cell_ids or [])}
    cells.sort(key=lambda c: (0 if coverage_cell_id(c) in priority else 1, coverage_cell_id(c)))
    out: List[Dict[str, Any]] = []
    for cell in cells:
        if rss_mb() >= getattr(config, "MEMORY_HARD_MB", 430):
            print(f"🧠 [I.2 CAUSAL] hard memory gate at {rss_mb():.1f}MB", flush=True)
            break
        print(f"🧪 [I.2 CAUSAL] {owner} -> {cell[2]} {cell[3]}", flush=True)
        findings = optimize_cell_candidates(cell, owner_engine=owner)
        out.extend(findings)
        if callable(on_finding):
            for finding in findings:
                try:
                    on_finding(finding)
                except Exception as exc:
                    print(f"⚠️ [I.2 CAUSAL] incremental persistence: {exc}", flush=True)
    return out
