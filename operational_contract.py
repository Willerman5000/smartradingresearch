"""Commit 9.6 — bounded Research contract for the expanded production universe.

Production can operate 15 Futures assets, but heavy Research intentionally uses
one representative per risk class plus the three Spot portfolio pairs.  A
representative supplies only a class prior; it never turns another symbol into
a local Champion. Backtest/OOS trains priors continuously; local Shadow/live
exists only to confirm execution persistence and detect alpha decay.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

VERSION = "RC9_7_CONTINGENCY_COLDSTART_BACKTEST_PRIMARY"
SPOT_SYMBOLS = ("BTC-USDT", "PAXG-USDT", "PAXG-BTC")
SPOT_TIMEFRAMES = ("4H", "12H", "1D", "1W")

FUTURES_REPRESENTATIVES = {
    "CORE1": ("BTC-USDT", ("30M", "1H", "2H", "4H", "12H", "1D")),
    "CORE2": ("XRP-USDT", ("30M", "1H", "2H", "4H", "12H")),
    "MEDIUM": ("LINK-USDT", ("30M", "1H", "2H", "4H")),
    "HIGH": ("SUI-USDT", ("30M", "1H", "2H")),
}

GROUP_MEMBERS = {
    "CORE1": ("BTC-USDT", "ETH-USDT", "SOL-USDT"),
    "CORE2": ("XRP-USDT", "ADA-USDT"),
    "MEDIUM": ("BNB-USDT", "LINK-USDT", "AVAX-USDT", "NEAR-USDT", "DOT-USDT"),
    "HIGH": ("SUI-USDT", "HYPE-USDT", "APT-USDT", "INJ-USDT", "SEI-USDT"),
}


def _u(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", "-")


def governed_cells() -> List[Tuple[str, str, str, str]]:
    cells: List[Tuple[str, str, str, str]] = []
    for symbol in SPOT_SYMBOLS:
        for tf in SPOT_TIMEFRAMES:
            for action in ("COMPRA_SPOT", "VENTA_SPOT"):
                cells.append(("SPOT", symbol, tf, action))
    for _group, (symbol, timeframes) in FUTURES_REPRESENTATIVES.items():
        for tf in timeframes:
            for action in ("LONG", "SHORT"):
                cells.append(("FUTURES", symbol, tf, action))
    return cells


_CELL_SET = set(governed_cells())


def source_row_in_active_contract(row: Dict[str, Any] | None) -> bool:
    row = dict(row or {})
    system = _u(row.get("system_type"))
    symbol = _u(row.get("symbol"))
    tf = _u(row.get("timeframe"))
    if system == "SPOT":
        return symbol in SPOT_SYMBOLS and tf in SPOT_TIMEFRAMES
    if system == "FUTURES":
        return any(symbol == rep and tf in tfs for rep, tfs in FUTURES_REPRESENTATIVES.values())
    return False


def group_for_symbol(symbol: Any) -> str:
    symbol = _u(symbol)
    for group, members in GROUP_MEMBERS.items():
        if symbol in members:
            return group
    return "UNKNOWN"


def representative_for_symbol(symbol: Any) -> str:
    group = group_for_symbol(symbol)
    row = FUTURES_REPRESENTATIVES.get(group)
    return row[0] if row else ""


def coverage_cell_id_in_active_contract(cell_id: Any) -> bool:
    """Filter coverage_optimizer cells without importing its dataclass here."""
    raw = _u(cell_id).split("|")
    # Current format: SYSTEM|MARKET_FAMILY|SYMBOL|TF|ACTION
    if len(raw) >= 5:
        system, _family, symbol, tf, action = raw[-5:]
    elif len(raw) == 4:
        system, symbol, tf, action = raw
    else:
        return False
    if system in {"CRYPTO_FUTURES", "FUTURES"}:
        system = "FUTURES"
    elif system in {"CRYPTO_SPOT", "SPOT"}:
        system = "SPOT"
    action = _u(action)
    return (system, _u(symbol), _u(tf), action) in _CELL_SET


def audit() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "cells": len(_CELL_SET),
        "spot_cells": sum(1 for c in _CELL_SET if c[0] == "SPOT"),
        "futures_cells": sum(1 for c in _CELL_SET if c[0] == "FUTURES"),
        "expected_cells": 60,
        "ok": len(_CELL_SET) == 60,
        "representatives": {k: v[0] for k, v in FUTURES_REPRESENTATIVES.items()},
    }


if not audit()["ok"]:
    raise RuntimeError(f"Commit 9.6 Research contract mismatch: {audit()}")


def _cell_priority(cell: Tuple[str, str, str, str, str]) -> Tuple[int, int, int, str, str]:
    """Deterministic discovery order without changing the 60-cell contract.

    Priority is about *research scheduling*, not trading authority:
    - Futures fast TFs first because they can improve opportunity frequency.
    - HIGH/MEDIUM representatives first inside fast TFs because their exit
      profiles are intentionally shorter.
    - Spot 4H/12H before 1D/1W so accumulation/rotation timing is learned sooner.
    Both directions remain symmetric; no bullish/bearish bias is introduced.
    """
    system, _family, symbol, tf, action = cell
    tf = _u(tf)
    if str(system).lower() == "futures":
        group = group_for_symbol(symbol)
        tf_rank = {"30M": 0, "1H": 1, "2H": 2, "4H": 3, "12H": 4, "1D": 5}.get(tf, 9)
        group_rank = {"HIGH": 0, "MEDIUM": 1, "CORE2": 2, "CORE1": 3}.get(group, 9)
        return (0, tf_rank, group_rank, _u(symbol), _u(action))
    tf_rank = {"4H": 0, "12H": 1, "1D": 2, "1W": 3}.get(tf, 9)
    return (1, tf_rank, 0, _u(symbol), _u(action))


def optimizer_lanes() -> Dict[str, List[Tuple[str, str, str, str, str]]]:
    """60-cell heavy Research lanes, ordered for fast-edge discovery.

    Validation remains a reader/judge. Existing local Champions outside this
    representative discovery contract stay in Knowledge Core and Shadow. This
    only changes *which pending cell is researched first*; it does not lower
    OOS, Safety, Entry, SL, TP or promotion requirements.
    """
    futures: List[Tuple[str, str, str, str, str]] = []
    for group, (symbol, timeframes) in FUTURES_REPRESENTATIVES.items():
        for tf in timeframes:
            for action in ("LONG", "SHORT"):
                futures.append(("futures", "CRYPTO_FUTURES", symbol, tf, action))
    futures.sort(key=_cell_priority)

    execution = [c for c in futures if c[3] == "30M"]
    risk = [c for c in futures if c[3] in {"1H", "2H"}]
    strategy = [c for c in futures if c[3] in {"4H", "12H", "1D"}]

    traders: List[Tuple[str, str, str, str, str]] = []
    for tf in SPOT_TIMEFRAMES:
        for symbol in SPOT_SYMBOLS:
            family = "PAXG_USDT" if symbol == "PAXG-USDT" else ("PAXG_BTC" if symbol == "PAXG-BTC" else "CRYPTO_SPOT")
            for action in ("COMPRA_SPOT", "VENTA_SPOT"):
                traders.append(("spot", family, symbol, tf, action))
    traders.sort(key=_cell_priority)

    lanes = {"execution": execution, "risk": risk, "strategy": strategy, "traders": traders}
    flat = [c for name in ("execution", "risk", "strategy", "traders") for c in lanes[name]]
    if len(flat) != 60 or len(set(flat)) != 60:
        raise RuntimeError(f"RC9.7 optimizer lane mismatch: total={len(flat)} unique={len(set(flat))}")
    return lanes


def priority_rank_for_coverage_id(cell_id: Any) -> Tuple[int, int, int, str, str]:
    """Expose the same deterministic priority for Validation rescue/pending lists."""
    raw = _u(cell_id).split("|")
    if len(raw) >= 5:
        system, family, symbol, tf, action = raw[-5:]
    elif len(raw) == 4:
        system, symbol, tf, action = raw
        family = "CRYPTO_FUTURES" if system in {"FUTURES", "CRYPTO_FUTURES"} else "CRYPTO_SPOT"
    else:
        return (9, 9, 9, _u(cell_id), "")
    system_norm = "futures" if system in {"FUTURES", "CRYPTO_FUTURES"} else "spot"
    return _cell_priority((system_norm, family, symbol, tf, action))


def research_hold_bars(symbol: Any, timeframe: Any) -> int:
    """Pre-declared exit horizon used by representative heavy Research.

    Faster groups test faster resolution. This changes research geometry, not
    production Safety and not the SL/TP gate.
    """
    tf = _u(timeframe)
    group = group_for_symbol(symbol)
    if group == "HIGH":
        return {"30M": 8, "1H": 6, "2H": 4}.get(tf, 6)
    if group == "MEDIUM":
        return {"30M": 12, "1H": 10, "2H": 8, "4H": 6}.get(tf, 8)
    return {"30M": 24, "1H": 18, "2H": 14, "4H": 10, "12H": 8, "1D": 6, "1W": 4}.get(tf, 18)


def research_wait_bars(symbol: Any, timeframe: Any) -> int:
    tf = _u(timeframe)
    group = group_for_symbol(symbol)
    if group == "HIGH":
        return {"30M": 2, "1H": 2, "2H": 2}.get(tf, 2)
    if group == "MEDIUM":
        return {"30M": 2, "1H": 2, "2H": 2, "4H": 2}.get(tf, 2)
    return {"30M": 3, "1H": 3, "2H": 3, "4H": 2, "12H": 2, "1D": 2, "1W": 1}.get(tf, 3)
