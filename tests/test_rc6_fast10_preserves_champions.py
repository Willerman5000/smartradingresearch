
import config

def test_rc7_fast_cadence_is_five_minutes():
    assert config.BOOTSTRAP_FAST_MINUTES == 5
    assert config.AUTO_INTERVAL_MINUTES == 180

def test_rc6_preserves_rc5_research_generation():
    # RC7 changes scheduler cadence/card metadata only. Keeping the generation id prevents
    # already validated RC5 champions from disappearing from current fusion.
    assert config.VERSION == "RFV1_13_RC8_1_ACTION_EDGE_92CELL_20260916"
    assert config.SCHEDULER_POLICY_VERSION == "RC8_1_FAST5_ACTION_CHAMPIONS"
