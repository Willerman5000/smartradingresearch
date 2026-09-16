from __future__ import annotations

"""RC7 deterministic Strategy Card for causal specialists.

The card is generated only from the exact StrategySpec/scope/metrics already
validated by Research Federation. It never calls an LLM and it never invents
parameters. `raw_spec` is retained verbatim for reproducibility.
"""

import hashlib
import json
from typing import Any, Dict, List

CARD_SCHEMA_VERSION = "RC7_STRATEGY_CARD_V1"

_FAMILY_NAMES = {
    "TREND_CONTINUATION": "Continuación de tendencia",
    "TREND_PULLBACK": "Pullback de tendencia",
    "BREAKOUT_RETEST": "Ruptura y retesteo",
    "MEAN_REVERSION": "Reversión a la media",
    "SWEEP_REVERSAL": "Barrido de liquidez y reversión",
    "EMA_RECLAIM": "Recuperación de EMA",
    "RSI_TREND": "RSI de tendencia",
    "MOMENTUM_BREAKOUT": "Ruptura de momentum",
    "MACD_TREND": "MACD de tendencia",
    "ADX_DI_TREND": "ADX/DMI de tendencia",
    "STOCH_REVERSAL": "Reversión Estocástica",
    "CCI_WILLIAMS_REVERSAL": "Reversión CCI + Williams %R",
    "MFI_REVERSAL": "Reversión MFI",
    "BOLLINGER_REVERSION": "Reversión en Bandas de Bollinger",
    "BOLLINGER_SQUEEZE": "Squeeze de Bollinger",
    "VWAP_REVERSION": "Reversión VWAP",
    "SUPERTREND_PULLBACK": "Pullback Supertrend",
    "PSAR_TREND": "Parabolic SAR de tendencia",
    "RSI_MAVERICK_REVERSAL": "Reversión RSI Maverick",
    "MFI_OBV_FLOW": "Flujo MFI + OBV + Force Index",
    "FIB_RETRACE_TREND": "Retroceso Fibonacci en tendencia",
    "DIVERGENCE_REVERSAL": "Divergencia regular de reversión",
    "HIDDEN_DIVERGENCE_TREND": "Divergencia oculta de continuación",
    "ICHIMOKU_TREND": "Tendencia Ichimoku",
    "VOLUME_PROFILE_RETEST": "Retesteo de POC / Volume Profile",
    "VOLUME_PROFILE_NODE_REACTION": "Reacción HVN/LVN / Volume Profile",
    "FVG_RECLAIM": "Reclaim de Fair Value Gap",
    "MULTI_RSI_TREND": "Triple RSI de tendencia",
}


def _num(v: Any, default: Any = None):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _fmt(v: Any) -> str:
    n = _num(v)
    if n is None:
        return "--"
    if abs(n - round(n)) < 1e-9:
        return str(int(round(n)))
    return f"{n:.4g}"


def _indicators(family: str, spec: Dict[str, Any]) -> List[str]:
    # These baseline features are always computed/used by the causal engine for
    # regime, trend-strength, volatility and/or execution filters.
    out = [
        f"EMA rápida ({spec.get('fast', 12)})",
        f"EMA lenta ({spec.get('slow', 36)})",
        f"RSI ({spec.get('rsi_len', 14)})",
        "ATR (14)",
        "ATR relativo vs media de 100 velas",
        "Volumen vs media de 20 velas",
    ]
    extra = {
        "MACD_TREND": [f"MACD (12,26,{spec.get('signal_period', 9)})"],
        "ADX_DI_TREND": [f"ADX/DMI ({spec.get('aux_period', 14)})"],
        "STOCH_REVERSAL": [f"Estocástico ({spec.get('aux_period', 14)})"],
        "CCI_WILLIAMS_REVERSAL": [f"CCI ({spec.get('aux_period', 20)})", f"Williams %R ({spec.get('aux_period', 20)})"],
        "MFI_REVERSAL": [f"MFI ({spec.get('aux_period', 14)})"],
        "BOLLINGER_REVERSION": [f"Bandas Bollinger (20, {_fmt(spec.get('band_mult', 2.0))}σ)"],
        "BOLLINGER_SQUEEZE": [f"Bandas Bollinger (20, {_fmt(spec.get('band_mult', 2.0))}σ)"],
        "VWAP_REVERSION": ["VWAP móvil (20)"],
        "SUPERTREND_PULLBACK": ["Supertrend (3×ATR)"],
        "PSAR_TREND": ["Parabolic SAR (0.02/0.20)"],
        "RSI_MAVERICK_REVERSAL": [f"RSI Maverick ({spec.get('aux_period', 20)}, banda {_fmt(spec.get('band_mult', 2.0))})"],
        "MFI_OBV_FLOW": [f"MFI ({spec.get('aux_period', 14)})", "OBV + media OBV(20)", "Force Index (13)"],
        "FIB_RETRACE_TREND": [f"Fibonacci sobre lookback {spec.get('lookback', 20)}"],
        "ICHIMOKU_TREND": ["Ichimoku (Tenkan/Kijun/Cloud)"],
        "VOLUME_PROFILE_RETEST": [f"Volume Profile rolling ({spec.get('lookback', 20)}) + POC"],
        "VOLUME_PROFILE_NODE_REACTION": [f"Volume Profile rolling ({spec.get('lookback', 20)}) + HVN/LVN"],
        "MULTI_RSI_TREND": ["RSI rápido (7)", f"RSI medio ({spec.get('rsi_len', 14)})", "RSI lento (21)"],
    }.get(family, [])
    if family in {"DIVERGENCE_REVERSAL", "HIDDEN_DIVERGENCE_TREND"}:
        extra.append(f"Oscilador divergencia: {str(spec.get('indicator') or 'RSI').upper()}")
        extra.append(f"Pivotes causales/lookback: {spec.get('lookback', 20)}")
    if family == "FVG_RECLAIM":
        extra.append(f"Fair Value Gap causal · lookback {spec.get('lookback', 20)}")
    for item in extra:
        if item not in out:
            out.append(item)
    return out


def _signal_logic(fam: str, s: Dict[str, Any]) -> str:
    lo = _fmt(s.get("rsi_low")); hi = _fmt(s.get("rsi_high")); lb = s.get("lookback", 20)
    vm = _fmt(s.get("volume_mult", 1.0)); ind = str(s.get("indicator") or "RSI").upper()
    logic = {
        "TREND_CONTINUATION": f"Sólo en tendencia: cierre del lado de EMA rápida y RSI confirma continuidad (LONG ≥ max(50,{lo}); SHORT ≤ min(50,{hi})).",
        "TREND_PULLBACK": f"Tendencia vigente + retroceso a EMA rápida (±0.20 ATR) + RSI de continuación (LONG ≥ max(43,{lo}); SHORT ≤ min(57,{hi})).",
        "BREAKOUT_RETEST": f"Ruptura causal del máximo/mínimo de {lb} velas con volumen ≥ {vm}× su media.",
        "MEAN_REVERSION": f"Sólo en BALANCE: RSI extremo ({lo}/{hi}) y precio separado al menos 0.45 ATR de EMA rápida.",
        "SWEEP_REVERSAL": f"Barrido del extremo de {lb} velas y cierre de vuelta dentro del rango; RSI evita perseguir el movimiento.",
        "EMA_RECLAIM": "Tendencia vigente + recuperación/perdida de EMA rápida respecto de la vela previa; RSI ≥48 para LONG o ≤52 para SHORT.",
        "RSI_TREND": f"Tendencia vigente + cierre del lado de EMA rápida + RSI cruza umbral validado ({lo}/{hi}).",
        "MOMENTUM_BREAKOUT": f"Tendencia vigente + ruptura del high/low previo + RSI ({lo}/{hi}) + volumen ≥ {vm}× media.",
        "MACD_TREND": "Tendencia vigente + MACD del lado de su señal y histograma acelerando en la dirección del trade.",
        "ADX_DI_TREND": f"ADX ≥ {hi} + dominancia +DI/-DI coherente + cierre del lado de EMA rápida.",
        "STOCH_REVERSAL": f"Sólo en BALANCE: Estocástico extremo ({lo}/{hi}) y vela confirma giro.",
        "CCI_WILLIAMS_REVERSAL": f"Sólo en BALANCE: CCI extremo ±{hi} junto a Williams %R extremo (-80/-20).",
        "MFI_REVERSAL": f"Sólo en BALANCE: MFI extremo ({lo}/{hi}) y vela confirma giro.",
        "BOLLINGER_REVERSION": "Sólo en BALANCE: mecha toca/supera banda y el cierre recupera el interior de Bollinger.",
        "BOLLINGER_SQUEEZE": f"Expansión del ancho Bollinger + cierre fuera de banda previa + volumen ≥ {vm}× media.",
        "VWAP_REVERSION": "Sólo en BALANCE: exceso ≥0.45 ATR respecto a VWAP y cierre recupera VWAP.",
        "SUPERTREND_PULLBACK": "Supertrend alineado + pullback a EMA rápida + RSI de confirmación (48/52).",
        "PSAR_TREND": "Parabolic SAR alineado; admite cambio de lado del SAR o continuidad con régimen tendencial.",
        "RSI_MAVERICK_REVERSAL": f"Sólo en BALANCE: RSI Maverick revierte desde banda validada {lo}/{hi} y el precio confirma giro.",
        "MFI_OBV_FLOW": "Tendencia + OBV respecto de su media + MFI (≥55/≤45) + Force Index con signo coherente.",
        "FIB_RETRACE_TREND": f"Tendencia vigente + retroceso Fibonacci entre 0.35 y 0.66 del rango causal de {lb} velas + vela confirma reanudación.",
        "DIVERGENCE_REVERSAL": f"Divergencia REGULAR en {ind} dentro de lookback {lb}; se permite BALANCE o tendencia contraria agotándose.",
        "HIDDEN_DIVERGENCE_TREND": f"Divergencia OCULTA en {ind} dentro de lookback {lb}, sólo a favor del régimen de tendencia.",
        "ICHIMOKU_TREND": "Cierre fuera de la nube Ichimoku y Tenkan/Kijun alineados en la misma dirección.",
        "VOLUME_PROFILE_RETEST": "Tendencia + precio retestea POC rolling y cierra nuevamente del lado de continuación.",
        "VOLUME_PROFILE_NODE_REACTION": "Reacción cercana a HVN (≤0.35 ATR) o ruptura de LVN en la dirección resultante.",
        "FVG_RECLAIM": f"Tendencia + retorno al FVG causal de las últimas {lb} velas y reclaim de al menos su zona media.",
        "MULTI_RSI_TREND": "Tendencia + RSI 7/14/21 alineados (LONG >55/>52/>50; SHORT <45/<48/<50).",
    }
    return logic.get(fam, f"Reglas causales exactas de la familia {fam}; ver raw_spec para los parámetros congelados.")


def _volatility_rule(mode: str) -> str:
    mode = str(mode or "ANY").upper()
    return {
        "ANY": "Sin filtro adicional de volatilidad.",
        "QUIET": "ATR relativo / media ATR relativa(100) ≤ 0.82.",
        "NORMAL": "ATR relativo / media ATR relativa(100) entre 0.72 y 1.35.",
        "EXPANSION": "ATR relativo / media ATR relativa(100) ≥ 1.18.",
    }.get(mode, f"Modo de volatilidad: {mode}.")


def build_strategy_card(*, strategy_id: str, scope: Dict[str, Any], spec: Dict[str, Any], metrics: Dict[str, Any], stage: str, updated_at: Any = None) -> Dict[str, Any]:
    spec = dict(spec or {})
    scope = dict(scope or {})
    fam = str(spec.get("family") or "UNKNOWN").upper()
    val = (metrics or {}).get("validation") or {}
    allm = (metrics or {}).get("all") or {}
    technical_spec_key = (
        f"{fam}:{spec.get('direction_mode','BOTH')}:f{spec.get('fast',12)}:s{spec.get('slow',36)}:"
        f"r{spec.get('rsi_len',14)}:{_fmt(spec.get('rsi_low',32))}-{_fmt(spec.get('rsi_high',68))}:"
        f"lb{spec.get('lookback',20)}:v{_fmt(spec.get('volume_mult',1))}:{spec.get('entry_style','NEXT_OPEN')}:"
        f"{_fmt(spec.get('entry_atr',.25))}:sl{_fmt(spec.get('sl_atr',1.5))}:rr{_fmt(spec.get('rr',2))}:"
        f"w{spec.get('max_wait',3)}:h{spec.get('max_hold',24)}:vol{spec.get('volatility_mode','ANY')}:"
        f"ts{_fmt(spec.get('trend_strength_min',0))}:ind{spec.get('indicator','RSI')}:aux{spec.get('aux_period',14)}:"
        f"sig{spec.get('signal_period',9)}:bm{_fmt(spec.get('band_mult',2))}:div{spec.get('divergence_mode','NONE')}"
    )
    immutable_payload = {
        "schema": CARD_SCHEMA_VERSION,
        "strategy_id": str(strategy_id or ""),
        "scope": scope,
        "spec": spec,
        "technical_spec_key": technical_spec_key,
    }
    fp = hashlib.sha256(json.dumps(immutable_payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    entry_style = str(spec.get("entry_style") or "NEXT_OPEN").upper()
    if entry_style == "PULLBACK":
        entry_rule = (
            f"PULLBACK: orden a {_fmt(spec.get('entry_atr', .25))} ATR desde el cierre de señal "
            f"en dirección de retroceso; esperar máximo {int(spec.get('max_wait',3) or 3)} velas."
        )
    else:
        entry_rule = f"NEXT_OPEN: entrada en la apertura de la siguiente vela; espera máxima {int(spec.get('max_wait',3) or 3)} velas."

    regime = str(scope.get("regime") or "ALL").upper()
    stage_u = str(stage or "").upper()
    return {
        "schema_version": CARD_SCHEMA_VERSION,
        "immutable": stage_u in {"SHADOW_READY", "SHADOW_READY_FAST"},
        "fingerprint_sha256": fp,
        "technical_id": str(strategy_id or ""),
        "technical_spec_key": technical_spec_key,
        "display_name": _FAMILY_NAMES.get(fam, fam.replace("_", " ").title()),
        "family": fam,
        "market_family": scope.get("market_family"),
        "symbol": scope.get("symbol"),
        "timeframe": scope.get("timeframe"),
        "direction": scope.get("direction") or spec.get("direction_mode") or "BOTH",
        "valid_regime": regime,
        "indicators": _indicators(fam, spec),
        "signal_logic": _signal_logic(fam, spec),
        "entry": {
            "style": entry_style,
            "entry_atr": _num(spec.get("entry_atr")),
            "max_wait_bars": int(spec.get("max_wait", 3) or 3),
            "rule": entry_rule,
        },
        "risk": {
            "stop_atr": _num(spec.get("sl_atr")),
            "stop_rule": f"Distancia de SL = max({_fmt(spec.get('sl_atr',1.5))} × ATR(14), 0.05% del precio de entrada).",
            "rr": _num(spec.get("rr")),
            "take_profit_rule": f"TP = {_fmt(spec.get('rr',2))}R desde Entry según la distancia real de SL.",
            "max_hold_bars": int(spec.get("max_hold", 24) or 24),
        },
        "filters": {
            "volatility_mode": str(spec.get("volatility_mode") or "ANY").upper(),
            "volatility_rule": _volatility_rule(spec.get("volatility_mode")),
            "trend_strength_min": _num(spec.get("trend_strength_min"), 0.0),
            "trend_strength_rule": f"|EMA rápida − EMA lenta| / ATR ≥ {_fmt(spec.get('trend_strength_min',0))}.",
            "volume_mult": _num(spec.get("volume_mult"), 1.0),
        },
        "evidence": {
            "backtest_n": int(allm.get("resolved") or 0),
            "backtest_wr_pct": _num(allm.get("win_rate_pct")),
            "backtest_expectancy_r": _num(allm.get("expectancy_r")),
            "oos_n": int(val.get("resolved") or 0),
            "oos_wr_pct": _num(val.get("win_rate_pct")),
            "oos_expectancy_r": _num(val.get("expectancy_r")),
            "oos_profit_factor": _num(val.get("profit_factor")),
            "walk_forward": (metrics or {}).get("walk_forward") or {},
        },
        "validation_stage": stage_u,
        "source_updated_at": updated_at,
        "raw_spec": spec,
        "generated_by": "DETERMINISTIC_RESEARCH_CONFIG",
        "uses_llm": False,
    }
