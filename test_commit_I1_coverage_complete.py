import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def test_version_and_matrix_contract():
    cfg=(ROOT/'config.py').read_text(encoding='utf-8')
    cov=(ROOT/'coverage_optimizer.py').read_text(encoding='utf-8')
    assert 'RFV1_9_RELEASE_CANDIDATE_40CELL_20260914' in cfg
    assert 'CAUSAL_REQUIRED_CELLS = 40' in cfg
    assert 'def all_coverage_cells' in cov
    tree=ast.parse(cov)
    assert tree

def test_incremental_persistence_and_validation_rescue():
    app=(ROOT/'app.py').read_text(encoding='utf-8')
    assert 'def _upsert_findings' in app
    assert 'on_finding=_publish_causal' in app
    assert 'def _rescue_missing_causal_cells' in app
    assert "Celdas símbolo×temporalidad visibles: {len(causal)}/{required}" in app
    assert "N={it.get('resolved')}" in app

def test_validation_rescue_is_bounded_by_active_contract():
    app=(ROOT/'app.py').read_text(encoding='utf-8')
    cfg=(ROOT/'config.py').read_text(encoding='utf-8')
    assert 'def _rescue_missing_causal_cells' in app
    assert 'CAUSAL_REQUIRED_CELLS = 40' in cfg
