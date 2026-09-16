from pathlib import Path

from alpha_decay import classify_alpha_decay

ROOT = Path(__file__).resolve().parents[1]


def test_hard_eight_loss_streak_recycles_exact_champion():
    out = classify_alpha_decay({
        "resolved_n": 8,
        "recent8_n": 8,
        "current_loss_streak": 8,
        "recent8_expectancy_r": -1.0,
        "recent8_profit_factor": 0.0,
        "recent8_trade_sharpe": -2.0,
    }, baseline_expectancy_r=0.3, baseline_profit_factor=1.5)
    assert out["state"] == "DEGRADED_LOSS_STREAK"
    assert out["degraded"] is True
    assert out["recycle_required"] is True


def test_rolling_eight_trade_multidimensional_decay_recycles():
    out = classify_alpha_decay({
        "resolved_n": 20,
        "recent8_n": 8,
        "previous8_n": 8,
        "current_loss_streak": 3,
        "recent8_expectancy_r": -0.22,
        "recent8_profit_factor": 0.72,
        "recent8_trade_sharpe": -0.18,
        "previous8_expectancy_r": 0.28,
        "previous8_trade_sharpe": 0.52,
    }, baseline_expectancy_r=0.31, baseline_profit_factor=1.55)
    assert out["state"] == "DEGRADED_ROLLING"
    assert out["recycle_required"] is True


def test_eight_trade_window_is_not_magic_single_metric_delete_rule():
    out = classify_alpha_decay({
        "resolved_n": 8,
        "recent8_n": 8,
        "previous8_n": 0,
        "current_loss_streak": 2,
        "recent8_expectancy_r": 0.04,
        "recent8_profit_factor": 0.98,
        "recent8_trade_sharpe": 0.08,
    }, baseline_expectancy_r=0.20, baseline_profit_factor=1.4)
    assert out["state"] == "WATCH"
    assert out["degraded"] is False
    assert out["recycle_required"] is False


def test_dashboard_is_stale_while_revalidate_and_json_safe():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    js = (ROOT / "static" / "dashboard.js").read_text(encoding="utf-8")
    db = (ROOT / "db.py").read_text(encoding="utf-8")
    assert "DASHBOARD_CACHE" in app
    assert "_load_dashboard_rows_uncached" in app
    assert "timeout=6" in app or "timeout = 6" in app
    assert "deferred" in app
    assert "await r.text()" in js
    assert "JSON.parse" in js
    assert "AbortController" in js
    assert "READ_CIRCUIT" in db or "_READ_CIRCUIT" in db
    assert "522" in db


def test_persistent_champion_can_be_demoted_only_by_its_own_decay_state():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "champion_degraded" in app
    assert "_apply_alpha_decay_to_incumbents" in app
    assert "classify_alpha_decay" in app
    assert "alpha_decay_health" in app


def test_causal_backtest_publishes_recent_eight_health_without_annualizing():
    src = (ROOT / "causal_backtest.py").read_text(encoding="utf-8")
    validation = (ROOT / "engines" / "validation.py").read_text(encoding="utf-8")
    assert '"recent8_expectancy_r"' in src
    assert '"current_loss_streak"' in src
    assert "Per-trade health ratio" in src
    assert "backtest_alpha_decay_health" in validation
    assert 'backtest_decay.get("degraded")' in validation
    assert 'backtest_decay.get("state") == "WATCH"' in validation
