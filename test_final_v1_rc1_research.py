from pathlib import Path

ROOT = Path(__file__).resolve().parent


def txt(name):
    return (ROOT / name).read_text(encoding='utf-8')


def test_release_candidate_oos_integrity_contract():
    cfg = txt('config.py')
    cov = txt('coverage_optimizer.py')
    val = txt('engines/validation.py')
    assert 'RFV1_9_RELEASE_CANDIDATE_40CELL_20260914' in cfg
    assert 'causal_dataset_signature' in cov
    assert 'final_oos_locked' in cov
    assert 'NEW_DATA_REQUIRED_FOR_RETEST' in cov
    assert 'previous_signature' in cov and 'current_signature' in cov
    assert 'causal_finalist_rank' in val
    assert 'causal_oos_promotable' in val
    assert 'finalista backup; requiere nueva generación OOS' in val


def test_uncovered_cells_are_prioritized_not_standards_lowered():
    app = txt('app.py')
    cov = txt('coverage_optimizer.py')
    assert '_priority_cells_for_engine' in app
    assert 'priority_cell_ids=_priority_cells_for_engine' in app
    assert 'priority_cell_ids=None' in cov
    assert 'priority =' in cov
    # The validation thresholds still exist; RC1 changes ordering/integrity, not profitability gates.
    val = txt('engines/validation.py')
    assert 'vexp>min_exp' in val and 'vpf>min_pf' in val
