from __future__ import annotations

"""Bounded public market-history loader used only by Research Federation.

No pandas/numpy are used: candles are compact tuples so each free Render research
service can spend its RAM on historical coverage rather than dataframe overhead.
The cache is process-local and bounded. A restart simply refetches public data.
"""

from collections import OrderedDict
import threading
import time
from typing import Dict, List, NamedTuple, Tuple

import requests

import config
from engines.base import rss_mb


class Candle(NamedTuple):
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


_SPOT_INTERVAL = {
    "5M": "5min", "15M": "15min", "30M": "30min", "1H": "1hour",
    "2H": "2hour", "4H": "4hour", "12H": "12hour", "1D": "1day", "1W": "1week",
}
_FUTURES_GRANULARITY = {
    "5M": 5, "15M": 15, "30M": 30, "1H": 60, "2H": 120, "4H": 240,
}
_TF_SECONDS = {
    "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "2H": 7200,
    "4H": 14400, "12H": 43200, "1D": 86400, "1W": 604800,
}
_FUTURES_CONTRACT = {
    "BTC-USDT": "XBTUSDTM", "ETH-USDT": "ETHUSDTM", "SOL-USDT": "SOLUSDTM",
    "XRP-USDT": "XRPUSDTM", "ADA-USDT": "ADAUSDTM", "LINK-USDT": "LINKUSDTM",
    "BNB-USDT": "BNBUSDTM",
}

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "SmartradingResearch/1.6 causal-backtest"})
_CACHE: "OrderedDict[Tuple[str,str,str], List[Candle]]" = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = threading.Lock()
# Conservative approximation for tuple+float/int references in CPython.
_APPROX_CANDLE_BYTES = 240


def normalize_tf(value: str) -> str:
    raw = str(value or "").strip().upper()
    aliases = {"1DAY": "1D", "1WEEK": "1W", "60M": "1H", "120M": "2H", "240M": "4H"}
    return aliases.get(raw, raw)


def tf_seconds(tf: str) -> int:
    return int(_TF_SECONDS.get(normalize_tf(tf), 3600))


def _cache_limit_bytes() -> int:
    return max(16, int(getattr(config, "CAUSAL_CACHE_MB", 220))) * 1024 * 1024


def _cache_get(key: Tuple[str, str, str], bars: int) -> List[Candle] | None:
    with _CACHE_LOCK:
        value = _CACHE.get(key)
        if value and len(value) >= min(20, bars):
            _CACHE.move_to_end(key)
            return list(value[-bars:])
    return None


def _cache_put(key: Tuple[str, str, str], candles: List[Candle]) -> None:
    global _CACHE_BYTES
    if not candles:
        return
    with _CACHE_LOCK:
        old = _CACHE.pop(key, None)
        if old:
            _CACHE_BYTES = max(0, _CACHE_BYTES - len(old) * _APPROX_CANDLE_BYTES)
        _CACHE[key] = list(candles)
        _CACHE_BYTES += len(candles) * _APPROX_CANDLE_BYTES
        limit = _cache_limit_bytes()
        while _CACHE and _CACHE_BYTES > limit:
            _, evicted = _CACHE.popitem(last=False)
            _CACHE_BYTES = max(0, _CACHE_BYTES - len(evicted) * _APPROX_CANDLE_BYTES)


def cache_stats() -> Dict[str, float | int]:
    with _CACHE_LOCK:
        return {
            "series": len(_CACHE),
            "approx_mb": round(_CACHE_BYTES / (1024 * 1024), 2),
            "limit_mb": int(getattr(config, "CAUSAL_CACHE_MB", 220)),
        }


def _pause() -> None:
    ms = max(0, int(getattr(config, "CAUSAL_HTTP_PAUSE_MS", 80)))
    if ms:
        time.sleep(ms / 1000.0)


def _float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def fetch_spot(symbol: str, timeframe: str, bars: int) -> List[Candle]:
    tf = normalize_tf(timeframe)
    ktype = _SPOT_INTERVAL.get(tf)
    if not ktype:
        return []
    symbol = str(symbol or "").upper().replace("/", "-")
    key = ("spot", symbol, tf)
    hit = _cache_get(key, bars)
    if hit is not None:
        return hit

    page_max = 1500
    sec = tf_seconds(tf)
    end_at = int(time.time())
    out: Dict[int, Candle] = {}
    max_pages = max(1, min(80, (int(bars) + page_max - 1) // page_max + 2))
    for _ in range(max_pages):
        if len(out) >= bars:
            break
        need = min(page_max, max(50, bars - len(out)))
        start_at = max(0, end_at - sec * (need + 10))
        r = _SESSION.get(
            "https://api.kucoin.com/api/v1/market/candles",
            params={"symbol": symbol, "type": ktype, "startAt": start_at, "endAt": end_at},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json() if r.content else {}
        rows = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        oldest = None
        for raw in rows:
            if not isinstance(raw, (list, tuple)) or len(raw) < 6:
                continue
            # Spot: time, open, close, high, low, volume, turnover.
            ts = int(_float(raw[0]))
            c = Candle(ts, _float(raw[1]), _float(raw[3]), _float(raw[4]), _float(raw[2]), _float(raw[5]))
            out[ts] = c
            oldest = ts if oldest is None else min(oldest, ts)
        if oldest is None or oldest >= end_at:
            break
        end_at = oldest - 1
        _pause()
        if rss_mb() >= getattr(config, "CAUSAL_MEMORY_TARGET_MB", 390):
            break

    candles = sorted(out.values(), key=lambda x: x.ts)[-bars:]
    _cache_put(key, candles)
    return candles


def fetch_futures(symbol: str, timeframe: str, bars: int) -> List[Candle]:
    tf = normalize_tf(timeframe)
    gran = _FUTURES_GRANULARITY.get(tf)
    if not gran:
        return []
    symbol = str(symbol or "").upper().replace("/", "-")
    contract = _FUTURES_CONTRACT.get(symbol)
    if not contract:
        return []
    key = ("futures", symbol, tf)
    hit = _cache_get(key, bars)
    if hit is not None:
        return hit

    page_max = 500
    ms = tf_seconds(tf) * 1000
    end_ms = int(time.time() * 1000)
    out: Dict[int, Candle] = {}
    max_pages = max(1, min(90, (int(bars) + page_max - 1) // page_max + 2))
    for _ in range(max_pages):
        if len(out) >= bars:
            break
        start_ms = max(0, end_ms - ms * (page_max + 10))
        r = _SESSION.get(
            "https://api-futures.kucoin.com/api/v1/kline/query",
            params={"symbol": contract, "granularity": gran, "from": start_ms, "to": end_ms},
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json() if r.content else {}
        rows = payload.get("data") if isinstance(payload, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        oldest = None
        for raw in rows:
            if not isinstance(raw, (list, tuple)) or len(raw) < 6:
                continue
            # Futures: time(ms), open, high, low, close, volume, turnover.
            ts_ms = int(_float(raw[0]))
            ts = ts_ms // 1000
            c = Candle(ts, _float(raw[1]), _float(raw[2]), _float(raw[3]), _float(raw[4]), _float(raw[5]))
            out[ts] = c
            oldest = ts_ms if oldest is None else min(oldest, ts_ms)
        if oldest is None or oldest >= end_ms:
            break
        end_ms = oldest - 1
        _pause()
        if rss_mb() >= getattr(config, "CAUSAL_MEMORY_TARGET_MB", 390):
            break

    candles = sorted(out.values(), key=lambda x: x.ts)[-bars:]
    _cache_put(key, candles)
    return candles


def fetch_market(system_type: str, symbol: str, timeframe: str, bars: int) -> List[Candle]:
    if str(system_type or "").lower() == "spot":
        return fetch_spot(symbol, timeframe, bars)
    return fetch_futures(symbol, timeframe, bars)
