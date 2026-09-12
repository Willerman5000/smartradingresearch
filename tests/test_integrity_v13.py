from metrics import summarize_oos
from engines.base import runtime_contract
from engines.validation import analyze_findings
from config import VERSION


def row(i, r):
    return {
        'id': str(i), 'symbol':'BTC-USDT', 'timeframe':'30m', 'system_type':'futures',
        'action_normalized':'SHORT', 'created_at':f'2026-01-{(i%28)+1:02d}T{i%24:02d}:00:00+00:00',
        'risk_reward':2.0,
        'signal_results':[{'status':'tp_hit' if r>0 else 'sl_hit','modeled_net_r':r}],
    }


def test_profit_factor_degenerate_is_not_fake_999():
    s=summarize_oos([row(i, 1.0) for i in range(20)])
    assert s['all']['profit_factor'] is None
    assert s['all']['profit_factor_degenerate'] is True


def test_walk_forward_is_reported():
    rows=[row(i, 1.0 if i%3 else -1.0) for i in range(60)]
    s=summarize_oos(rows)
    assert 'walk_forward' in s
    assert s['methodology']['causal_candle_replay'] is False


def test_runtime_contract_blocks_unreproducible_trader_fields():
    c=runtime_contract({'market_family':'CRYPTO_FUTURES','trader':'Smart Money','direction':'SHORT'})
    assert c['runtime_trackable'] is False
    assert 'trader' in c['untrackable_fields']


def test_validation_requires_net_and_runtime_for_shadow():
    finding={
        'engine':'execution','experiment':'X','feature_key':'x','research_version':VERSION,
        'scope':{'market_family':'CRYPTO_FUTURES','direction':'SHORT','timeframe':'30M'},
        'meta':{'is_current':True,'runtime_contract':{'runtime_trackable':True}},
        'metrics':{
            'all':{'resolved':50,'symbols':['BTC-USDT','ETH-USDT'],'net_evidence_pct':20},
            'validation':{'resolved':12,'expectancy_r':0.4,'profit_factor':2.0,'profit_factor_degenerate':False},
            'walk_forward':{'valid_folds':3,'positive_fold_ratio':1.0},
        },
    }
    out=analyze_findings([finding])[0]
    assert out['stage']=='VALIDATION_REQUIRED'
    assert 'costes' in out['reason']
