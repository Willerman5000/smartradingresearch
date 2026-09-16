from __future__ import annotations

"""FINAL V1 RC4 — Active Symbol×Timeframe Profitability Coverage.

Research target:
- Futures core: 7 symbols × (30M,1H,2H,4H) × (LONG,SHORT) = 56 action cells.
- Futures swing context: BTC/ETH/SOL × (12H,1D) × (LONG,SHORT) = 12 action cells.
- Spot: BTC-USDT, PAXG-USDT, PAXG-BTC × 4 TF × (COMPRA_SPOT,VENTA_SPOT) = 24 action cells.
- Total contract = 92 cells.

RC8.1: profitability authority is action-specific. A LONG edge never fills SHORT;
a COMPRA_SPOT edge never fills VENTA_SPOT. Regime remains a StrategySpec/filter,
not a mandatory extra governance dimension.

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


def _future_cells(tfs: Sequence[str], symbols: Sequence[str] = FUTURES_SYMBOLS) -> List[Tuple[str, str, str, str, str]]:
    return [("futures", "CRYPTO_FUTURES", symbol, tf, action)
            for tf in tfs for symbol in symbols for action in ("LONG", "SHORT")]

def _spot_cells(family: str, symbol: str, tfs: Sequence[str]) -> List[Tuple[str, str, str, str, str]]:
    return [("spot", family, symbol, tf, action)
            for tf in tfs for action in ("COMPRA_SPOT", "VENTA_SPOT")]

def _action_direction(system_type: str, action: str) -> str:
    action=str(action or '').upper()
    if str(system_type or '').lower() == 'spot':
        return 'LONG' if action == 'COMPRA_SPOT' else 'SHORT'
    return action if action in {'LONG','SHORT'} else 'BOTH'


# RC8.1 keeps the four workers but each base market cell is split by action.
# 12H/1D are also useful as context for lower-TF production decisions, but each
# remains its own independently validated profitability cell.
LANES = {
    "execution": [*_future_cells(("30M",)), *_future_cells(("12H",), FUTURES_HIGH_TF_SYMBOLS)],  # 10
    "risk": [*_future_cells(("1H",)), *_future_cells(("1D",), FUTURES_HIGH_TF_SYMBOLS)],          # 10
    "strategy": _future_cells(("2H", "4H")),                                                    # 14
    "traders": [
        *_spot_cells("CRYPTO_SPOT", "BTC-USDT", SPOT_TFS),
        *_spot_cells("PAXG_USDT", "PAXG-USDT", SPOT_TFS),
        *_spot_cells("PAXG_BTC", "PAXG-BTC", SPOT_TFS),
    ],                                                    # 24 action cells
}


def all_coverage_cells() -> List[Tuple[str, str, str, str, str]]:
    out: List[Tuple[str, str, str, str, str]] = []
    for owner in ("execution", "risk", "strategy", "traders"):
        out.extend(LANES.get(owner, []))
    return out


def owner_for_cell(cell: Tuple[str, str, str, str, str]) -> str:
    for owner, cells in LANES.items():
        if cell in cells:
            return owner
    return "strategy"


def coverage_cell_id(cell: Tuple[str, str, str, str, str]) -> str:
    return "|".join(str(x).upper() for x in cell)


def _bars_for(tf: str) -> int:
    defaults = {"30M": 8000, "1H": 7000, "2H": 5500, "4H": 4500, "12H": 2800, "1D": 1900, "1W": 650}
    hard = int(getattr(config, "CAUSAL_MAX_BARS", 12000))
    return min(hard, defaults.get(tf, 4500))


def _hold_bars(tf: str) -> int:
    return {"30M": 24, "1H": 18, "2H": 14, "4H": 10, "12H": 8, "1D": 6, "1W": 4}.get(tf, 18)


def _wait_bars(tf: str) -> int:
    return {"30M": 3, "1H": 3, "2H": 3, "4H": 2, "12H": 2, "1D": 2, "1W": 1}.get(tf, 3)


SEARCH_WAVE_NAMES=("CORE","TREND","OSCILLATORS","DIVERGENCES","VOLATILITY","FLOW_STRUCTURE","COMPOSITE","ALT_PARAMS")

# Inventory covered by causal OHLCV replay vs observational historical features.
INDICATOR_COVERAGE_MANIFEST={
    "causal_ohlcv":["EMA","RSI","RSI_MULTI","MACD","STOCHASTIC","ATR","ADX_DMI","BOLLINGER","VOLUME","VWAP","MFI","FORCE_INDEX","OBV","CCI","WILLIAMS_R","SUPERTREND_PROXY","PARABOLIC_SAR","ICHIMOKU","FIBONACCI_RETRACE","FVG_PROXY","VOLUME_PROFILE_POC_PROXY","HVN_LVN_PROFILE_PROXY","STRUCTURE_SWEEP","DIVERGENCES_REGULAR_HIDDEN","RSI_MAVERICK"],
    "observational_only_without_trustworthy_historical_series":["ORDER_FLOW","ORDER_BOOK","OPEN_INTEREST","FUNDING","BASIS","LIQUIDATION_HEATMAP","WHALE_ACTIVITY","FEAR_GREED","MACRO_NEWS","CEX_FLOWS_RESERVES","SENTIMENT"],
}


def _search_wave(cell: Tuple[str,str,str,str,str]) -> tuple[int,str]:
    # Result-independent rotation: time slot + stable cell hash. OOS never selects
    # what is tried next, which reduces adaptive p-hacking risk.
    slot=max(1,int(getattr(config,"BOOTSTRAP_FAST_MINUTES",5)))*60
    epoch=int(time.time()//slot)
    h=int(hashlib.sha256(coverage_cell_id(cell).encode()).hexdigest()[:8],16)
    idx=(epoch+h)%min(len(SEARCH_WAVE_NAMES),int(getattr(config,"RC5_SEARCH_WAVES",8)))
    return idx,SEARCH_WAVE_NAMES[idx]


def _core_specs(tf: str) -> List[StrategySpec]:
    h = _hold_bars(tf); w = _wait_bars(tf); specs=[]
    for direction in ("LONG","SHORT"):
        for fast,slow in ((9,21),(12,36),(21,50)):
            for strength in (0.0,0.35): specs.append(StrategySpec("TREND_CONTINUATION",direction,fast=fast,slow=slow,rsi_low=46,rsi_high=54,sl_atr=1.5,rr=2,max_wait=w,max_hold=h,trend_strength_min=strength))
            specs.append(StrategySpec("TREND_PULLBACK",direction,fast=fast,slow=slow,rsi_low=43,rsi_high=57,entry_style="PULLBACK",entry_atr=.25,sl_atr=1.5,rr=2,max_wait=w,max_hold=h,trend_strength_min=.25))
        for lb,vm,vol in ((12,.85,"ANY"),(20,1,"NORMAL"),(30,1.1,"EXPANSION")): specs.append(StrategySpec("BREAKOUT_RETEST",direction,lookback=lb,volume_mult=vm,sl_atr=1.5,rr=2,max_wait=w,max_hold=h,volatility_mode=vol))
        for low,high,vol in ((25,75,"ANY"),(30,70,"NORMAL"),(35,65,"QUIET")): specs.append(StrategySpec("MEAN_REVERSION",direction,rsi_low=low,rsi_high=high,sl_atr=1.25,rr=1.75,max_wait=w,max_hold=h,volatility_mode=vol))
        for lb in (10,20,30): specs.append(StrategySpec("SWEEP_REVERSAL",direction,rsi_low=48,rsi_high=52,lookback=lb,sl_atr=1.25,rr=2,max_wait=w,max_hold=h))
        for fast,slow in ((9,21),(12,36)): specs.append(StrategySpec("EMA_RECLAIM",direction,fast=fast,slow=slow,sl_atr=1.35,rr=2,max_wait=w,max_hold=h,trend_strength_min=.2))
        for low,high in ((45,55),(42,58),(40,60)): specs.append(StrategySpec("RSI_TREND",direction,rsi_low=low,rsi_high=high,sl_atr=1.5,rr=2,max_wait=w,max_hold=h,trend_strength_min=.25))
        for low,high,vm in ((42,58,.9),(40,60,1)): specs.append(StrategySpec("MOMENTUM_BREAKOUT",direction,fast=9,slow=21,rsi_low=low,rsi_high=high,volume_mult=vm,sl_atr=1.5,rr=2.25,max_wait=w,max_hold=h,volatility_mode="EXPANSION",trend_strength_min=.25))
    return specs


def _wave_specs(tf: str, wave: str) -> List[StrategySpec]:
    h=_hold_bars(tf); w=_wait_bars(tf); specs=[]; wave=str(wave).upper()
    if wave=="CORE": return _core_specs(tf)
    for d in ("LONG","SHORT"):
        if wave=="TREND":
            for fast,slow in ((9,21),(12,26),(12,36),(21,50)):
                specs += [StrategySpec("MACD_TREND",d,fast=fast,slow=slow,signal_period=9,sl_atr=1.5,rr=2.25,max_wait=w,max_hold=h,trend_strength_min=.2,indicator="MACD"),StrategySpec("SUPERTREND_PULLBACK",d,fast=fast,slow=slow,entry_style="PULLBACK",entry_atr=.3,sl_atr=1.6,rr=2.25,max_wait=w,max_hold=h,indicator="SUPERTREND")]
            specs.append(StrategySpec("PSAR_TREND",d,sl_atr=1.45,rr=2.25,max_wait=w,max_hold=h,trend_strength_min=.15,indicator="PARABOLIC_SAR"))
            for adx in (20,25,30): specs.append(StrategySpec("ADX_DI_TREND",d,rsi_high=adx,aux_period=14,sl_atr=1.5,rr=2.25,max_wait=w,max_hold=h,indicator="ADX_DMI"))
            specs.append(StrategySpec("ICHIMOKU_TREND",d,sl_atr=1.6,rr=2.5,max_wait=w,max_hold=h,indicator="ICHIMOKU"))
        elif wave=="OSCILLATORS":
            for period in (7,10,14,21):
                specs += [StrategySpec("STOCH_REVERSAL",d,aux_period=period,rsi_low=20,rsi_high=80,sl_atr=1.2,rr=1.8,max_wait=w,max_hold=h,volatility_mode="NORMAL",indicator="STOCHASTIC"),StrategySpec("MFI_REVERSAL",d,aux_period=period,rsi_low=20,rsi_high=80,sl_atr=1.25,rr=1.8,max_wait=w,max_hold=h,indicator="MFI")]
            for threshold in (80,100,120): specs.append(StrategySpec("CCI_WILLIAMS_REVERSAL",d,rsi_high=threshold,aux_period=20,sl_atr=1.25,rr=1.9,max_wait=w,max_hold=h,indicator="CCI_WILLIAMS"))
            specs.append(StrategySpec("MULTI_RSI_TREND",d,fast=9,slow=21,sl_atr=1.4,rr=2.2,max_wait=w,max_hold=h,trend_strength_min=.2,indicator="RSI_MULTI"))
            for lo,hi,bm in ((0.15,0.85,2.0),(0.20,0.80,2.0),(0.25,0.75,2.5)):
                specs.append(StrategySpec("RSI_MAVERICK_REVERSAL",d,rsi_low=lo,rsi_high=hi,aux_period=20,band_mult=bm,sl_atr=1.25,rr=1.9,max_wait=w,max_hold=h,indicator="RSI_MAVERICK"))
        elif wave=="DIVERGENCES":
            for ind in ("RSI","RSI_MAVERICK","MACD","STOCH","MFI","CCI","WILLIAMS","OBV","FORCE_INDEX"):
                for lb in (14,20,30):
                    specs.append(StrategySpec("DIVERGENCE_REVERSAL",d,lookback=lb,sl_atr=1.3,rr=2.2,max_wait=w,max_hold=h,indicator=ind,divergence_mode="REGULAR"))
                    specs.append(StrategySpec("HIDDEN_DIVERGENCE_TREND",d,lookback=lb,sl_atr=1.45,rr=2.4,max_wait=w,max_hold=h,trend_strength_min=.2,indicator=ind,divergence_mode="HIDDEN"))
        elif wave=="VOLATILITY":
            for bm in (1.8,2.0,2.2):
                specs += [StrategySpec("BOLLINGER_REVERSION",d,band_mult=bm,sl_atr=1.2,rr=1.8,max_wait=w,max_hold=h,volatility_mode="NORMAL",indicator="BOLLINGER"),StrategySpec("BOLLINGER_SQUEEZE",d,band_mult=bm,volume_mult=1.0,sl_atr=1.5,rr=2.5,max_wait=w,max_hold=h,volatility_mode="EXPANSION",indicator="BOLLINGER_SQUEEZE")]
            specs.append(StrategySpec("VWAP_REVERSION",d,sl_atr=1.25,rr=1.8,max_wait=w,max_hold=h,volatility_mode="NORMAL",indicator="VWAP"))
        elif wave=="FLOW_STRUCTURE":
            specs += [StrategySpec("MFI_OBV_FLOW",d,sl_atr=1.45,rr=2.2,max_wait=w,max_hold=h,trend_strength_min=.2,indicator="MFI_OBV_FORCE")]
            for lb in (20,30,50): specs.append(StrategySpec("FIB_RETRACE_TREND",d,lookback=lb,entry_style="PULLBACK",entry_atr=.18,sl_atr=1.5,rr=2.5,max_wait=w,max_hold=h,trend_strength_min=.2,indicator="FIBONACCI"))
            # Structure/liquidity is causally represented by sweep/retest families.
            for lb in (12,20,36):
                specs.append(StrategySpec("SWEEP_REVERSAL",d,lookback=lb,volume_mult=.9,sl_atr=1.3,rr=2.25,max_wait=w,max_hold=h,indicator="STRUCTURE_SWEEP"))
                specs.append(StrategySpec("FVG_RECLAIM",d,lookback=lb,entry_style="PULLBACK",entry_atr=.18,sl_atr=1.4,rr=2.4,max_wait=w,max_hold=h,trend_strength_min=.15,indicator="FVG"))
            specs.append(StrategySpec("VOLUME_PROFILE_RETEST",d,lookback=50,sl_atr=1.5,rr=2.3,max_wait=w,max_hold=h,trend_strength_min=.15,indicator="VOLUME_PROFILE_POC"))
            specs.append(StrategySpec("VOLUME_PROFILE_NODE_REACTION",d,lookback=50,sl_atr=1.5,rr=2.4,max_wait=w,max_hold=h,trend_strength_min=.10,indicator="HVN_LVN_PROFILE"))
        elif wave=="COMPOSITE":
            # Only economically interpretable 2-3 family confluences, never arbitrary Cartesian combinations.
            specs += [StrategySpec("MOMENTUM_BREAKOUT",d,fast=12,slow=36,rsi_low=44,rsi_high=56,volume_mult=1.05,sl_atr=1.5,rr=2.5,max_wait=w,max_hold=h,volatility_mode="EXPANSION",trend_strength_min=.35,indicator="EMA_RSI_VOLUME"),StrategySpec("MFI_OBV_FLOW",d,fast=12,slow=36,sl_atr=1.5,rr=2.4,max_wait=w,max_hold=h,trend_strength_min=.35,indicator="TREND_FLOW"),StrategySpec("SUPERTREND_PULLBACK",d,fast=12,slow=36,entry_style="PULLBACK",entry_atr=.3,sl_atr=1.5,rr=2.5,max_wait=w,max_hold=h,trend_strength_min=.3,indicator="SUPERTREND_RSI")]
        elif wave=="ALT_PARAMS":
            for rlen in (7,10,14,21,28):
                for lo,hi in ((38,62),(40,60),(42,58),(45,55)):
                    specs.append(StrategySpec("RSI_TREND",d,rsi_len=rlen,rsi_low=lo,rsi_high=hi,fast=12,slow=36,sl_atr=1.5,rr=2.2,max_wait=w,max_hold=h,trend_strength_min=.2,indicator="RSI"))
            for fast,slow in ((5,13),(8,21),(9,21),(12,26),(20,50)):
                specs.append(StrategySpec("TREND_CONTINUATION",d,fast=fast,slow=slow,rsi_low=46,rsi_high=54,sl_atr=1.5,rr=2.2,max_wait=w,max_hold=h,trend_strength_min=.2,indicator="EMA"))
    return specs or _core_specs(tf)


def _coarse_specs(tf: str) -> List[StrategySpec]:
    """Compatibility helper: core declared universe only; RC5 rotates extra waves separately."""
    return _core_specs(tf)


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
                    indicator=seed.indicator, aux_period=seed.aux_period,
                    signal_period=seed.signal_period, band_mult=seed.band_mult,
                    divergence_mode=seed.divergence_mode,
                )


def _strategy_id(cell: Tuple[str, str, str, str, str], spec: StrategySpec) -> str:
    raw = json.dumps({"cell": cell, "spec": spec.key()}, sort_keys=True, separators=(",", ":"))
    return "CI_" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def _scope(cell: Tuple[str, str, str, str, str], spec: StrategySpec) -> Dict[str, str]:
    system_type, family, symbol, tf, action = cell
    direction = _action_direction(system_type, action)
    scope = {"market_family": family, "timeframe": tf, "symbol": symbol, "action": action}
    if direction in {"LONG", "SHORT"}:
        scope["direction"] = direction
    if spec.family in {"TREND_CONTINUATION", "TREND_PULLBACK", "EMA_RECLAIM", "RSI_TREND", "MOMENTUM_BREAKOUT", "MACD_TREND", "ADX_DI_TREND", "SUPERTREND_PULLBACK", "MFI_OBV_FLOW", "FIB_RETRACE_TREND", "HIDDEN_DIVERGENCE_TREND", "MULTI_RSI_TREND", "ICHIMOKU_TREND", "VOLUME_PROFILE_RETEST", "FVG_RECLAIM"}:
        scope["regime"] = "TREND_UP" if spec.direction_mode == "LONG" else "TREND_DOWN"
    elif spec.family in {"MEAN_REVERSION","STOCH_REVERSAL","CCI_WILLIAMS_REVERSAL","MFI_REVERSAL","RSI_MAVERICK_REVERSAL","BOLLINGER_REVERSION","VWAP_REVERSION","DIVERGENCE_REVERSAL"}:
        scope["regime"] = "BALANCE"
    if spec.family == "TREND_PULLBACK":
        scope["has_pullback"] = "YES"
    if spec.family == "SWEEP_REVERSAL":
        scope["has_sweep"] = "YES"
    return scope


def _load_cell(cell: Tuple[str, str, str, str, str]) -> Dict[str, Sequence[Any]]:
    system_type, _family, symbol, tf, _action = cell
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


def optimize_cell_candidates(cell: Tuple[str, str, str, str, str], owner_engine: str | None = None, blocked_strategy_ids=None) -> List[Dict[str, Any]]:
    """Return pre-ranked finalists; ranking never reads Final OOS.

    RC8 Knowledge Core: exact configurations still inside their rejection cooldown
    are skipped using compact candidate-memory IDs.  The search remains
    result-independent otherwise: wave rotation is driven by time+cell hash, not
    by Final OOS, and expired configurations may be retested in a new regime.
    """
    started = time.time()
    system_type, family, symbol, tf, action = cell
    data = _load_cell(cell)
    if not data:
        return [_finding(cell, None, {}, {}, "NO_HISTORY", started, data, owner_engine=owner_engine, finalist_rank=1)]

    blocked = {str(x) for x in (blocked_strategy_ids or set()) if x}
    wave_idx, wave_name = _search_wave(cell)
    target_direction = _action_direction(system_type, action)
    declared_specs = [spec for spec in _wave_specs(tf, wave_name) if spec.direction_mode == target_direction]
    if blocked:
        declared_specs = [spec for spec in declared_specs if _strategy_id(cell, spec) not in blocked]
        # If this wave was already exhausted, advance deterministically through
        # the predeclared universe instead of immediately retesting a rejection.
        if not declared_specs:
            active_waves=SEARCH_WAVE_NAMES[:min(len(SEARCH_WAVE_NAMES),int(getattr(config,'RC5_SEARCH_WAVES',8)))]
            base_idx = active_waves.index(wave_name) if wave_name in active_waves else (wave_idx % len(active_waves))
            for offset in range(1, len(active_waves)):
                alt = active_waves[(base_idx + offset) % len(active_waves)]
                alt_specs = [spec for spec in _wave_specs(tf, alt) if spec.direction_mode == target_direction and _strategy_id(cell, spec) not in blocked]
                if alt_specs:
                    wave_name = alt
                    wave_idx = (base_idx + offset) % len(SEARCH_WAVE_NAMES)
                    declared_specs = alt_specs
                    break
    coarse = []
    for spec in declared_specs:
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
            if spec.key() in seen or _strategy_id(cell, spec) in blocked:
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
            search_wave=wave_name, search_wave_index=wave_idx, declared_candidates=len(declared_specs),
        ))
    return out


def optimize_cell(cell: Tuple[str, str, str, str, str], owner_engine: str | None = None) -> Dict[str, Any]:
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


def _finding(cell, spec, metrics, exec_stats, status, started, data, candidates_tested=0, owner_engine=None, finalist_rank=1, finalists_tested=1, search_wave="REGISTRY", search_wave_index=-1, declared_candidates=1):
    system_type, family, symbol, tf, action = cell
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
            "coverage_cell": {"system_type": system_type, "market_family": family, "symbol": symbol, "timeframe": tf, "action": action},
            "coverage_cell_id": coverage_cell_id(cell),
            "coverage_owner_engine": source_engine,
            "coverage_status": status,
            "coverage_target_mode": "ONE_VALIDATED_SPECIALIST_PER_MARKET_SYMBOL_TIMEFRAME_ACTION",
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
            "production_parity": False,
            "production_parity_reason": "Historical causal/proxy replay; real production committee and Entry/SL/TP require Shadow/LIVE confirmation.",
            "live_shadow_confirms_runtime_parity": True,
            "cross_asset": robustness,
            "signals_seen": int((exec_stats or {}).get("signals") or 0),
            "entries_activated": int((exec_stats or {}).get("activated") or 0),
            "candidates_tested": int(candidates_tested),
            "search_space_version": "RC5_LOGICAL_GRAMMAR_V1",
            "search_wave": str(search_wave),
            "search_wave_index": int(search_wave_index),
            "declared_candidate_budget": int(declared_candidates),
            "broad_indicator_search": str(search_wave) not in {"CORE","REGISTRY"},
            "indicator_manifest": INDICATOR_COVERAGE_MANIFEST,
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
        system_type=str(cell_raw.get("system_type") or "").lower()
        spec = _spec_from_meta(meta)
        if spec is None:
            continue
        action=str(cell_raw.get("action") or "").upper()
        if not action:
            action = ("COMPRA_SPOT" if spec.direction_mode == "LONG" else "VENTA_SPOT") if system_type == "spot" else spec.direction_mode
        cell = (
            system_type,
            str(cell_raw.get("market_family") or ""),
            str(cell_raw.get("symbol") or "").upper(),
            str(cell_raw.get("timeframe") or "").upper(),
            action,
        )
        if cell not in LANES.get(owner, []):
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


def analyze_coverage_for_engine(engine: str, on_finding=None, priority_cell_ids=None, only_cell_ids=None, blocked_strategy_ids_by_cell=None) -> List[Dict[str, Any]]:
    if not bool(getattr(config, "CAUSAL_ENABLED", True)):
        return []
    owner = str(engine or "").lower()
    cells = list(LANES.get(owner, []))
    priority = {str(x) for x in (priority_cell_ids or [])}
    only = {str(x) for x in (only_cell_ids or [])}
    if only:
        cells = [c for c in cells if coverage_cell_id(c) in only]
    cells.sort(key=lambda c: (0 if coverage_cell_id(c) in priority else 1, coverage_cell_id(c)))
    out: List[Dict[str, Any]] = []
    for cell in cells:
        if rss_mb() >= getattr(config, "MEMORY_HARD_MB", 430):
            print(f"🧠 [I.2 CAUSAL] hard memory gate at {rss_mb():.1f}MB", flush=True)
            break
        print(f"🧪 [I.2 CAUSAL] {owner} -> {cell[2]} {cell[3]} {cell[4]}", flush=True)
        blocked_map = blocked_strategy_ids_by_cell or {}
        findings = optimize_cell_candidates(cell, owner_engine=owner, blocked_strategy_ids=blocked_map.get(coverage_cell_id(cell), set()))
        out.extend(findings)
        if callable(on_finding):
            for finding in findings:
                try:
                    on_finding(finding)
                except Exception as exc:
                    print(f"⚠️ [I.2 CAUSAL] incremental persistence: {exc}", flush=True)
    return out
