from __future__ import annotations

from causal_backtest import StrategySpec, replay_series, temporal_metrics
from coverage_optimizer import LANES
from historical_market import Candle
from engines import validation
import config


def _uptrend(n=420, step=0.35):
    out=[]
    price=100.0
    ts=1_700_000_000
    for i in range(n):
        o=price
        # recurrent mild pullback but persistent trend
        drift=step if i % 7 else -step*0.35
        c=max(1.0,o+drift)
        h=max(o,c)+0.25
        l=min(o,c)-0.25
        out.append(Candle(ts+i*3600,o,h,l,c,1000+i))
        price=c
    return out


def test_causal_replay_produces_net_metrics_without_lookahead():
    spec=StrategySpec('TREND_CONTINUATION','LONG',fast=9,slow=21,sl_atr=1.5,rr=1.5,max_wait=1,max_hold=12)
    trades,stats=replay_series('BTC-USDT','spot',_uptrend(),spec)
    assert stats['signals'] > 0
    assert trades
    assert all(t.entry_ts > t.signal_ts for t in trades)
    metrics=temporal_metrics(trades)
    assert metrics['methodology']['causal_candle_replay'] is True
    assert metrics['methodology']['same_bar_tp_sl'] == 'CONSERVATIVE_SL_FIRST'
    assert metrics['all']['net_evidence_pct'] == 100.0


def test_commit_i2_lanes_cover_all_54_symbol_tf_cells_once():
    cells=[cell for engine,cells in LANES.items() for cell in cells]
    assert len(cells) == 54
    assert len(set(cells)) == 54
    futures=[x for x in cells if x[0]=='futures']
    spot=[x for x in cells if x[0]=='spot']
    assert {x[3] for x in futures} == {'5M','15M','30M','1H','2H','4H'}
    assert len(futures) == 42
    assert {x[2] for x in futures} == {'BTC-USDT','ETH-USDT','SOL-USDT','XRP-USDT','ADA-USDT','LINK-USDT','BNB-USDT'}
    assert len(spot) == 12
    assert {x[2] for x in spot} == {'BTC-USDT','PAXG-USDT','PAXG-BTC'}


def _causal_row(tf='30M', oos_n=14, exp=0.28, pf=1.45):
    by_symbol={
        'BTC-USDT':{'n':3,'expectancy_r':0.20},
        'ETH-USDT':{'n':3,'expectancy_r':0.15},
        'SOL-USDT':{'n':3,'expectancy_r':0.40},
        'XRP-USDT':{'n':2,'expectancy_r':0.12},
    }
    return {
        'engine':'risk','experiment':'CAUSAL_COVERAGE_STRATEGY','feature_key':'ci:test',
        'research_version':config.VERSION,
        'scope':{'market_family':'CRYPTO_FUTURES','symbol':'BTC-USDT','timeframe':tf,'direction':'SHORT','regime':'TREND_DOWN'},
        'meta':{'is_current':True,'causal_candle_replay':True,'runtime_contract':{'runtime_trackable':True}},
        'metrics':{
            'all':{'resolved':80,'symbols':list(by_symbol),'net_evidence_pct':100.0,'expectancy_r':0.31,'profit_factor':1.55},
            'validation':{'resolved':oos_n,'expectancy_r':exp,'profit_factor':pf,'profit_factor_degenerate':False,'by_symbol':by_symbol},
            'walk_forward':{'valid_folds':3,'positive_fold_ratio':1.0},
        },
    }


def test_validation_promotes_strong_causal_futures_to_shadow_ready():
    out=validation.analyze_findings([_causal_row()])
    assert len(out)==1
    assert out[0]['stage'] in {'SHADOW_READY','SHADOW_READY_FAST'}
    assert out[0]['meta']['causal_strategy'] is True
    assert out[0]['meta']['causal_cross_asset']['required'] is False
    assert out[0]['meta']['symbol_timeframe_specialist'] is True


def test_validation_rejects_negative_causal_oos():
    row=_causal_row(oos_n=14,exp=-0.25,pf=0.7)
    out=validation.analyze_findings([row])
    assert out[0]['stage']=='REJECTED_OOS'


def test_exchange_flow_context_can_never_promote():
    row={
        'engine':'strategy','experiment':'EXCHANGE_FLOW_CONTEXT','feature_key':'flow:test',
        'research_version':config.VERSION,
        'scope':{'market_family':'CRYPTO_FUTURES','exchange_flow_state':'NET_INFLOW_PRESSURE'},
        'meta':{'is_current':True,'runtime_trackable':False},
        'metrics':{'all':{'resolved':999,'net_evidence_pct':100},'validation':{'resolved':100,'expectancy_r':5.0,'profit_factor':9.0},'walk_forward':{'valid_folds':3,'positive_fold_ratio':1.0}},
    }
    out=validation.analyze_findings([row])
    assert out[0]['stage']=='OBSERVE'


def test_research_memory_budget_keeps_headroom():
    assert config.CAUSAL_MEMORY_TARGET_MB < config.MEMORY_HARD_MB
    assert config.MEMORY_HARD_MB <= 460
