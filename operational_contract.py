"""RC9.2 governed operational universe shared conceptually with Main.

Research remains evidence-only. This file prevents Research from drifting away
from the exact production cells that Main knows how to operate.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

VERSION = "RC9_2_OPERATIONAL_CONTRACT_V1"
SPOT_SYMBOLS = ("BTC-USDT", "PAXG-USDT", "PAXG-BTC")
SPOT_TIMEFRAMES = ("4H", "12H", "1D", "1W")
FUTURES_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT", "BNB-USDT", "LINK-USDT")
FUTURES_CORE_TIMEFRAMES = ("30M", "1H", "2H", "4H")
FUTURES_HTF_SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
FUTURES_HTF_TIMEFRAMES = ("12H", "1D")


def _u(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", "-")


def governed_cells() -> List[Tuple[str, str, str, str]]:
    cells: List[Tuple[str, str, str, str]] = []
    for symbol in SPOT_SYMBOLS:
        for tf in SPOT_TIMEFRAMES:
            for action in ("COMPRA_SPOT", "VENTA_SPOT"):
                cells.append(("SPOT", symbol, tf, action))
    for symbol in FUTURES_SYMBOLS:
        for tf in FUTURES_CORE_TIMEFRAMES:
            for action in ("LONG", "SHORT"):
                cells.append(("FUTURES", symbol, tf, action))
    for symbol in FUTURES_HTF_SYMBOLS:
        for tf in FUTURES_HTF_TIMEFRAMES:
            for action in ("LONG", "SHORT"):
                cells.append(("FUTURES", symbol, tf, action))
    return cells


_CELL_SET = set(governed_cells())


def source_row_in_active_contract(row: Dict[str, Any] | None) -> bool:
    row = dict(row or {})
    system = _u(row.get("system_type"))
    symbol = _u(row.get("symbol"))
    tf = _u(row.get("timeframe"))
    if system == "FUTURES":
        if tf in FUTURES_CORE_TIMEFRAMES:
            return symbol in FUTURES_SYMBOLS
        if tf in FUTURES_HTF_TIMEFRAMES:
            return symbol in FUTURES_HTF_SYMBOLS
        return False
    if system == "SPOT":
        return symbol in SPOT_SYMBOLS and tf in SPOT_TIMEFRAMES
    return False


def audit() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "cells": len(_CELL_SET),
        "spot_cells": sum(1 for c in _CELL_SET if c[0] == "SPOT"),
        "futures_cells": sum(1 for c in _CELL_SET if c[0] == "FUTURES"),
        "ok": len(_CELL_SET) == 92,
    }


if not audit()["ok"]:
    raise RuntimeError(f"Operational contract mismatch: {audit()}")
