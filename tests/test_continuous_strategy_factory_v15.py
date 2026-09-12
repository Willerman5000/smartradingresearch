import unittest

from engines.strategy_factory import analyze_factory
from features import micro_features
from engines.base import runtime_contract


def _row(i, *, regime="TREND_DOWN", action="SHORT", oi=0.8, funding=0.0002, basis=-0.02):
    status = "tp_hit" if i % 3 else "sl_hit"
    r = 2.0 if status == "tp_hit" else -1.0
    return {
        "id": str(i),
        "created_at": f"2026-09-{1 + i//24:02d}T{i%24:02d}:00:00Z",
        "system_type": "futures",
        "symbol": "BTC-USDT" if i % 2 else "ETH-USDT",
        "timeframe": "30m",
        "action_normalized": action,
        "risk_reward": 2.0,
        "status": status,
        "context": {
            "learning": {
                "quantitative_shadow": {"regime": regime},
                "microstructure_shadow": {
                    "alignment": "ALIGNED",
                    "metrics": {
                        "orderbook_imbalance": -0.3 if action == "SHORT" else 0.3,
                        "recent_buy_share": 0.35 if action == "SHORT" else 0.65,
                        "spread_pct": 0.02,
                        "oi_change_pct": oi,
                        "funding_rate": funding,
                        "basis_pct": basis,
                        "liquidity_band": "HIGH",
                    },
                },
                "strategy_attribution_v2": {"items": [{"strategy": "PULLBACK BAJISTA"}]},
            },
            "execution": {
                "entry_defensibility_score": 82,
                "entry_reachability_score": 77,
                "sl_reliability": 91,
                "tp_quality_score": 78,
            },
        },
        "signal_results": [{"status": status, "modeled_net_r": r}],
    }


class ContinuousStrategyFactoryTests(unittest.TestCase):
    def test_micro_features_include_derivatives_context(self):
        f = micro_features(_row(1))
        self.assertEqual(f["oi_change_band"], "BUILDING")
        self.assertEqual(f["funding_band"], "NEUTRAL")
        self.assertEqual(f["basis_band"], "NEUTRAL")
        self.assertEqual(f["liquidity_band"], "HIGH")

    def test_factory_generates_bounded_interpretable_candidates(self):
        rows = [_row(i) for i in range(1, 42)]
        items = analyze_factory(rows)
        self.assertTrue(items)
        self.assertLessEqual(len(items), 40)
        families = {(x.get("meta") or {}).get("factory_family") for x in items}
        self.assertIn("TREND_CONTINUATION", families)
        self.assertIn("DERIVATIVES_POSITIONING", families)
        for item in items:
            meta = item.get("meta") or {}
            self.assertTrue(meta.get("factory_strategy"))
            self.assertFalse(meta.get("production_changes_allowed"))

    def test_new_factory_fields_are_runtime_trackable(self):
        contract = runtime_contract({
            "market_family": "CRYPTO_FUTURES",
            "timeframe": "30M",
            "direction": "SHORT",
            "regime": "TREND_DOWN",
            "oi_change_band": "BUILDING",
            "funding_band": "NEUTRAL",
            "basis_band": "NEUTRAL",
            "liquidity_band": "HIGH",
        })
        self.assertTrue(contract["runtime_trackable"])


if __name__ == "__main__":
    unittest.main()
