from __future__ import annotations
from typing import Any, Dict, Iterable, List, Set, Tuple
from metrics import learning, execution, regime, num


def _band(value, cuts, labels):
    x = num(value)
    if x is None:
        return "NO_DATA"
    for threshold, label in zip(cuts, labels):
        if x < threshold:
            return label
    return labels[-1]


def micro_features(row: Dict[str, Any]) -> Dict[str, str]:
    micro = learning(row).get("microstructure_shadow") or {}
    if not isinstance(micro, dict):
        return {}
    metrics = micro.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    spread_band = _band(metrics.get("spread_pct"), [0.03, 0.10], ["TIGHT","NORMAL","WIDE"])
    liquidity_raw = str(metrics.get("liquidity_band") or "").upper()
    if liquidity_raw not in {"LOW", "NORMAL", "HIGH"}:
        liquidity_raw = {"TIGHT": "HIGH", "NORMAL": "NORMAL", "WIDE": "LOW"}.get(spread_band, "NO_DATA")
    return {
        "micro_alignment": str(micro.get("alignment") or "UNAVAILABLE").upper(),
        "orderbook_imbalance_band": _band(metrics.get("orderbook_imbalance"), [-0.25, 0.25], ["SELL_HEAVY","BALANCED","BUY_HEAVY"]),
        "recent_buy_share_band": _band(metrics.get("recent_buy_share"), [0.40, 0.60], ["SELL_HEAVY","BALANCED","BUY_HEAVY"]),
        "spread_band": spread_band,
        "oi_change_band": _band(metrics.get("oi_change_pct"), [-0.50, 0.50], ["DELEVERAGING","STABLE","BUILDING"]),
        "funding_band": _band(metrics.get("funding_rate"), [-0.0005, 0.0005], ["SHORT_CROWDED","NEUTRAL","LONG_CROWDED"]),
        "basis_band": _band(metrics.get("basis_pct"), [-0.05, 0.05], ["BACKWARDATION","NEUTRAL","CONTANGO"]),
        "liquidity_band": liquidity_raw,
    }


def strategy_tokens(row: Dict[str, Any]) -> Set[str]:
    learn = learning(row)
    tokens: Set[str] = set()
    q7 = learn.get("q7_strategy_lab_shadow") or {}
    if isinstance(q7, dict):
        profile = str(q7.get("active_profile") or "").upper()
        if profile:
            tokens.add(f"Q7_PROFILE:{profile}")
        rsi = q7.get("rsi_profile") or {}
        if isinstance(rsi, dict):
            direction = str(rsi.get("direction") or "").upper()
            align = str(rsi.get("alignment_with_system") or "").upper()
            if direction:
                tokens.add(f"RSI_DIRECTION:{direction}")
            if align:
                tokens.add(f"RSI_ALIGNMENT:{align}")
        for key in ("vwap_reversal", "breakout_retest", "trendline_retest"):
            val = q7.get(key)
            if isinstance(val, dict):
                status = str(val.get("status") or val.get("signal") or val.get("direction") or "").upper()
                if status:
                    tokens.add(f"{key.upper()}:{status}")
    attribution = learn.get("strategy_attribution_v2") or {}
    items = attribution.get("items") if isinstance(attribution, dict) else []
    if isinstance(items, list):
        for item in items[:40]:
            if not isinstance(item, dict):
                continue
            strategy = str(item.get("strategy") or "").strip().upper()
            if strategy:
                tokens.add(f"STRATEGY:{strategy}")
    return tokens


def execution_features(row: Dict[str, Any]) -> Dict[str, str]:
    ex = execution(row)
    out = {
        "direction": str(row.get("action_normalized") or "UNKNOWN").upper(),
        "timeframe": str(row.get("timeframe") or "UNKNOWN").upper(),
        "regime": regime(row),
        "entry_source": str(ex.get("entry_source") or "UNKNOWN").upper(),
        "entry_score_band": _band(ex.get("entry_score"), [55, 70, 80], ["LOW","MEDIUM","HIGH","VERY_HIGH"]),
        "defensibility_band": _band(ex.get("entry_defensibility_score"), [55,70,80], ["LOW","MEDIUM","HIGH","VERY_HIGH"]),
        "reachability_band": _band(ex.get("entry_reachability_score"), [55,70,80], ["LOW","MEDIUM","HIGH","VERY_HIGH"]),
        "sl_quality_band": _band(ex.get("sl_reliability"), [60,75,90], ["LOW","MEDIUM","HIGH","VERY_HIGH"]),
        "tp_quality_band": _band(ex.get("tp_quality_score"), [55,70,80], ["LOW","MEDIUM","HIGH","VERY_HIGH"]),
    }
    out.update(micro_features(row))
    tokens = strategy_tokens(row)
    out["has_order_block"] = "YES" if any("ORDER BLOCK" in t or "ORDER_BLOCK" in t for t in tokens) else "NO"
    out["has_sweep"] = "YES" if any("SWEEP" in t or "LIQUIDITY" in t for t in tokens) else "NO"
    out["has_pullback"] = "YES" if any("PULLBACK" in t or "RETEST" in t for t in tokens) else "NO"
    return out


def trader_items(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    attribution = learning(row).get("strategy_attribution_v2") or {}
    items = attribution.get("items") if isinstance(attribution, dict) else []
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def market_family(row: Dict[str, Any]) -> str:
    system_type = str(row.get("system_type") or "").lower()
    symbol = str(row.get("symbol") or "").upper().replace("/", "-")
    if system_type == "futures":
        return "CRYPTO_FUTURES"
    if symbol in {"PAXG-USDT", "PAXGUSDT"}:
        return "PAXG_USDT"
    if symbol in {"PAXG-BTC", "PAXGBTC"}:
        return "PAXG_BTC"
    return "CRYPTO_SPOT"


def scoped_symbol(row: Dict[str, Any]) -> str:
    """Return the real symbol for learning/attribution.

    Final V1 never grants positive authority from pooled crypto evidence.
    Global aggregates may still be built explicitly for diagnostics, but the
    default research key is market × symbol × timeframe so ETH cannot lend
    reputation/edge to XRP and Spot cannot lend it to Futures.
    """
    symbol = str(row.get("symbol") or "UNKNOWN").upper().replace("/", "-")
    return symbol or "UNKNOWN"
