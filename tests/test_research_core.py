from metrics import summarize_oos, realized_r
from features import execution_features, strategy_tokens
from engines.validation import analyze_findings
from config import VERSION


def row(i, r, symbol='BTC-USDT', tf='30m', direction='SHORT'):
    status='tp_hit' if r>0 else 'sl_hit'
    return {
      'id': str(i), 'symbol':symbol,'timeframe':tf,'system_type':'futures','action_normalized':direction,
      'created_at':f'2026-01-{(i%28)+1:02d}T00:00:00+00:00','risk_reward':2,
      'context':{'learning':{'quantitative_shadow':{'regime':'TREND_DOWN'},'microstructure_shadow':{'alignment':'ALIGNED','metrics':{'orderbook_imbalance':0.4,'recent_buy_share':0.3}},'strategy_attribution_v2':{'items':[{'trader':'Smart Money','strategy':'ORDER BLOCK BAJISTA','relation_to_final':'SUPPORT'}]}},'execution':{'entry_source':'ORDER_BLOCK','entry_score':80,'sl_reliability':90,'tp_quality_score':70}},
      'signal_results':[{'status':status,'gross_r':r,'execution_forensics':{'mfe_r':1.2,'mae_r':0.4}}],
    }


def test_summary_and_features():
    rows=[row(i, 2 if i%3 else -1) for i in range(40)]
    s=summarize_oos(rows)
    assert s['all']['resolved']==40
    assert execution_features(rows[0])['has_order_block']=='YES'
    assert any('ORDER BLOCK' in x for x in strategy_tokens(rows[0]))


def test_validation_never_production_authority():
    finding={'engine':'execution','experiment':'X','feature_key':'x','research_version':VERSION,'scope':{},'meta':{'is_current':True,'runtime_contract':{'runtime_trackable':True}},'metrics':{'all':{'resolved':40,'symbols':['BTC-USDT','ETH-USDT'],'net_evidence_pct':100},'validation':{'resolved':12,'expectancy_r':0.4,'profit_factor':2.0,'profit_factor_degenerate':False},'walk_forward':{'valid_folds':3,'positive_fold_ratio':1.0}}}
    out=analyze_findings([finding])[0]
    assert out['stage']=='SHADOW_READY'
    assert out['authority']=='RESEARCH_ONLY'
    assert out['meta']['production_changes_allowed'] is False
