
import config

def test_rc6_fast_cadence_is_ten_minutes():
    assert config.BOOTSTRAP_FAST_MINUTES == 10
    assert config.AUTO_INTERVAL_MINUTES == 180

def test_rc6_preserves_rc5_research_generation():
    # RC6 changes scheduler cadence only. Keeping the generation id prevents
    # already validated RC5 champions from disappearing from current fusion.
    assert config.VERSION == "RFV1_12_RC5_ITERATIVE_EDGE_46CELL_20260915"
    assert config.SCHEDULER_POLICY_VERSION == "RC6_FAST10_PRESERVE_CHAMPIONS"
