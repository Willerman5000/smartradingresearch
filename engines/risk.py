from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import execution_features
from metrics import forensics

ENGINE = "risk"


def _flag(fx: Dict[str, Any], *names: str) -> str:
    for name in names:
        value = fx.get(name)
        if value is True:
            return "YES"
        if value is False:
            return "NO"
    return "UNKNOWN"


def analyze(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups = defaultdict(list)
    for row in rows:
        if str(row.get("system_type") or "").lower() != "futures":
            continue
        f = execution_features(row)
        fx = forensics(row)
        wick = _flag(fx, "wick_out", "wick_out_observed", "false_invalidation")
        reclaimed = _flag(fx, "reclaimed_entry", "post_sl_reclaimed_entry")
        tp_after = _flag(fx, "tp_reached_after_stop", "would_hit_original_tp_after_sl")
        scopes = [
            ("SL_QUALITY", {"direction":f["direction"], "regime":f["regime"], "sl_quality":f["sl_quality_band"]}),
            ("SL_MICRO", {"direction":f["direction"], "regime":f["regime"], "micro":f["micro_alignment"], "sl_quality":f["sl_quality_band"]}),
            ("WICK_OUT", {"direction":f["direction"], "regime":f["regime"], "wick_out":wick, "entry_source":f["entry_source"]}),
            ("RECLAIM_AFTER_SL", {"direction":f["direction"], "regime":f["regime"], "reclaimed":reclaimed, "tp_after_sl":tp_after}),
        ]
        for experiment, scope in scopes:
            groups[(experiment, tuple(sorted(scope.items())))].append(row)
    findings=[]
    for (experiment, scope_tuple), grows in groups.items():
        if len(grows) >= 5:
            findings.append(finding(ENGINE, experiment, dict(scope_tuple), grows))
    return top_findings(findings)
