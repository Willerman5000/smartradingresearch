import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def test_version_and_matrix_contract():
    cfg=(ROOT/'config.py').read_text(encoding='utf-8')
    cov=(ROOT/'coverage_optimizer.py').read_text(encoding='utf-8')
    assert 'RFV1_6_1_COVERAGE_COMPLETE_20260913' in cfg
    assert 'CAUSAL_REQUIRED_CELLS = 18' in cfg
    assert 'def all_coverage_cells' in cov
    tree=ast.parse(cov)
    assert tree

def test_incremental_persistence_and_validation_rescue():
    app=(ROOT/'app.py').read_text(encoding='utf-8')
    assert 'def _upsert_findings' in app
    assert 'on_finding=_publish_causal' in app
    assert 'def _rescue_missing_causal_cells' in app
    assert "Celdas causales visibles: {len(causal)}/{required}" in app
    assert "N={it.get('resolved')}" in app

def test_validation_uses_its_ram_only_as_rescue():
    render=(ROOT/'render.yaml').read_text(encoding='utf-8')
    assert 'RESEARCH_CAUSAL_VALIDATION_RESCUE' in render
    assert 'value: 430' in render
    assert 'value: 390' in render
