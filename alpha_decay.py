from __future__ import annotations

"""RC8 Alpha Decay Shield.

Pure classification only: no strategy is promoted from this module. It watches
one frozen Champion lineage/cell at a time and decides whether live Shadow
performance is still statistically compatible with the historical edge.
"""

from typing import Any, Dict, Optional

VERSION = "RC8_ALPHA_DECAY_SHIELD_V1"
WINDOW = 8
HARD_LOSS_STREAK = 8


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(value)
        return x if x == x and abs(x) != float("inf") else default
    except Exception:
        return default


def classify_alpha_decay(
    live: Dict[str, Any] | None,
    *,
    baseline_expectancy_r: Any = None,
    baseline_profit_factor: Any = None,
) -> Dict[str, Any]:
    """Classify decay using the exact Champion's resolved Shadow sequence.

    Eight trades is deliberately a *monitoring window*, not a magic optimizer.
    A hard 8-loss streak is enough to fail closed. Rolling deterioration needs
    multiple bad dimensions (expectancy plus PF/Sharpe plus trajectory/baseline)
    so one noisy small sample does not erase a valid strategy.
    """
    live = live or {}
    n = int(live.get("resolved_n") or 0)
    recent_n = int(live.get("recent8_n") or 0)
    prev_n = int(live.get("previous8_n") or 0)
    loss_streak = int(live.get("current_loss_streak") or 0)
    recent_exp = _num(live.get("recent8_expectancy_r"))
    recent_pf = _num(live.get("recent8_profit_factor"))
    recent_sharpe = _num(live.get("recent8_trade_sharpe"))
    prev_exp = _num(live.get("previous8_expectancy_r"))
    prev_sharpe = _num(live.get("previous8_trade_sharpe"))
    base_exp = _num(baseline_expectancy_r)
    base_pf = _num(baseline_profit_factor)

    result = {
        "version": VERSION,
        "state": "INSUFFICIENT",
        "degraded": False,
        "recycle_required": False,
        "resolved_n": n,
        "window": WINDOW,
        "recent_n": recent_n,
        "previous_n": prev_n,
        "current_loss_streak": loss_streak,
        "recent_expectancy_r": recent_exp,
        "recent_profit_factor": recent_pf,
        "recent_trade_sharpe": recent_sharpe,
        "previous_expectancy_r": prev_exp,
        "previous_trade_sharpe": prev_sharpe,
        "baseline_expectancy_r": base_exp,
        "baseline_profit_factor": base_pf,
        "reason": "Aún no hay 8 cierres Shadow del Champion para medir decay.",
    }

    if loss_streak >= HARD_LOSS_STREAK:
        result.update(
            state="DEGRADED_LOSS_STREAK",
            degraded=True,
            recycle_required=True,
            reason=(
                f"Alpha decay: {loss_streak} pérdidas consecutivas del mismo Champion. "
                "Se bloquea continuidad positiva y la celda vuelve a investigación/Shadow."
            ),
        )
        return result

    if recent_n < WINDOW:
        if loss_streak >= 4:
            result.update(
                state="WATCH",
                reason=f"Racha adversa de {loss_streak} pérdidas; todavía falta completar la ventana de 8 cierres.",
            )
        return result

    recent_bad = bool(
        recent_exp is not None and recent_exp < 0.0
        and (
            (recent_pf is not None and recent_pf < 0.90)
            or (recent_sharpe is not None and recent_sharpe < 0.0)
        )
    )
    trajectory_bad = bool(
        prev_n >= 6
        and (
            (recent_sharpe is not None and prev_sharpe is not None and recent_sharpe <= prev_sharpe - 0.25)
            or (recent_exp is not None and prev_exp is not None and recent_exp <= prev_exp - 0.15)
        )
    )
    baseline_bad = bool(
        base_exp is not None and base_exp > 0.0
        and recent_exp is not None
        and recent_exp <= min(0.0, base_exp * 0.25)
    )

    if recent_bad and (trajectory_bad or baseline_bad):
        result.update(
            state="DEGRADED_ROLLING",
            degraded=True,
            recycle_required=True,
            reason=(
                "Alpha decay: la última ventana de 8 trades tiene expectancy negativa y "
                "deterioro simultáneo de PF/Sharpe frente a la trayectoria o al OOS. "
                "El Champion se retira de continuidad y vuelve al ciclo iterativo."
            ),
        )
        return result

    watch = bool(
        (recent_exp is not None and base_exp is not None and base_exp > 0 and recent_exp < base_exp * 0.50)
        or (recent_sharpe is not None and recent_sharpe <= 0.10)
        or (recent_pf is not None and recent_pf < 1.05)
        or trajectory_bad
    )
    if watch:
        result.update(
            state="WATCH",
            reason=(
                "El edge reciente se ha debilitado. Se mantiene en observación sin aumentar autoridad/riesgo; "
                "un deterioro confirmado lo reciclará."
            ),
        )
    else:
        result.update(
            state="HEALTHY",
            reason="La ventana reciente no muestra evidencia suficiente de alpha decay.",
        )
    return result
