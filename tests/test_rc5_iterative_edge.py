import pathlib
import unittest
import config
from historical_market import Candle
from coverage_optimizer import all_coverage_cells, _wave_specs, SEARCH_WAVE_NAMES, INDICATOR_COVERAGE_MANIFEST
from causal_backtest import features, _direction_signal

ROOT=pathlib.Path(__file__).resolve().parents[1]

class RC5IterativeEdgeTests(unittest.TestCase):
    def _candles(self,n=220):
        out=[]; px=100.0
        for i in range(n):
            drift=0.18 if (i//35)%2==0 else -0.12
            close=px+drift+((i%7)-3)*0.03
            high=max(px,close)+0.35+(i%3)*0.03
            low=min(px,close)-0.35-(i%4)*0.02
            out.append(Candle(i*3600,px,high,low,close,1000+50*(i%11)))
            px=close
        return out

    def test_contract_and_version(self):
        self.assertEqual(len(all_coverage_cells()),46)
        self.assertEqual(config.CAUSAL_REQUIRED_CELLS,46)
        self.assertTrue(config.VERSION.startswith('RFV1_12_RC5_ITERATIVE_EDGE_46CELL'))

    def test_all_search_waves_have_candidates(self):
        for wave in SEARCH_WAVE_NAMES:
            self.assertGreater(len(_wave_specs('2H',wave)),0,wave)

    def test_divergences_and_indicator_inventory_present(self):
        families={s.family for s in _wave_specs('2H','DIVERGENCES')}
        self.assertIn('DIVERGENCE_REVERSAL',families)
        self.assertIn('HIDDEN_DIVERGENCE_TREND',families)
        all_families=set()
        for wave in SEARCH_WAVE_NAMES:
            all_families.update(s.family for s in _wave_specs('2H',wave))
        for family in ('PSAR_TREND','RSI_MAVERICK_REVERSAL','VOLUME_PROFILE_NODE_REACTION'):
            self.assertIn(family,all_families)
        inv=set(INDICATOR_COVERAGE_MANIFEST['causal_ohlcv'])
        for name in (
            'RSI','RSI_MULTI','RSI_MAVERICK','MACD','STOCHASTIC','ATR','ADX_DMI',
            'BOLLINGER','VWAP','MFI','FORCE_INDEX','OBV','CCI','WILLIAMS_R',
            'SUPERTREND_PROXY','PARABOLIC_SAR','ICHIMOKU','FIBONACCI_RETRACE',
            'FVG_PROXY','VOLUME_PROFILE_POC_PROXY','HVN_LVN_PROFILE_PROXY',
            'STRUCTURE_SWEEP','DIVERGENCES_REGULAR_HIDDEN',
        ):
            self.assertIn(name,inv)

    def test_new_families_compute_causally(self):
        candles=self._candles()
        samples=[]
        for wave in ('TREND','OSCILLATORS','DIVERGENCES','VOLATILITY','FLOW_STRUCTURE','COMPOSITE','ALT_PARAMS'):
            specs=_wave_specs('2H',wave)
            samples.extend(specs[:3])
            # Explicitly smoke the expensive/less common families too.
            for wanted in ('PSAR_TREND','RSI_MAVERICK_REVERSAL','VOLUME_PROFILE_NODE_REACTION'):
                hit=next((x for x in specs if x.family==wanted),None)
                if hit: samples.append(hit)
        for spec in samples:
            feat=features(candles,spec)
            self.assertEqual(len(feat['rsi']),len(candles))
            _direction_signal(len(candles)-2,spec,feat,candles)

    def test_bootstrap_uses_latest_cell_state_only(self):
        text=(ROOT/'app.py').read_text(encoding='utf-8')
        block=text[text.index('def _bootstrap_status'):text.index('def _bootstrap_interval_minutes')]
        self.assertIn('complete=set(); seen=set()',block)
        self.assertIn('cid not in expected_ids or cid in seen',block)
        self.assertIn("{'SHADOW_READY','SHADOW_READY_FAST'}",block)

    def test_fast_search_cadence_until_all_shadow_ready(self):
        self.assertEqual(config.BOOTSTRAP_FAST_MINUTES, 15)
        self.assertEqual(config.AUTO_INTERVAL_MINUTES, 180)
        text=(ROOT/'app.py').read_text(encoding='utf-8')
        block=text[text.index('def _bootstrap_status'):text.index('def _run_job')]
        self.assertIn("{'SHADOW_READY','SHADOW_READY_FAST'}", block)
        self.assertIn("count <= 0", block)
        self.assertIn("BOOTSTRAP_FAST_MINUTES", block)

    def test_indicator_manifest_has_causal_and_observational_system_families(self):
        causal=set(INDICATOR_COVERAGE_MANIFEST['causal_ohlcv'])
        obs=set(INDICATOR_COVERAGE_MANIFEST['observational_only_without_trustworthy_historical_series'])
        self.assertTrue({'PARABOLIC_SAR','RSI_MAVERICK','DIVERGENCES_REGULAR_HIDDEN','HVN_LVN_PROFILE_PROXY'} <= causal)
        self.assertTrue({'ORDER_FLOW','OPEN_INTEREST','FUNDING','LIQUIDATION_HEATMAP','WHALE_ACTIVITY','MACRO_NEWS'} <= obs)

if __name__=='__main__': unittest.main()
