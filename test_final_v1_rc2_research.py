from pathlib import Path

ROOT=Path(__file__).resolve().parent

def test_contract_is_40_cells_without_5m_15m():
    cfg=(ROOT/'config.py').read_text(encoding='utf-8')
    cov=(ROOT/'coverage_optimizer.py').read_text(encoding='utf-8')
    assert 'CAUSAL_REQUIRED_CELLS = 40' in cfg
    assert 'FUTURES_TFS = ("30M", "1H", "2H", "4H")' in cov
    assert '_future_cells(("5M", "15M"))' not in cov

def test_workers_keep_all_active_v1_cells():
    import importlib.util
    spec=importlib.util.spec_from_file_location('coverage_optimizer', ROOT/'coverage_optimizer.py')
    # Static checks avoid importing network/database dependencies.
    cov=(ROOT/'coverage_optimizer.py').read_text(encoding='utf-8')
    assert '"execution": _future_cells(("30M",))' in cov
    assert '"risk": _future_cells(("1H",))' in cov
    assert '"strategy": _future_cells(("2H", "4H"))' in cov

def test_version_marks_rc2():
    cfg=(ROOT/'config.py').read_text(encoding='utf-8')
    assert 'RFV1_9_RELEASE_CANDIDATE_40CELL_20260914' in cfg
