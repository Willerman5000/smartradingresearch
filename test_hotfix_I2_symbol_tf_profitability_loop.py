from pathlib import Path
import ast

import config
from coverage_optimizer import LANES, all_coverage_cells, coverage_cell_id

ROOT=Path(__file__).resolve().parent


def test_i2_contract_is_54_cells():
    cells=all_coverage_cells()
    assert config.CAUSAL_REQUIRED_CELLS == 54
    assert len(cells)==54
    assert len({coverage_cell_id(x) for x in cells})==54
    futures=[x for x in cells if x[0]=='futures']
    spot=[x for x in cells if x[0]=='spot']
    assert len(futures)==42
    assert len(spot)==12


def test_each_futures_symbol_has_each_operational_tf():
    cells=all_coverage_cells()
    symbols={'BTC-USDT','ETH-USDT','SOL-USDT','XRP-USDT','ADA-USDT','LINK-USDT','BNB-USDT'}
    tfs={'5M','15M','30M','1H','2H','4H'}
    got={(c[2],c[3]) for c in cells if c[0]=='futures'}
    assert got == {(s,tf) for s in symbols for tf in tfs}


def test_spot_contract_has_three_markets_four_tfs():
    cells=all_coverage_cells()
    symbols={'BTC-USDT','PAXG-USDT','PAXG-BTC'}
    tfs={'4H','12H','1D','1W'}
    got={(c[2],c[3]) for c in cells if c[0]=='spot'}
    assert got == {(s,tf) for s in symbols for tf in tfs}


def test_oos_is_not_used_to_rank_finalists():
    txt=(ROOT/'causal_backtest.py').read_text(encoding='utf-8')
    tree=ast.parse(txt)
    # The selection function intentionally reads only train + selection_holdout.
    fn=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='selection_score'][0]
    source=ast.get_source_segment(txt,fn) or ''
    assert 'selection_holdout' in source
    assert 'validation' not in source
    assert 'final_oos' not in source


def test_shadow_recycle_is_prioritized_without_changing_safety():
    cov=(ROOT/'coverage_optimizer.py').read_text(encoding='utf-8')
    app=(ROOT/'app.py').read_text(encoding='utf-8')
    assert 'CAUSAL_SHADOW_RECYCLE' in cov
    assert 'shadow_recycle_priority' in app
    assert 'changes_safety": False' in cov
    assert 'changes_leverage": False' in cov


def test_memory_headroom_is_preserved():
    assert config.CAUSAL_MEMORY_TARGET_MB <= 390
    assert config.MEMORY_HARD_MB <= 430
    assert config.CAUSAL_FINALISTS_PER_CELL <= 6
