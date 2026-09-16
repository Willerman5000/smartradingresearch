from coverage_optimizer import all_coverage_cells, coverage_cell_id, optimize_cell_candidates
import config


def test_action_cell_contract_is_92_and_unique():
    cells=all_coverage_cells()
    assert config.CAUSAL_REQUIRED_CELLS == 92
    assert len(cells) == 92
    assert len({coverage_cell_id(c) for c in cells}) == 92
    assert all(len(c)==5 for c in cells)


def test_futures_and_spot_actions_are_separate_cells():
    ids={coverage_cell_id(c) for c in all_coverage_cells()}
    assert 'FUTURES|CRYPTO_FUTURES|BTC-USDT|4H|LONG' in ids
    assert 'FUTURES|CRYPTO_FUTURES|BTC-USDT|4H|SHORT' in ids
    assert 'SPOT|CRYPTO_SPOT|BTC-USDT|4H|COMPRA_SPOT' in ids
    assert 'SPOT|CRYPTO_SPOT|BTC-USDT|4H|VENTA_SPOT' in ids


def test_strategy_search_filters_to_cell_action(monkeypatch):
    import coverage_optimizer as co
    cell=('futures','CRYPTO_FUTURES','BTC-USDT','4H','SHORT')
    monkeypatch.setattr(co,'_load_cell',lambda _:{'BTC-USDT':[1]*200})
    class S:
        direction_mode='SHORT'
        family='TEST'
        def key(self): return ('TEST','SHORT')
    monkeypatch.setattr(co,'_wave_specs',lambda tf,w:[S()])
    monkeypatch.setattr(co,'_evaluate',lambda data,system,spec:({'selection_holdout':{'resolved':0},'all':{},'validation':{},'walk_forward':{}},{'signals':0,'activated':0}))
    monkeypatch.setattr(co,'selection_score',lambda metrics:0)
    monkeypatch.setattr(co,'_refine',lambda seed,tf:[])
    monkeypatch.setattr(co,'_finding',lambda cell,spec,*a,**k:{'direction':getattr(spec,'direction_mode',None)})
    out=optimize_cell_candidates(cell)
    assert out and all(x['direction']=='SHORT' for x in out)
