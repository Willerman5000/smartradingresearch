from pathlib import Path

import config
from strategy_card import build_strategy_card, CARD_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]


def _sample_card(stage="SHADOW_READY"):
    spec = {
        "family": "RSI_TREND", "direction_mode": "SHORT",
        "fast": 9, "slow": 21, "rsi_len": 14,
        "rsi_low": 42, "rsi_high": 58, "lookback": 20,
        "volume_mult": 1.0, "entry_style": "PULLBACK",
        "entry_atr": 0.30, "sl_atr": 1.50, "rr": 2.25,
        "max_wait": 3, "max_hold": 18,
        "volatility_mode": "NORMAL", "trend_strength_min": 0.25,
        "indicator": "RSI", "aux_period": 14,
        "signal_period": 9, "band_mult": 2.0,
        "divergence_mode": "NONE",
    }
    scope = {
        "market_family": "CRYPTO_FUTURES", "symbol": "ETH-USDT",
        "timeframe": "4H", "direction": "SHORT", "regime": "TREND_DOWN",
    }
    metrics = {
        "all": {"resolved": 96, "win_rate_pct": 55.0, "expectancy_r": 0.40},
        "validation": {"resolved": 20, "win_rate_pct": 55.0, "expectancy_r": 0.35585, "profit_factor": 1.7283},
        "walk_forward": {"positive_folds": 3},
    }
    return build_strategy_card(
        strategy_id="CI_EXAMPLE", scope=scope, spec=spec,
        metrics=metrics, stage=stage, updated_at="2026-09-16T00:00:00Z",
    ), spec


def test_rc7_fast5_changes_scheduler_only_not_research_generation():
    assert config.BOOTSTRAP_FAST_MINUTES == 5
    assert config.VERSION == "RFV1_13_RC8_1_ACTION_EDGE_92CELL_20260916"
    assert config.SCHEDULER_POLICY_VERSION == "RC8_1_FAST5_ACTION_CHAMPIONS"
    assert config.MEMORY_HARD_MB == 430
    assert config.CAUSAL_MEMORY_TARGET_MB == 390


def test_strategy_card_is_deterministic_exact_and_llm_free():
    card, spec = _sample_card()
    card2, _ = _sample_card()
    assert card["schema_version"] == CARD_SCHEMA_VERSION
    assert card["uses_llm"] is False
    assert card["generated_by"] == "DETERMINISTIC_RESEARCH_CONFIG"
    assert card["raw_spec"] == spec
    assert card["technical_id"] == "CI_EXAMPLE"
    assert card["fingerprint_sha256"] == card2["fingerprint_sha256"]
    assert card["immutable"] is True
    assert card["entry"]["style"] == "PULLBACK"
    assert card["entry"]["entry_atr"] == 0.30
    assert card["risk"]["stop_atr"] == 1.50
    assert card["risk"]["rr"] == 2.25
    assert card["filters"]["volatility_mode"] == "NORMAL"
    assert card["valid_regime"] == "TREND_DOWN"
    assert card["evidence"]["oos_n"] == 20
    assert card["evidence"]["oos_profit_factor"] == 1.7283


def test_only_shadow_ready_cards_are_frozen():
    card, _ = _sample_card("VALIDATION_REQUIRED")
    assert card["immutable"] is False
    assert card["validation_stage"] == "VALIDATION_REQUIRED"


def test_validation_persists_card_from_exact_causal_spec():
    src = (ROOT / "engines" / "validation.py").read_text(encoding="utf-8")
    assert 'row_meta.get("causal_strategy_spec")' in src
    assert 'final_meta["strategy_card"] = build_strategy_card(' in src
    assert 'production_changes_allowed' in src


def test_fast5_is_deployed_to_all_five_research_services():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert render.count("RESEARCH_BOOTSTRAP_FAST_MINUTES") == 5
    # The next line for each occurrence is the explicit value 5.
    chunks = render.split("RESEARCH_BOOTSTRAP_FAST_MINUTES")[1:]
    assert all("value: 5" in c[:80] for c in chunks)


def test_final_oos_and_selection_separation_remains_locked():
    cov = (ROOT / "coverage_optimizer.py").read_text(encoding="utf-8")
    causal = (ROOT / "causal_backtest.py").read_text(encoding="utf-8")
    assert '"selection_uses_final_oos": False' in cov
    assert '"final_oos_locked": True' in cov
    assert '"final_oos_reused_for_selection": False' in cov
    assert 'int(n * 0.60)' in causal
    assert 'int(n * 0.20)' in causal or '0.20' in causal
