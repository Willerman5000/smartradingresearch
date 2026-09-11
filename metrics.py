from __future__ import annotations
import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def latest_result(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = row.get("signal_results") or []
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return raw[0]
    if isinstance(raw, dict):
        return raw
    return {}


def result_status(row: Dict[str, Any]) -> str:
    result = latest_result(row)
    return str(result.get("status") or row.get("status") or "").strip().lower()


def realized_r(row: Dict[str, Any]) -> Tuple[Optional[float], str]:
    """Return R and evidence source. Never pretends gross is net."""
    result = latest_result(row)
    for key in ("modeled_net_r", "gross_r"):
        value = num(result.get(key))
        if value is not None:
            return value, key
    status = result_status(row)
    rr = num(row.get("risk_reward"), 1.0) or 1.0
    if status in {"tp_hit", "win_tp", "win_protected", "tp"}:
        return max(0.01, rr), "geometry_fallback"
    if status in {"sl_hit", "loss_sl", "sl"}:
        return -1.0, "geometry_fallback"
    return None, "unresolved"


def forensics(row: Dict[str, Any]) -> Dict[str, Any]:
    raw = latest_result(row).get("execution_forensics") or {}
    return raw if isinstance(raw, dict) else {}


def learning(row: Dict[str, Any]) -> Dict[str, Any]:
    ctx = row.get("context") or {}
    if not isinstance(ctx, dict):
        return {}
    value = ctx.get("learning") or {}
    return value if isinstance(value, dict) else {}


def execution(row: Dict[str, Any]) -> Dict[str, Any]:
    ctx = row.get("context") or {}
    if not isinstance(ctx, dict):
        return {}
    value = ctx.get("execution") or {}
    return value if isinstance(value, dict) else {}


def regime(row: Dict[str, Any]) -> str:
    q = learning(row).get("quantitative_shadow") or {}
    if isinstance(q, dict):
        return str(q.get("regime") or "UNKNOWN").upper()
    return "UNKNOWN"


def temporal_split(rows: Sequence[Dict[str, Any]], train_ratio: float = 0.70, embargo_ratio: float = 0.02):
    ordered = sorted(rows, key=lambda r: str(r.get("created_at") or ""))
    n = len(ordered)
    if n < 3:
        return list(ordered), []
    cut = max(1, min(n - 1, int(n * train_ratio)))
    embargo = max(1, int(n * embargo_ratio)) if n >= 25 else 0
    train = ordered[:max(1, cut - embargo)]
    validation = ordered[min(n, cut + embargo):]
    return train, validation


def _max_drawdown(values: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    rs: List[float] = []
    wins = losses = 0
    source_counts: Dict[str, int] = {}
    mfe_values: List[float] = []
    mae_values: List[float] = []
    symbols = set()
    timeframes = set()
    for row in rows:
        r, source = realized_r(row)
        if r is None:
            continue
        rs.append(r)
        source_counts[source] = source_counts.get(source, 0) + 1
        wins += int(r > 0)
        losses += int(r < 0)
        symbols.add(str(row.get("symbol") or "UNKNOWN"))
        timeframes.add(str(row.get("timeframe") or "UNKNOWN"))
        fx = forensics(row)
        mfe = num(fx.get("mfe_r"), num(latest_result(row).get("mfe_r")))
        mae = num(fx.get("mae_r"), num(latest_result(row).get("mae_r")))
        if mfe is not None:
            mfe_values.append(mfe)
        if mae is not None:
            mae_values.append(mae)
    positives = sum(v for v in rs if v > 0)
    negatives = abs(sum(v for v in rs if v < 0))
    pf = (positives / negatives) if negatives > 1e-12 else (999.0 if positives > 0 else None)
    return {
        "n_rows": len(rows),
        "resolved": len(rs),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / len(rs) * 100.0, 2) if rs else None,
        "expectancy_r": round(sum(rs) / len(rs), 5) if rs else None,
        "profit_factor": round(pf, 4) if pf is not None else None,
        "max_drawdown_r": round(_max_drawdown(rs), 4) if rs else None,
        "avg_mfe_r": round(sum(mfe_values)/len(mfe_values), 4) if mfe_values else None,
        "avg_mae_r": round(sum(mae_values)/len(mae_values), 4) if mae_values else None,
        "symbols": sorted(symbols),
        "timeframes": sorted(timeframes),
        "r_source_counts": source_counts,
        "net_evidence_count": int(source_counts.get("modeled_net_r", 0)),
        "net_evidence_pct": round(source_counts.get("modeled_net_r", 0) / len(rs) * 100.0, 2) if rs else 0.0,
    }


def summarize_oos(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    train, validation = temporal_split(rows)
    return {"all": summarize(rows), "train": summarize(train), "validation": summarize(validation)}


def evidence_stage(summary: Dict[str, Any]) -> str:
    allm = summary.get("all") or {}
    val = summary.get("validation") or {}
    n = int(allm.get("resolved") or 0)
    vn = int(val.get("resolved") or 0)
    vexp = num(val.get("expectancy_r"))
    vpf = num(val.get("profit_factor"))
    if vn >= 10 and vexp is not None and vexp <= -0.20:
        return "DEGRADED_OOS"
    if n >= 25 and vn >= 10 and vexp is not None and vexp > 0.05 and (vpf is None or vpf > 1.10):
        return "PROMISING_OOS"
    if n >= 10:
        return "NEEDS_OOS"
    return "INSUFFICIENT"
