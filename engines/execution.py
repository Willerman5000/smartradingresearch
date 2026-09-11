from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from engines.base import finding, top_findings
from features import execution_features

ENGINE = "execution"


def analyze(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups = defaultdict(list)
    for row in rows:
        if str(row.get("system_type") or "").lower() != "futures":
            continue
        f = execution_features(row)
        # Questions that directly affect entry quality, not generic indicator spam.
        keys = [
            ("REGIME_DIRECTION_TF", {k:f[k] for k in ("direction","regime","timeframe")}),
            ("ENTRY_SOURCE", {k:f[k] for k in ("direction","regime","entry_source")}),
            ("OB_MICRO", {k:f[k] for k in ("direction","regime","has_order_block","micro_alignment")}),
            ("SWEEP_MICRO", {k:f[k] for k in ("direction","regime","has_sweep","micro_alignment")}),
            ("PULLBACK_MICRO", {k:f[k] for k in ("direction","regime","has_pullback","micro_alignment")}),
            ("ORDERFLOW", {k:f[k] for k in ("direction","regime","orderbook_imbalance_band","recent_buy_share_band")}),
            ("ENTRY_QUALITY", {k:f[k] for k in ("direction","regime","defensibility_band","reachability_band")}),
        ]
        for experiment, scope in keys:
            groups[(experiment, tuple(sorted(scope.items())))].append(row)
    findings = []
    for (experiment, scope_tuple), grows in groups.items():
        if len(grows) < 5:
            continue
        findings.append(finding(ENGINE, experiment, dict(scope_tuple), grows))
    return top_findings(findings)
