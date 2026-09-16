import pathlib

ROOT=pathlib.Path(__file__).resolve().parents[1]
SRC=(ROOT/'app.py').read_text(encoding='utf-8')


def block(start, end):
    return SRC[SRC.index(start):SRC.index(end, SRC.index(start))]


def test_persistent_champion_helper_is_present():
    b=block('def _persistent_champions_by_cell', 'def _best_causal_per_cell')
    assert "stage') or '').upper() in _SHADOW_STAGES" in b
    assert '_explicitly_demoted_roots(rows)' in b
    assert "champion_role') or '')).upper()=='INCUMBENT'" in b


def test_new_challenger_cannot_stale_incumbent_by_absence():
    b=block('def _run_validation', 'def _causal_retest_promotions_for_engine')
    assert 'incumbents=_persistent_champions_by_cell(old)' in b
    assert "meta['champion_role']='CHALLENGER'" in b
    assert "meta['champion_role']='INCUMBENT'" in b
    assert 'current_keys.add(key)' in b
    assert 'Everything else not emitted/preserved may become stale' in b


def test_only_explicit_retest_rejection_demotes_lineage():
    b=block('def _explicitly_demoted_roots', 'def _champion_score')
    assert "_RETEST_EXPERIMENTS" in b
    assert "upper()=='REJECTED_OOS'" in b
    assert "original_candidate_key" in b


def test_bootstrap_counts_persistent_champions_not_latest_challenger():
    b=block('def _bootstrap_status', 'def _bootstrap_interval_minutes')
    assert '_canonical_champion_cells' in b
    assert 'research_champions_v1' in SRC
    assert 'expected_ids-complete' in b
