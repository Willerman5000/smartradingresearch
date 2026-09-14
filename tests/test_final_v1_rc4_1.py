import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import historical_market
import config

ROOT = Path(__file__).resolve().parents[1]

class RC41ResearchTests(unittest.TestCase):
    def test_futures_high_tf_granularity(self):
        self.assertEqual(historical_market._FUTURES_GRANULARITY['12H'], 720)
        self.assertEqual(historical_market._FUTURES_GRANULARITY['1D'], 1440)

    def test_contract_remains_46_cells(self):
        self.assertEqual(config.CAUSAL_REQUIRED_CELLS, 46)
        self.assertEqual(config.VERSION, 'RFV1_11_FINAL_RC42_46CELL_20260914')
        self.assertEqual(config.RELEASE_LABEL, 'FINAL V1')

    def test_report_source_contains_active_contract_filter(self):
        src = (ROOT / 'app.py').read_text(encoding='utf-8')
        self.assertIn('def _row_in_active_contract', src)
        self.assertIn("tf not in {'5M','15M'}", src)
        self.assertIn('legacy_hidden', src)

if __name__ == '__main__':
    unittest.main()
