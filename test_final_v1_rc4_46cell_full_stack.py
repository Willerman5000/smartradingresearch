from collections import Counter
from causal_backtest import Trade, summarize_trades
from coverage_optimizer import all_coverage_cells, FUTURES_HIGH_TF_SYMBOLS
from full_stack_certification import build_full_stack_certification


def test_rc4_contract_has_46_independent_cells():
    cells = all_coverage_cells()
    assert len(cells) == 46
    assert len(set(cells)) == 46
    high = [c for c in cells if c[3] in {'12H','1D'} and c[0] == 'futures']
    assert len(high) == 6
    assert {c[2] for c in high} == set(FUTURES_HIGH_TF_SYMBOLS)
    assert Counter(c[3] for c in high) == {'12H':3,'1D':3}


def test_entry_wick_audit_is_visible_without_rewriting_pnl():
    trades = [Trade('BTC-USDT','LONG',1,2,3,100,95,110,-1.05,0.1,1.05,1,'SL',
                    direct_stop=True,wick_stop=True,reclaimed_after_sl=True,tp_after_sl=True)]
    summary = summarize_trades(trades)
    audit = summary['entry_execution_audit']
    assert audit['direct_stop_pct'] == 100.0
    assert audit['wick_stop_pct'] == 100.0
    assert audit['tp_after_sl_count'] == 1
    assert summary['expectancy_r'] == -1.05


def test_full_stack_certificate_never_grants_production_authority():
    metrics = {'validation': {
        'resolved': 12, 'expectancy_r': 0.25, 'profit_factor': 1.8,
        'cost_model':'MODELED_FEES_SLIPPAGE_AND_FUNDING_STRESS',
        'entry_execution_audit': {'n_resolved':12},
        'guardian_operational_replay': {'delta_expectancy_r_vs_original':0.05},
    }}
    cert = build_full_stack_certification(system_type='futures',symbol='BTC-USDT',timeframe='4H',
        strategy_family='BREAKOUT_RETEST', strategy_spec={'entry_style':'PULLBACK','entry_atr':0.3,'sl_atr':1.4,'rr':2.5},
        metrics=metrics, execution_stats={'signals':20,'activated':14})
    assert cert['certification_state'].startswith('HISTORICAL_BACKTEST_POSITIVE')
    assert cert['production_parity'] is False
    assert cert['production_authority'] is False
    assert cert['committee']['state'] == 'RUNTIME_ATTRIBUTION_REQUIRED'
