from __future__ import annotations

"""Research-only exchange reserve/flow context.

Free/public historical CEX-flow coverage is not assumed. This collector is
therefore CURRENT_CONTEXT_ONLY and can never be promoted as causal evidence.
It optionally parses DefiLlama's public CEX transparency page. If the page
changes or is unavailable, it fails open and produces no finding.
"""

import re
from typing import Any, Dict, List
import requests

import config
from db import utc_now

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "SmartradingResearch/1.6 exchange-flow-context"})


def _money(text: str) -> float | None:
    raw = str(text or "").strip().replace("$", "").replace(",", "")
    m = re.match(r"^(-?)([0-9]+(?:\.[0-9]+)?)([kmbt]?)$", raw, re.I)
    if not m:
        return None
    sign = -1.0 if m.group(1) else 1.0
    value = float(m.group(2)); suffix = m.group(3).lower()
    value *= {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(suffix, 1.0)
    return sign * value


def current_exchange_flow_finding() -> Dict[str, Any] | None:
    if not bool(getattr(config, "EXCHANGE_FLOW_ENABLED", True)):
        return None
    try:
        r = _SESSION.get("https://defillama.com/cexs", timeout=12)
        r.raise_for_status()
        text = re.sub(r"\s+", " ", r.text)
        rows = []
        for name in ("Binance", "OKX", "Bybit", "Bitfinex", "KuCoin"):
            # Deliberately loose: current public page is context only, never a
            # causal backtest source. Capture a short window after exchange name.
            m = re.search(re.escape(name) + r"(.{0,700})", text, re.I)
            if not m:
                continue
            dollars = re.findall(r"-?\$[0-9][0-9,]*(?:\.[0-9]+)?\s*[kKmMbBtT]?", m.group(1))
            parsed = [_money(x.replace(" ", "")) for x in dollars]
            parsed = [x for x in parsed if x is not None]
            if len(parsed) >= 4:
                rows.append({"exchange": name, "assets_usd": parsed[0], "clean_assets_usd": parsed[1], "inflow_24h_usd": parsed[2], "inflow_7d_usd": parsed[3]})
        if not rows:
            return None
        total_24h = sum(float(x.get("inflow_24h_usd") or 0.0) for x in rows)
        total_7d = sum(float(x.get("inflow_7d_usd") or 0.0) for x in rows)
        if total_24h > 250_000_000:
            state = "NET_INFLOW_PRESSURE"
        elif total_24h < -250_000_000:
            state = "NET_OUTFLOW_PRESSURE"
        else:
            state = "NEUTRAL"
        return {
            "engine": config.ENGINE,
            "experiment": "EXCHANGE_FLOW_CONTEXT",
            "feature_key": f"{config.ENGINE}:EXCHANGE_FLOW_CONTEXT:GLOBAL_CEX",
            "scope": {"market_family": "CRYPTO_FUTURES", "exchange_flow_state": state},
            "stage": "OBSERVE",
            "authority": "RESEARCH_ONLY",
            "metrics": {"all": {"resolved": 0, "net_evidence_pct": 0.0}, "validation": {"resolved": 0}, "walk_forward": {}},
            "meta": {
                "current_context_only": True,
                "historical_causal_backtest": False,
                "source": "DEFILLAMA_PUBLIC_CEX_PAGE",
                "exchanges": rows,
                "aggregate_inflow_24h_usd": round(total_24h, 2),
                "aggregate_inflow_7d_usd": round(total_7d, 2),
                "interpretation": state,
                "production_changes_allowed": False,
                "runtime_trackable": False,
                "note": "No equivale a compras/ventas propias del exchange; puede incluir depósitos, retiros, custodia o movimientos internos.",
            },
            "research_version": config.VERSION,
            "updated_at": utc_now(),
        }
    except Exception as exc:
        print(f"⚠️ [I EXCHANGE FLOW] fail-open: {str(exc)[:160]}", flush=True)
        return None
