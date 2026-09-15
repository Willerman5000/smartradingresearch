from pathlib import Path

import config
from coverage_optimizer import all_coverage_cells, coverage_cell_id
from features import scoped_symbol
from full_stack_certification import build_full_stack_certification

ROOT = Path(__file__).resolve().parents[1]

def test_final_contract_and_bootstrap_policy():
    cells = all_coverage_cells()
    assert len(cells) == 46
    assert len({coverage_cell_id(c) for c in cells}) == 46
    assert config.BOOTSTRAP_ACCELERATED is True
    assert config.BOOTSTRAP_FAST_MINUTES == 10
    assert config.BOOTSTRAP_BACKOFF_MINUTES >= 60
    assert config.AUTO_INTERVAL_MINUTES == 180

def test_symbol_isolation_is_default_learning_key():
    assert scoped_symbol({'system_type':'futures','symbol':'XRP-USDT'}) == 'XRP-USDT'
    assert scoped_symbol({'system_type':'spot','symbol':'BTC-USDT'}) == 'BTC-USDT'

def test_historical_certificate_never_claims_production_parity():
    cert = build_full_stack_certification(
        system_type='futures', symbol='SOL-USDT', timeframe='2H',
        strategy_family='EMA_RECLAIM', strategy_spec={'entry_style':'PULLBACK','sl_atr':1.5,'rr':2.0},
        metrics={'validation':{'resolved':10,'expectancy_r':0.2,'profit_factor':1.4}},
        execution_stats={'signals':12,'activated':8},
    )
    assert cert['production_authority'] is False
    assert cert['production_parity'] is False
    assert cert['certification_state'].startswith('HISTORICAL_BACKTEST_POSITIVE')

def test_retired_fast_timeframes_are_excluded_from_new_observational_learning():
    src=(ROOT/'app.py').read_text(encoding='utf-8')
    assert 'def _source_row_in_active_contract' in src
    assert "{'30M','1H','2H','4H'}" in src
    assert "{'12H','1D'}" in src
    assert 'BOOTSTRAP_FAST' in src
    assert 'only_cell_ids=bootstrap_pending' in src
    assert 'if bootstrap_pending else []' in src
