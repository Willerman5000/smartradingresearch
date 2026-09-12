from __future__ import annotations
from collections import defaultdict
from typing import Any, Dict, List
from config import FINDING_LIMIT
from engines.base import finding, top_findings
from engines.strategy_factory import analyze_factory
from features import (
    strategy_tokens, market_family, scoped_symbol, execution_features,
)
from metrics import regime, summarize_oos
ENGINE="strategy"


def _proposal_match(row: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    f = execution_features(row)
    actual = {
        "market_family": market_family(row),
        "symbol": str(row.get("symbol") or "").upper().replace("/", "-"),
        "timeframe": f.get("timeframe"),
        "direction": f.get("direction"),
        "regime": f.get("regime"),
        "micro_alignment": f.get("micro_alignment"),
        "sl_quality_band": f.get("sl_quality_band"),
        "tp_quality_band": f.get("tp_quality_band"),
        "defensibility_band": f.get("defensibility_band"),
        "reachability_band": f.get("reachability_band"),
        "has_order_block": f.get("has_order_block"),
        "has_sweep": f.get("has_sweep"),
        "has_pullback": f.get("has_pullback"),
        "oi_change_band": f.get("oi_change_band"),
        "funding_band": f.get("funding_band"),
        "basis_band": f.get("basis_band"),
        "liquidity_band": f.get("liquidity_band"),
        "orderbook_imbalance_band": f.get("orderbook_imbalance_band"),
        "recent_buy_share_band": f.get("recent_buy_share_band"),
    }
    tokens = strategy_tokens(row)
    for key, expected in (filters or {}).items():
        expected = str(expected or "").upper()
        if key == "component":
            if expected not in tokens:
                return False
            continue
        if key not in actual:
            return False
        if str(actual.get(key) or "").upper() != expected:
            return False
    return True


def _ai_proposal_findings(rows: List[Dict[str, Any]], proposals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out=[]
    for proposal in (proposals or [])[:12]:
        if not isinstance(proposal, dict):
            continue
        filters=proposal.get("research_filters") or {}
        if not isinstance(filters, dict):
            continue
        discriminants=[k for k in filters if k != "market_family"]
        if not discriminants:
            continue
        matched=[row for row in rows if _proposal_match(row, filters)]
        scope={str(k):str(v)[:180] for k,v in filters.items()}
        item=finding(
            ENGINE,
            "AI_STRATEGY_PROPOSAL",
            scope,
            matched,
            meta={
                "ai_proposal_id": str(proposal.get("proposal_id") or "")[:80],
                "ai_proposal_name": str(proposal.get("name") or "Propuesta IA")[:160],
                "ai_proposal_thesis": str(proposal.get("thesis") or "")[:600],
                "ai_provider": str(proposal.get("provider") or "")[:60],
                "ai_created_at": str(proposal.get("created_at") or "")[:60],
                "ai_proposal_only": True,
                "production_changes_allowed": False,
                "note": "Hipótesis generada por Learning Scientist y medida sólo contra evidencia persistida; no es señal ni aprobación.",
            },
        )
        out.append(item)
    return out


def analyze(rows: List[Dict[str,Any]], ai_proposals: List[Dict[str,Any]] | None = None) -> List[Dict[str,Any]]:
    token_rows=defaultdict(list); baseline=defaultdict(list)
    for row in rows:
        st=str(row.get("system_type") or "").lower()
        if st not in {"futures","spot"}: continue
        fam=market_family(row); sym=scoped_symbol(row); direction=str(row.get("action_normalized") or "UNKNOWN").upper(); rg=regime(row); tf=str(row.get("timeframe") or "UNKNOWN").upper()
        ctx=(fam,sym,direction,rg,tf); baseline[ctx].append(row)
        for token in strategy_tokens(row): token_rows[(ctx,token)].append(row)
    findings=[]
    for (ctx,token),grows in token_rows.items():
        if len(grows)<5: continue
        fam,sym,direction,rg,tf=ctx; scope={"market_family":fam,"direction":direction,"regime":rg,"timeframe":tf,"component":token[:180]}
        if sym!="ALL_CRYPTO": scope["symbol"]=sym
        item=finding(ENGINE,"COMPONENT_ATTRIBUTION",scope,grows)
        comp=((item.get("metrics") or {}).get("all") or {}).get("expectancy_r"); base=((summarize_oos(baseline[ctx]).get("all") or {}).get("expectancy_r"))
        try: delta=float(comp)-float(base)
        except Exception: delta=None
        meta=dict(item.get("meta") or {})
        meta.update({"baseline_expectancy_r":base,"marginal_association_r":round(delta,5) if delta is not None else None,"note":"Asociación histórica; no prueba causal."})
        item["meta"]=meta
        findings.append(item)

    ranked=top_findings(findings)
    ai_items=_ai_proposal_findings(rows, ai_proposals or [])
    factory_items=analyze_factory(rows)
    # Factory + IA are hypotheses, not authority.  We reserve visibility for
    # continuous generation without hiding the baseline component attribution.
    merged=[]; seen=set()
    streams = [factory_items[:40], ai_items[:12], ranked]
    cursors = [0, 0, 0]
    while len(merged) < FINDING_LIMIT and any(cursors[i] < len(streams[i]) for i in range(len(streams))):
        for i, stream in enumerate(streams):
            if cursors[i] >= len(stream):
                continue
            item = stream[cursors[i]]
            cursors[i] += 1
            key=str(item.get("feature_key") or "")
            if not key or key in seen:
                continue
            seen.add(key); merged.append(item)
            if len(merged)>=FINDING_LIMIT:
                break
    return merged
