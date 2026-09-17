from pathlib import Path

def test_critical_reads_have_reserved_budget_and_heavy_reads_do_not():
    db=Path('db.py').read_text(encoding='utf-8')
    app=Path('app.py').read_text(encoding='utf-8')
    assert '_egress_critical_reserve_bytes = 2 * 1024 * 1024' in db
    assert 'priority: str = "normal"' in db
    assert "priority='critical'" in app
    assert "research_champions_v1', params=params, timeout=4, retries=0, priority='critical'" in app

def test_egress_pressure_keeps_causal_search_alive_from_cache():
    app=Path('app.py').read_text(encoding='utf-8')
    assert 'if cached and ratio >= 0.80:' in app
    assert 'if not cached and ratio >= 1.0:' in app
    assert 'return []' in app
