from causal_backtest import StrategySpec, replay_series, temporal_metrics, selection_score
from historical_market import Candle


def _trend(n=420):
    rows=[]
    p=100.0
    ts=1_700_000_000
    for i in range(n):
        # steady trend with periodic pullbacks and older resistance context
        drift=0.28 if i % 8 else -0.10
        o=p
        c=max(1.0,o+drift)
        h=max(o,c)+0.35
        l=min(o,c)-0.25
        rows.append(Candle(ts+i*14400,o,h,l,c,1000+i))
        p=c
    return rows


def test_rc3_operational_guardian_replay_is_present_but_research_only():
    spec=StrategySpec(
        'TREND_CONTINUATION','LONG',fast=9,slow=21,
        sl_atr=1.4,rr=1.6,max_wait=1,max_hold=10,
    )
    trades,stats=replay_series('ETH-USDT','futures',_trend(),spec)
    assert stats['signals'] > 0
    assert trades
    metrics=temporal_metrics(trades)
    overlay=metrics['all']['guardian_operational_replay']
    assert overlay['authority']=='RESEARCH_ONLY'
    assert overlay['selection_uses_overlay'] is False
    assert overlay['policy']=='RC3_GUARDIAN_CLOSED_CANDLE_CAUSAL_OVERLAY'
    assert overlay['n']==len(trades)
    assert 'delta_expectancy_r_vs_original' in overlay


def test_selection_score_ignores_guardian_overlay_and_final_oos():
    base={
        'train':{'resolved':30,'expectancy_r':0.20,'profit_factor':1.3,'max_drawdown_r':1.0},
        'selection_holdout':{'resolved':10,'expectancy_r':0.25,'profit_factor':1.4,'max_drawdown_r':0.8},
        'validation':{'resolved':8,'expectancy_r':-9.0,'profit_factor':0.01},
        'all':{},
    }
    score1=selection_score(base)
    changed={**base,'validation':{'resolved':8,'expectancy_r':99.0,'profit_factor':99.0}}
    changed['selection_holdout']={**base['selection_holdout'],'guardian_operational_replay':{'expectancy_r':-100.0}}
    score2=selection_score(changed)
    assert score1==score2
