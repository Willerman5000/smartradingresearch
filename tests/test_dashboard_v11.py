from pathlib import Path

def test_dashboard_files_exist():
    root=Path(__file__).resolve().parents[1]
    assert (root/'templates/dashboard.html').exists()
    assert (root/'static/dashboard.js').exists()

def test_validation_never_active():
    text=(Path(__file__).resolve().parents[1]/'engines/validation.py').read_text()
    assert "'authority': 'RESEARCH_ONLY'" in text
    assert "production_changes_allowed': False" in text
    assert "SHADOW_READY_FAST" in text

def test_no_heavy_frontend_libs():
    root=Path(__file__).resolve().parents[1]
    html=(root/'templates/dashboard.html').read_text()
    assert 'plotly' not in html.lower()
    assert 'chart.js' not in html.lower()
