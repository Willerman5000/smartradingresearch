
import config

def test_rc7_fast_cadence_is_five_minutes():
    assert config.BOOTSTRAP_FAST_MINUTES == 5
    assert config.AUTO_INTERVAL_MINUTES == 180

def test_rc6_preserves_rc5_research_generation():
    # RC7 changes scheduler cadence/card metadata only. Keeping the generation id prevents
    # already validated RC5 champions from disappearing from current fusion.
    assert config.VERSION == "RFV1_12_RC5_ITERATIVE_EDGE_46CELL_20260915"
    assert config.SCHEDULER_POLICY_VERSION == "RC7_FAST5_PRESERVE_CHAMPIONS"
