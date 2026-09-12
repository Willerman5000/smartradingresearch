from engines.base import balanced_current_rows, source_coverage


def _row(key, engine, tf):
    return {
        'candidate_key': key,
        'source_engine': engine,
        'stage': 'OBSERVE',
        'scope': {'timeframe': tf},
        'meta': {'is_current': True},
    }


def test_source_coverage_marks_strategic_timeframes():
    rows = [
        {'id': '1', 'system_type': 'spot', 'timeframe': '4h'},
        {'id': '2', 'system_type': 'spot', 'timeframe': '12h'},
        {'id': '3', 'system_type': 'spot', 'timeframe': '1D'},
        {'id': '4', 'system_type': 'spot', 'timeframe': '1W'},
    ]
    cov = source_coverage(rows)
    assert cov['strategic_timeframes'] == {'4H': 1, '12H': 1, '1D': 1, '1W': 1}
    assert cov['missing_strategic_timeframes'] == []


def test_balanced_rows_does_not_let_intraday_monopolize_dashboard():
    rows = [_row(f'5m-{i}', 'risk', '5M') for i in range(50)]
    rows += [
        _row('4h-execution', 'execution', '4H'),
        _row('12h-strategy', 'strategy', '12H'),
        _row('1d-traders', 'traders', '1D'),
        _row('1w-risk', 'risk', '1W'),
    ]
    out = balanced_current_rows(rows, 12)
    tfs = {str((x.get('scope') or {}).get('timeframe')).upper() for x in out}
    assert {'4H', '12H', '1D', '1W'}.issubset(tfs)
