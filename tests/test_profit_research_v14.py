from engines import strategy, traders
from engines.validation import analyze_findings


def _row(i=0, *, status='tp_hit'):
    return {
        'id': f's{i}',
        'system_type': 'futures',
        'symbol': 'BTC-USDT' if i % 2 == 0 else 'ETH-USDT',
        'action_normalized': 'SHORT',
        'timeframe': '30M',
        'created_at': f'2026-09-{1+i:02d}T00:00:00+00:00',
        'risk_reward': 2.0,
        'context': {
            'learning': {
                'quantitative_shadow': {'regime': 'TREND_DOWN'},
                'microstructure_shadow': {
                    'alignment': 'ALIGNED',
                    'metrics': {'orderbook_imbalance': 0.0, 'recent_buy_share': 0.5},
                },
                'strategy_attribution_v2': {
                    'items': [{
                        'trader': 'Pullback',
                        'strategy': 'PULLBACK BAJISTA',
                        'relation_to_final': 'SUPPORT',
                    }]
                },
            },
            'execution': {
                'entry_source': 'PULLBACK + ORDER BLOCK',
                'entry_defensibility_score': 80,
                'entry_reachability_score': 80,
                'sl_reliability': 90,
                'tp_quality_score': 75,
            },
        },
        'signal_results': [{
            'status': status,
            'modeled_net_r': 1.2 if status == 'tp_hit' else -1.0,
        }],
    }


def test_ai_proposal_becomes_research_finding_and_keeps_runtime_contract():
    rows = [_row(i) for i in range(8)]
    proposal = {
        'proposal_id': 'AI_test',
        'name': 'Short 30m trend down',
        'thesis': 'Hipótesis medible',
        'status': 'SHADOW_PROPOSAL',
        'runtime_testable': True,
        'research_filters': {
            'market_family': 'CRYPTO_FUTURES',
            'timeframe': '30M',
            'direction': 'SHORT',
            'regime': 'TREND_DOWN',
            'component': 'STRATEGY:PULLBACK BAJISTA',
        },
    }
    findings = strategy.analyze(rows, ai_proposals=[proposal])
    ai = next(x for x in findings if x['experiment'] == 'AI_STRATEGY_PROPOSAL')
    assert ai['meta']['ai_proposal_id'] == 'AI_test'
    assert ai['meta']['runtime_contract']['runtime_trackable'] is True
    assert ai['meta']['production_changes_allowed'] is False


def test_strategy_component_meta_does_not_destroy_runtime_contract():
    rows = [_row(i) for i in range(8)]
    findings = strategy.analyze(rows)
    component = next(x for x in findings if x['experiment'] == 'COMPONENT_ATTRIBUTION')
    assert 'runtime_contract' in component['meta']
    assert component['meta']['methodology'] == 'OBSERVATIONAL_ATTRIBUTION_WITH_TEMPORAL_HOLDOUT'


def test_trader_meta_does_not_destroy_runtime_contract():
    rows = [_row(i) for i in range(8)]
    findings = traders.analyze(rows)
    item = findings[0]
    assert 'runtime_contract' in item['meta']
    # Trader/relation is intentionally not runtime-trackable yet, but the
    # contract must survive so Validation can fail closed for the right reason.
    assert item['meta']['runtime_contract']['runtime_trackable'] is False


def test_ai_proposal_can_never_take_fast_lane():
    row = {
        'engine': 'strategy',
        'experiment': 'AI_STRATEGY_PROPOSAL',
        'feature_key': 'strategy:AI_STRATEGY_PROPOSAL:test',
        'research_version': __import__('config').VERSION,
        'scope': {'market_family': 'CRYPTO_FUTURES', 'timeframe': '30M', 'direction': 'SHORT'},
        'metrics': {
            'all': {'resolved': 80, 'net_evidence_pct': 100, 'symbols': ['BTC-USDT','ETH-USDT','SOL-USDT']},
            'validation': {'resolved': 20, 'expectancy_r': 0.5, 'profit_factor': 2.0, 'profit_factor_degenerate': False},
            'walk_forward': {'valid_folds': 3, 'positive_fold_ratio': 1.0},
        },
        'meta': {'is_current': True, 'runtime_contract': {'runtime_trackable': True}},
    }
    out = analyze_findings([row])
    assert out[0]['stage'] == 'SHADOW_READY'
    assert out[0]['meta']['ai_proposal'] is True
