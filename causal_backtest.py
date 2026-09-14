from __future__ import annotations

"""Causal candle replay for Research Federation V1.7.

Commit I.2 keeps the Final OOS lockbox untouched by candidate ranking while
expanding the *pre-declared* strategy universe used for each symbol×timeframe
specialist.  A candidate can be selected only from Discovery + Selection
Holdout. Final OOS is read afterwards by Validation.

The extra filters are deliberately simple and causal (EMA/ATR/RSI/volume). They
exist to let a cell abstain in regimes where its family historically had no
edge rather than forcing one generic strategy to trade every condition.
"""

from collections import deque
from dataclasses import dataclass
import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import config
from historical_market import Candle


@dataclass(frozen=True)
class StrategySpec:
    family: str
    direction_mode: str = "BOTH"  # BOTH | LONG | SHORT
    fast: int = 12
    slow: int = 36
    rsi_len: int = 14
    rsi_low: float = 32.0
    rsi_high: float = 68.0
    lookback: int = 20
    volume_mult: float = 1.0
    entry_style: str = "NEXT_OPEN"  # NEXT_OPEN | PULLBACK
    entry_atr: float = 0.25
    sl_atr: float = 1.5
    rr: float = 2.0
    max_wait: int = 3
    max_hold: int = 24
    # I.2: bounded specialist filters. They are evaluated with data <= bar i.
    volatility_mode: str = "ANY"  # ANY | QUIET | NORMAL | EXPANSION
    trend_strength_min: float = 0.0  # abs(EMAfast-EMAslow)/ATR

    def key(self) -> str:
        return (
            f"{self.family}:{self.direction_mode}:f{self.fast}:s{self.slow}:"
            f"r{self.rsi_len}:{self.rsi_low:g}-{self.rsi_high:g}:lb{self.lookback}:"
            f"v{self.volume_mult:g}:{self.entry_style}:{self.entry_atr:g}:"
            f"sl{self.sl_atr:g}:rr{self.rr:g}:w{self.max_wait}:h{self.max_hold}:"
            f"vol{self.volatility_mode}:ts{self.trend_strength_min:g}"
        )


@dataclass
class Trade:
    symbol: str
    direction: str
    signal_ts: int
    entry_ts: int
    exit_ts: int
    entry: float
    stop: float
    target: float
    r_net: float
    mfe_r: float
    mae_r: float
    bars_held: int
    exit_reason: str
    # RC3 diagnostic-only operational replay. These fields NEVER participate
    # in candidate selection; they measure whether a fixed Guardian policy
    # would add or destroy R after the strategy geometry is already defined.
    guardian_r_net: float | None = None
    guardian_delta_r: float | None = None
    guardian_exit_reason: str | None = None
    guardian_interventions: int = 0


def _ema(values: Sequence[float], period: int) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (max(1, period) + 1.0)
    out = [float(values[0])]
    for x in values[1:]:
        out.append(out[-1] + alpha * (float(x) - out[-1]))
    return out


def _atr(candles: Sequence[Candle], period: int = 14) -> List[float]:
    if not candles:
        return []
    trs = [max(1e-12, candles[0].high - candles[0].low)]
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    alpha = 1.0 / max(1, period)
    out = [trs[0]]
    for x in trs[1:]:
        out.append(out[-1] + alpha * (x - out[-1]))
    return out


def _rsi(closes: Sequence[float], period: int = 14) -> List[float]:
    if not closes:
        return []
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains[i] = max(0.0, d)
        losses[i] = max(0.0, -d)
    alpha = 1.0 / max(1, period)
    ag = al = 0.0
    out = [50.0] * len(closes)
    for i in range(1, len(closes)):
        ag += alpha * (gains[i] - ag)
        al += alpha * (losses[i] - al)
        if i < period:
            out[i] = 50.0
        elif al <= 1e-12:
            out[i] = 100.0
        else:
            rs = ag / al
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _rolling_mean(values: Sequence[float], period: int) -> List[float]:
    out = [0.0] * len(values)
    q: deque[float] = deque()
    total = 0.0
    for i, x in enumerate(values):
        q.append(float(x)); total += float(x)
        if len(q) > period:
            total -= q.popleft()
        out[i] = total / max(1, len(q))
    return out


def features(candles: Sequence[Candle], spec: StrategySpec) -> Dict[str, List[float]]:
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]
    atr = _atr(candles, 14)
    atr_pct = [float(a) / max(abs(float(c.close)), 1e-12) for a, c in zip(atr, candles)]
    return {
        "ema_fast": _ema(closes, spec.fast),
        "ema_slow": _ema(closes, spec.slow),
        "rsi": _rsi(closes, spec.rsi_len),
        "atr": atr,
        "atr_pct": atr_pct,
        "atr_pct_ma": _rolling_mean(atr_pct, 100),
        "volume_ma": _rolling_mean(volumes, 20),
    }


def _regime(i: int, feat: Dict[str, List[float]], candles: Sequence[Candle]) -> str:
    fast = feat["ema_fast"][i]; slow = feat["ema_slow"][i]
    atr = max(feat["atr"][i], candles[i].close * 1e-6)
    distance = (fast - slow) / atr
    if distance >= 0.35:
        return "TREND_UP"
    if distance <= -0.35:
        return "TREND_DOWN"
    return "BALANCE"


def _volatility_ok(i: int, spec: StrategySpec, feat: Dict[str, List[float]]) -> bool:
    mode = str(spec.volatility_mode or "ANY").upper()
    if mode == "ANY":
        return True
    baseline = max(float(feat["atr_pct_ma"][i] or 0.0), 1e-12)
    ratio = float(feat["atr_pct"][i] or 0.0) / baseline
    if mode == "QUIET":
        return ratio <= 0.82
    if mode == "NORMAL":
        return 0.72 <= ratio <= 1.35
    if mode == "EXPANSION":
        return ratio >= 1.18
    return True


def _direction_signal(i: int, spec: StrategySpec, feat: Dict[str, List[float]], candles: Sequence[Candle]) -> Tuple[str | None, Dict[str, Any]]:
    if i < max(spec.slow, spec.lookback, spec.rsi_len, 100) + 2:
        return None, {}
    c = candles[i]
    prev = candles[i - 1]
    fast = feat["ema_fast"][i]; slow = feat["ema_slow"][i]
    prev_fast = feat["ema_fast"][i - 1]
    rsi = feat["rsi"][i]
    atr = max(feat["atr"][i], c.close * 1e-6)
    regime = _regime(i, feat, candles)
    trend_strength = abs(fast - slow) / atr
    if trend_strength + 1e-12 < max(0.0, float(spec.trend_strength_min or 0.0)):
        return None, {"regime": regime, "trend_strength": round(trend_strength, 4)}
    if not _volatility_ok(i, spec, feat):
        return None, {"regime": regime, "trend_strength": round(trend_strength, 4)}

    vma = max(1e-12, feat["volume_ma"][i])
    volume_ok = c.volume >= vma * spec.volume_mult
    prior = candles[max(0, i - spec.lookback):i]
    prior_high = max((x.high for x in prior), default=c.high)
    prior_low = min((x.low for x in prior), default=c.low)
    pullback_long = regime == "TREND_UP" and c.low <= fast + 0.20 * atr and c.close >= fast
    pullback_short = regime == "TREND_DOWN" and c.high >= fast - 0.20 * atr and c.close <= fast
    sweep_low = c.low < prior_low and c.close > prior_low
    sweep_high = c.high > prior_high and c.close < prior_high

    direction = None
    fam = str(spec.family or "").upper()
    if fam == "TREND_CONTINUATION":
        if regime == "TREND_UP" and c.close > fast and rsi >= max(50.0, spec.rsi_low):
            direction = "LONG"
        elif regime == "TREND_DOWN" and c.close < fast and rsi <= min(50.0, spec.rsi_high):
            direction = "SHORT"
    elif fam == "TREND_PULLBACK":
        if pullback_long and rsi >= max(43.0, spec.rsi_low):
            direction = "LONG"
        elif pullback_short and rsi <= min(57.0, spec.rsi_high):
            direction = "SHORT"
    elif fam == "BREAKOUT_RETEST":
        if prev.close <= prior_high and c.close > prior_high and volume_ok:
            direction = "LONG"
        elif prev.close >= prior_low and c.close < prior_low and volume_ok:
            direction = "SHORT"
    elif fam == "MEAN_REVERSION":
        if regime == "BALANCE" and rsi <= spec.rsi_low and c.close <= fast - 0.45 * atr:
            direction = "LONG"
        elif regime == "BALANCE" and rsi >= spec.rsi_high and c.close >= fast + 0.45 * atr:
            direction = "SHORT"
    elif fam == "SWEEP_REVERSAL":
        if sweep_low and rsi <= min(50.0, spec.rsi_high):
            direction = "LONG"
        elif sweep_high and rsi >= max(50.0, spec.rsi_low):
            direction = "SHORT"
    elif fam == "EMA_RECLAIM":
        if regime == "TREND_UP" and prev.close < prev_fast and c.close > fast and rsi >= 48:
            direction = "LONG"
        elif regime == "TREND_DOWN" and prev.close > prev_fast and c.close < fast and rsi <= 52:
            direction = "SHORT"
    elif fam == "RSI_TREND":
        if regime == "TREND_UP" and c.close > fast and rsi >= spec.rsi_high:
            direction = "LONG"
        elif regime == "TREND_DOWN" and c.close < fast and rsi <= spec.rsi_low:
            direction = "SHORT"
    elif fam == "MOMENTUM_BREAKOUT":
        if regime == "TREND_UP" and c.close > prev.high and rsi >= spec.rsi_high and volume_ok:
            direction = "LONG"
        elif regime == "TREND_DOWN" and c.close < prev.low and rsi <= spec.rsi_low and volume_ok:
            direction = "SHORT"

    if spec.direction_mode in {"LONG", "SHORT"} and direction != spec.direction_mode:
        direction = None
    ctx = {
        "regime": regime,
        "has_pullback": "YES" if (pullback_long or pullback_short) else "NO",
        "has_sweep": "YES" if (sweep_low or sweep_high) else "NO",
        "trend_strength": round(trend_strength, 4),
        "volatility_mode": str(spec.volatility_mode or "ANY").upper(),
    }
    return direction, ctx


def _cost_r(entry: float, stop: float, bars_held: int, system_type: str, seconds_per_bar: int) -> float:
    risk_pct = abs(entry - stop) / max(abs(entry), 1e-12)
    if risk_pct <= 1e-9:
        return 99.0
    if system_type == "spot":
        bps = float(getattr(config, "CAUSAL_SPOT_ROUNDTRIP_BPS", 20)) + float(getattr(config, "CAUSAL_SPOT_SLIPPAGE_BPS", 4))
        return (bps / 10000.0) / risk_pct
    bps = float(getattr(config, "CAUSAL_FUTURES_ROUNDTRIP_BPS", 12)) + float(getattr(config, "CAUSAL_FUTURES_SLIPPAGE_BPS", 6))
    funding_bps = float(getattr(config, "CAUSAL_FUNDING_STRESS_BPS_8H", 1.0))
    hours_per_bar = max(1.0 / 60.0, float(seconds_per_bar) / 3600.0)
    funding_blocks = max(0.0, bars_held * hours_per_bar / 8.0)
    return ((bps + funding_bps * funding_blocks) / 10000.0) / risk_pct


def _guardian_operational_replay(
    candles: Sequence[Candle],
    entry_idx: int,
    direction: str,
    entry: float,
    original_stop: float,
    original_target: float,
    max_hold: int,
    system_type: str,
    seconds_per_bar: int,
) -> Dict[str, Any]:
    """Causal RC3 replay of the fixed Futures Guardian management policy.

    This is DIAGNOSTIC only. It does not select candidates and therefore cannot
    leak Final OOS into ranking. Every management decision uses only bars that
    have already CLOSED before the next bar is evaluated.

    The overlay models HOLD / PROTECT / EXTEND / structural EXIT. Scale-in and
    partial REDUCE are deliberately counted as research opportunities but do
    not invent fills or position sizing in historical PnL.
    """
    risk = abs(entry - original_stop)
    reward = abs(original_target - entry)
    if risk <= 0 or reward <= 0:
        return {"r_net": None, "reason": "INVALID", "interventions": 0}

    stop = float(original_stop)
    target = float(original_target)
    interventions = 0
    reason = "TIMEOUT"
    exit_idx = min(len(candles) - 1, entry_idx + max(1, int(max_hold)))
    exit_price = float(candles[exit_idx].close)
    post_closes: List[float] = []
    post_highs: List[float] = []
    post_lows: List[float] = []

    for k in range(entry_idx, min(len(candles), entry_idx + max(1, int(max_hold)) + 1)):
        bar = candles[k]
        if direction == "LONG":
            sl_hit = bar.low <= stop
            tp_hit = bar.high >= target
        else:
            sl_hit = bar.high >= stop
            tp_hit = bar.low <= target
        # Same-bar ambiguity stays conservative.
        if sl_hit:
            exit_idx, exit_price, reason = k, stop, "GUARDIAN_SL"
            break
        if tp_hit:
            exit_idx, exit_price, reason = k, target, "GUARDIAN_TP"
            break

        post_closes.append(float(bar.close))
        post_highs.append(float(bar.high))
        post_lows.append(float(bar.low))
        if len(post_closes) < 2:
            continue

        close = post_closes[-1]
        favorable = max(0.0, close - entry) if direction == "LONG" else max(0.0, entry - close)
        progress_r = favorable / risk
        tp_progress = favorable / reward

        lookback = min(5, len(post_closes) - 1)
        base = post_closes[-1 - lookback]
        recent_change_pct = ((close - base) / base * 100.0) if base else 0.0
        fast = sum(post_closes[-3:]) / min(3, len(post_closes))
        slow_n = min(8, len(post_closes))
        slow = sum(post_closes[-slow_n:]) / slow_n
        momentum_with = (
            recent_change_pct >= 0.22 and fast >= slow
            if direction == "LONG"
            else recent_change_pct <= -0.22 and fast <= slow
        )

        structure_bad = False
        if len(post_closes) >= 6:
            ref = post_closes[-6:-2]
            if direction == "LONG":
                level = min(ref)
                structure_bad = post_closes[-1] < level and post_closes[-2] < level and fast < slow
            else:
                level = max(ref)
                structure_bad = post_closes[-1] > level and post_closes[-2] > level and fast > slow
        adverse_r = (
            max(0.0, entry - close) / risk
            if direction == "LONG"
            else max(0.0, close - entry) / risk
        )
        if structure_bad and adverse_r >= 0.55:
            exit_idx, exit_price, reason = k, close, "GUARDIAN_INVALIDATION_EXIT"
            interventions += 1
            break

        # PROTECT for next candle only after the current candle has closed.
        if progress_r >= 0.65 and len(post_lows) >= 2:
            old_stop = stop
            if direction == "LONG":
                base_protection = entry if progress_r >= 1.0 else original_stop + risk * 0.45
                structural = min(post_lows[-3:]) - risk * 0.10
                candidate = max(original_stop, base_protection, structural)
                if progress_r < 1.0:
                    candidate = min(candidate, entry - risk * 0.06)
                candidate = min(candidate, close - risk * 0.14)
                if candidate > stop + risk * 0.01 and candidate < close:
                    stop = candidate
            else:
                base_protection = entry if progress_r >= 1.0 else original_stop - risk * 0.45
                structural = max(post_highs[-3:]) + risk * 0.10
                candidate = min(original_stop, base_protection, structural)
                if progress_r < 1.0:
                    candidate = max(candidate, entry + risk * 0.06)
                candidate = max(candidate, close + risk * 0.14)
                if candidate < stop - risk * 0.01 and candidate > close:
                    stop = candidate
            if stop != old_stop:
                interventions += 1

        # EXTEND to the nearest PRE-EXISTING closed structural swing beyond TP.
        # Context ends at k, so no future leakage; if a post-entry candle had
        # already touched original TP, the intrabar block above would have exited.
        if tp_progress >= 0.70 and momentum_with and not structure_bad:
            context_start = max(0, entry_idx - 20)
            context = candles[context_start:entry_idx]
            old_target = target
            if direction == "LONG":
                levels = sorted({float(x.high) for x in context if float(x.high) > target})
                if levels:
                    candidate = levels[0]
                    extension = candidate - target
                    if risk * 0.15 <= extension <= risk * 1.25:
                        target = candidate
            else:
                levels = sorted({float(x.low) for x in context if float(x.low) < target}, reverse=True)
                if levels:
                    candidate = levels[0]
                    extension = target - candidate
                    if risk * 0.15 <= extension <= risk * 1.25:
                        target = candidate
            if target != old_target:
                interventions += 1

    held = max(1, exit_idx - entry_idx + 1)
    gross_r = ((exit_price - entry) / risk) if direction == "LONG" else ((entry - exit_price) / risk)
    cost_r = _cost_r(entry, original_stop, held, system_type, seconds_per_bar)
    return {
        "r_net": gross_r - cost_r,
        "reason": reason,
        "interventions": interventions,
        "exit_idx": exit_idx,
        "stop": stop,
        "target": target,
    }


def replay_series(symbol: str, system_type: str, candles: Sequence[Candle], spec: StrategySpec) -> Tuple[List[Trade], Dict[str, int]]:
    if len(candles) < max(140, spec.slow + spec.lookback + 110):
        return [], {"signals": 0, "activated": 0}
    feat = features(candles, spec)
    seconds_per_bar = max(60, int(candles[1].ts - candles[0].ts)) if len(candles) > 1 else 3600
    trades: List[Trade] = []
    signals = activated = 0
    i = max(spec.slow, spec.lookback, spec.rsi_len, 100) + 2
    n = len(candles)
    while i < n - 2:
        direction, _ctx = _direction_signal(i, spec, feat, candles)
        if direction is None:
            i += 1
            continue
        signals += 1
        atr = max(feat["atr"][i], candles[i].close * 1e-5)
        signal_close = candles[i].close
        entry_idx = None; entry = None
        for j in range(i + 1, min(n, i + 1 + max(1, spec.max_wait))):
            bar = candles[j]
            if spec.entry_style == "NEXT_OPEN":
                entry_idx = j; entry = bar.open; break
            wanted = signal_close - spec.entry_atr * atr if direction == "LONG" else signal_close + spec.entry_atr * atr
            if bar.low <= wanted <= bar.high:
                entry_idx = j; entry = wanted; break
        if entry_idx is None or entry is None:
            i += 1
            continue
        activated += 1
        risk = max(atr * spec.sl_atr, abs(entry) * 0.0005)
        stop = entry - risk if direction == "LONG" else entry + risk
        target = entry + risk * spec.rr if direction == "LONG" else entry - risk * spec.rr
        mfe = mae = 0.0
        exit_idx = min(n - 1, entry_idx + max(1, spec.max_hold))
        reason = "TIMEOUT"
        gross_r = 0.0
        for k in range(entry_idx, min(n, entry_idx + max(1, spec.max_hold) + 1)):
            bar = candles[k]
            if direction == "LONG":
                mfe = max(mfe, (bar.high - entry) / risk)
                mae = max(mae, (entry - bar.low) / risk)
                sl_hit = bar.low <= stop
                tp_hit = bar.high >= target
            else:
                mfe = max(mfe, (entry - bar.low) / risk)
                mae = max(mae, (bar.high - entry) / risk)
                sl_hit = bar.high >= stop
                tp_hit = bar.low <= target
            # Conservative intrabar ordering: if both touched, count SL.
            if sl_hit:
                exit_idx = k; reason = "SL"; gross_r = -1.0; break
            if tp_hit:
                exit_idx = k; reason = "TP"; gross_r = spec.rr; break
        else:
            last = candles[exit_idx].close
            gross_r = ((last - entry) / risk) if direction == "LONG" else ((entry - last) / risk)
            gross_r = max(-1.0, min(spec.rr, gross_r))
        held = max(1, exit_idx - entry_idx + 1)
        cost_r = _cost_r(entry, stop, held, system_type, seconds_per_bar)
        net_r = gross_r - cost_r
        operational = _guardian_operational_replay(
            candles, entry_idx, direction, entry, stop, target, spec.max_hold,
            system_type, seconds_per_bar,
        )
        guardian_r_net = operational.get("r_net")
        guardian_delta_r = (
            float(guardian_r_net) - float(net_r)
            if guardian_r_net is not None else None
        )
        trades.append(Trade(
            symbol, direction, candles[i].ts, candles[entry_idx].ts, candles[exit_idx].ts,
            entry, stop, target, net_r, mfe, mae, held, reason,
            guardian_r_net=guardian_r_net,
            guardian_delta_r=guardian_delta_r,
            guardian_exit_reason=operational.get("reason"),
            guardian_interventions=int(operational.get("interventions") or 0),
        ))
        i = max(i + 1, exit_idx + 1)
    return trades, {"signals": signals, "activated": activated}


def _drawdown(rs: Iterable[float]) -> float:
    eq = peak = dd = 0.0
    for r in rs:
        eq += r; peak = max(peak, eq); dd = max(dd, peak - eq)
    return dd


def summarize_trades(trades: Sequence[Trade]) -> Dict[str, Any]:
    rs = [float(t.r_net) for t in trades]
    wins = sum(1 for x in rs if x > 0); losses = sum(1 for x in rs if x < 0)
    pos = sum(x for x in rs if x > 0); neg = abs(sum(x for x in rs if x < 0))
    pf = pos / neg if neg > 1e-12 else None
    symbols: Dict[str, List[float]] = {}
    for t in trades:
        symbols.setdefault(t.symbol, []).append(t.r_net)
    by_symbol = {}
    for sym, vals in symbols.items():
        by_symbol[sym] = {
            "n": len(vals),
            "expectancy_r": round(sum(vals) / len(vals), 5) if vals else None,
            "wins": sum(1 for v in vals if v > 0),
        }

    guardian_rs = [float(t.guardian_r_net) for t in trades if t.guardian_r_net is not None]
    guardian_delta = [float(t.guardian_delta_r) for t in trades if t.guardian_delta_r is not None]
    gpos = sum(x for x in guardian_rs if x > 0); gneg = abs(sum(x for x in guardian_rs if x < 0))
    gpf = gpos / gneg if gneg > 1e-12 else None
    guardian_overlay = {
        "authority": "RESEARCH_ONLY",
        "selection_uses_overlay": False,
        "n": len(guardian_rs),
        "expectancy_r": round(sum(guardian_rs) / len(guardian_rs), 5) if guardian_rs else None,
        "profit_factor": round(gpf, 4) if gpf is not None else None,
        "max_drawdown_r": round(_drawdown(guardian_rs), 4) if guardian_rs else None,
        "delta_expectancy_r_vs_original": round(sum(guardian_delta) / len(guardian_delta), 5) if guardian_delta else None,
        "improved_trades": sum(1 for x in guardian_delta if x > 1e-9),
        "harmed_trades": sum(1 for x in guardian_delta if x < -1e-9),
        "neutral_trades": sum(1 for x in guardian_delta if abs(x) <= 1e-9),
        "interventions": sum(int(t.guardian_interventions or 0) for t in trades),
        "policy": "RC3_GUARDIAN_CLOSED_CANDLE_CAUSAL_OVERLAY",
        "scale_in_pnl_modeled": False,
    }
    return {
        "n_rows": len(trades), "resolved": len(trades), "wins": wins, "losses": losses,
        "win_rate_pct": round(wins / len(rs) * 100.0, 2) if rs else None,
        "expectancy_r": round(sum(rs) / len(rs), 5) if rs else None,
        "profit_factor": round(pf, 4) if pf is not None else None,
        "profit_factor_degenerate": bool(rs and pf is None and pos > 0),
        "max_drawdown_r": round(_drawdown(rs), 4) if rs else None,
        "avg_mfe_r": round(sum(t.mfe_r for t in trades) / len(trades), 4) if trades else None,
        "avg_mae_r": round(sum(t.mae_r for t in trades) / len(trades), 4) if trades else None,
        "symbols": sorted(symbols), "by_symbol": by_symbol,
        "net_evidence_count": len(trades), "net_evidence_pct": 100.0 if trades else 0.0,
        "cost_model": "MODELED_FEES_SLIPPAGE_AND_FUNDING_STRESS",
        "guardian_operational_replay": guardian_overlay,
    }


def temporal_metrics(trades: Sequence[Trade]) -> Dict[str, Any]:
    ordered = sorted(trades, key=lambda t: (t.signal_ts, t.symbol))
    n = len(ordered)
    a = max(1, int(n * 0.60)) if n else 0
    b = max(a, int(n * 0.80)) if n else 0
    discovery = ordered[:a]
    selection_holdout = ordered[a:b]
    final_oos = ordered[b:]
    wf = []
    tail = ordered[a:]
    if len(tail) >= 9:
        step = max(1, len(tail) // 3)
        for k in range(3):
            chunk = tail[k * step: (k + 1) * step if k < 2 else len(tail)]
            if chunk:
                wf.append({"fold": k + 1, "holdout": summarize_trades(chunk)})
    positive = [x for x in wf if (x.get("holdout") or {}).get("expectancy_r") is not None and (x.get("holdout") or {}).get("expectancy_r") > 0]
    return {
        "all": summarize_trades(ordered),
        "train": summarize_trades(discovery),
        "selection_holdout": summarize_trades(selection_holdout),
        "validation": summarize_trades(final_oos),
        "walk_forward": {
            "folds": wf, "valid_folds": len(wf), "positive_folds": len(positive),
            "positive_fold_ratio": round(len(positive) / len(wf), 4) if wf else None,
        },
        "methodology": {
            "evidence_type": "CAUSAL_CANDLE_REPLAY",
            "discovery_label": "DISCOVERY_60",
            "selection_label": "SELECTION_HOLDOUT_20",
            "validation_label": "UNTOUCHED_FINAL_OOS_20",
            "causal_candle_replay": True,
            "same_bar_tp_sl": "CONSERVATIVE_SL_FIRST",
        },
    }


def selection_score(metrics: Dict[str, Any]) -> float:
    """Ranking uses Discovery + Selection only. Final OOS is never read here."""
    train = metrics.get("train") or {}; hold = metrics.get("selection_holdout") or {}
    hn = int(hold.get("resolved") or 0); tn = int(train.get("resolved") or 0)
    he = hold.get("expectancy_r"); hp = hold.get("profit_factor"); dd = hold.get("max_drawdown_r")
    te = train.get("expectancy_r")
    if hn <= 0 or he is None:
        return -999.0
    pf_term = min(2.0, float(hp)) if hp is not None else 0.0
    dd_penalty = min(3.0, float(dd or 0.0)) * 0.05
    stability = -abs(float(he) - float(te or 0.0)) * 0.20
    return float(he) * min(1.0, hn / 12.0) + 0.12 * pf_term + 0.10 * float(te or 0.0) * min(1.0, tn / 30.0) + stability - dd_penalty
