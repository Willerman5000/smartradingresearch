from __future__ import annotations
import gc
import hashlib
import json
import os
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template, Response

import config
from db import RestDB, since_iso, utc_now
from engines import ENGINES
from engines.base import rss_mb, balanced_current_rows, source_coverage
from coverage_optimizer import (
    analyze_coverage_for_engine, all_coverage_cells, owner_for_cell, coverage_cell_id,
    optimize_cell, optimize_cell_candidates, retest_registry_promotions
)
from exchange_flow import current_exchange_flow_finding
from historical_market import cache_stats as causal_cache_stats

app = Flask(__name__)
central = RestDB(config.CENTRAL_URL, config.CENTRAL_KEY)
private = RestDB(config.PRIVATE_URL, config.PRIVATE_KEY)
_engine = ENGINES.get(config.ENGINE)
_job_lock = threading.Lock()
_start_lock = threading.Lock()
_state = {
    'running': False, 'last_started_at': None, 'last_finished_at': None,
    'last_error': None, 'last_run_id': None, 'last_source_rows': 0,
    'last_findings': 0, 'last_rss_mb': 0.0, 'last_source_coverage': {},
    'last_ai_proposals': 0, 'last_causal_cells': 0, 'last_causal_profitable': 0,
}


def _auth_ok() -> bool:
    if not config.RUN_TOKEN:
        return True
    supplied = request.headers.get('X-Research-Token') or request.args.get('token') or ''
    return supplied == config.RUN_TOKEN


def _source_rows(days: int, max_rows: int):
    """Read a bounded but timeframe-balanced source window.

    A single newest-first global LIMIT can be dominated by intraday Futures and
    can starve the strategic 4h/12h/1D/1W Spot evidence.  We reserve part of
    the same max_rows budget for those strategic Spot lanes, then sample every
    supported timeframe, and finally fill any unused capacity with the newest
    rows globally.  Total rows never exceeds max_rows.
    """
    strategic_spot = ("4h", "12h", "1D", "1W")
    all_timeframes = ("30m", "1h", "2h", "4h", "12h", "1D", "1W")
    selected = {}

    def add_rows(rows):
        for row in rows or []:
            if len(selected) >= max_rows:
                break
            key = str(row.get("id") or "")
            if key and key not in selected:
                selected[key] = row

    def lane(limit, *, timeframe=None, system_type=None):
        limit = max(0, min(int(limit or 0), max_rows - len(selected)))
        if limit <= 0:
            return []
        params = {
            'select': 'id,symbol,timeframe,system_type,action_normalized,status,created_at,entry_price,stop_loss,take_profit,risk_reward,context,signal_results',
            'created_at': f'gte.{since_iso(days)}',
            'order': 'created_at.desc',
        }
        if timeframe:
            params['timeframe'] = f'eq.{timeframe}'
        if system_type:
            params['system_type'] = f'eq.{system_type}'
        return central.paged_select(
            'analytics_quality_v2_compact_v1', params=params,
            max_rows=limit, page_size=min(config.PAGE_SIZE, max(1, limit)),
        )

    # 35% of the budget is reserved for the four Spot decision timeframes.
    strategic_budget = max(0, min(max_rows, int(round(max_rows * 0.35))))
    strategic_quota = strategic_budget // len(strategic_spot) if strategic_spot else 0
    for tf in strategic_spot:
        add_rows(lane(strategic_quota, timeframe=tf, system_type='spot'))

    # The remaining planned budget covers every timeframe, regardless of market.
    general_budget = max(0, max_rows - strategic_budget)
    general_quota = general_budget // len(all_timeframes) if all_timeframes else 0
    for tf in all_timeframes:
        add_rows(lane(general_quota, timeframe=tf))

    # Sparse long-TF lanes leave room; use it for the newest rows overall.
    remaining = max_rows - len(selected)
    if remaining > 0:
        add_rows(lane(remaining))

    rows = list(selected.values())
    rows.sort(key=lambda r: (str(r.get('created_at') or ''), str(r.get('id') or '')))
    return rows



def _load_ai_strategy_proposals(limit: int = 12):
    """Read structured Learning Scientist proposals from the central DB.

    The LLM only proposes hypotheses.  Strategy Research measures them against
    the same persisted source rows; Validation/Governance remains the only
    promotion path and production is never changed here.
    """
    if config.ENGINE != 'strategy':
        return []
    try:
        rows = central.select('ai_advisor_observations', params={
            'select': 'response_json,created_at,provider,model',
            'usage_type': 'eq.LEARNING',
            'context_type': 'eq.LEARNING',
            'order': 'created_at.desc',
            'limit': '20',
        })
    except Exception as exc:
        print(f'⚠️ AI proposal source: {exc}', flush=True)
        return []

    out=[]; seen=set()
    for row in rows or []:
        response=row.get('response_json') or {}
        if not isinstance(response, dict):
            continue
        proposals=response.get('strategy_proposals') or []
        if not isinstance(proposals, list):
            continue
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            if str(proposal.get('status') or '').upper() != 'SHADOW_PROPOSAL':
                continue
            if not bool(proposal.get('runtime_testable')):
                continue
            filters=proposal.get('research_filters') or {}
            if not isinstance(filters, dict) or not filters:
                continue
            key=str(proposal.get('proposal_id') or '') or json.dumps(filters, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            item=dict(proposal)
            item['provider']=row.get('provider')
            item['model']=row.get('model')
            item['created_at']=row.get('created_at')
            out.append(item)
            if len(out) >= max(1, int(limit)):
                return out
    return out


def _dataset_fingerprint(rows) -> str:
    h = hashlib.sha256()
    for row in rows or []:
        h.update(str(row.get('id') or '').encode('utf-8'))
        h.update(b'|')
        h.update(str(row.get('created_at') or '').encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()[:24]


def _record_run(run_id: str, status: str, **extra):
    row = {
        'run_id': run_id, 'engine': config.ENGINE, 'status': status,
        'research_version': config.VERSION, 'updated_at': utc_now(),
        'meta': extra,
    }
    try:
        private.upsert('research_runs_v1', [row], on_conflict='run_id')
    except Exception as exc:
        print(f'⚠️ run persistence: {exc}', flush=True)


def _heartbeat(**extra):
    row = {
        'engine': config.ENGINE,
        'research_version': config.VERSION,
        'last_seen_at': utc_now(),
        'status': 'RUNNING' if _state['running'] else 'IDLE',
        'rss_mb': round(rss_mb(), 2),
        'meta': extra,
    }
    try:
        private.upsert('research_engine_state_v1', [row], on_conflict='engine')
    except Exception as exc:
        print(f'⚠️ heartbeat persistence: {exc}', flush=True)


def _upsert_findings(findings, run_id: str, dataset_fingerprint: str):
    """Persist a batch without invalidating sibling findings. Returns current keys."""
    rows=[]
    current_keys=set()
    for item in findings or []:
        meta=dict(item.get('meta') or {})
        meta.update({
            'is_current': True,
            'research_run_id': run_id,
            'dataset_fingerprint': dataset_fingerprint,
        })
        feature_key=item['feature_key']
        current_keys.add(feature_key)
        rows.append({
            'engine': item['engine'], 'experiment': item['experiment'],
            'feature_key': feature_key, 'scope': item.get('scope') or {},
            'stage': item.get('stage') or 'INSUFFICIENT', 'authority': 'RESEARCH_ONLY',
            'metrics': item.get('metrics') or {}, 'meta': meta,
            'research_version': config.VERSION, 'updated_at': utc_now(),
        })
    if rows:
        central.upsert('research_findings_v1', rows, on_conflict='feature_key')
    return current_keys


def _mark_stale_findings(current_keys, run_id: str, engine: str | None = None):
    engine = str(engine or config.ENGINE)
    try:
        previous=central.select('research_findings_v1', params={
            'select':'feature_key,meta,research_version',
            'engine':f'eq.{engine}',
            'research_version':f'eq.{config.VERSION}',
            'limit':'800',
        })
        for old in previous:
            key=str(old.get('feature_key') or '')
            if not key or key in current_keys:
                continue
            meta=dict(old.get('meta') or {})
            meta.update({'is_current':False,'stale_after_run_id':run_id})
            central.patch('research_findings_v1', {'stage':'STALE','meta':meta,'updated_at':utc_now()}, filters={'feature_key':f'eq.{key}'})
    except Exception as exc:
        print(f'⚠️ stale findings: {exc}', flush=True)


def _persist_findings(findings, run_id: str, dataset_fingerprint: str):
    keys=_upsert_findings(findings, run_id, dataset_fingerprint)
    _mark_stale_findings(keys, run_id)
    return keys



def _stage_priority(stage):
    return {
        'SHADOW_READY_FAST': 60, 'SHADOW_READY': 55,
        'VALIDATION_REQUIRED': 40, 'VALIDATED_SINGLE_ASSET': 38,
        'OBSERVE': 25, 'REJECTED_OOS': 10, 'STALE': 0,
    }.get(str(stage or '').upper(), 20)


def _best_causal_per_cell(rows):
    """One representative per symbol×TF cell for coverage/UI only.

    All finalists remain persisted and available to Validation; this helper
    prevents 4 finalists from making a 54-cell matrix look like 216 cells.
    """
    best={}
    for row in rows or []:
        meta=row.get('meta') or {}
        cid=str(meta.get('coverage_cell_id') or '')
        if not cid:
            continue
        metrics=row.get('metrics') or {}
        val=metrics.get('validation') or {}
        score=(
            _stage_priority(row.get('stage')),
            float(val.get('expectancy_r') if val.get('expectancy_r') is not None else -999),
            float(val.get('profit_factor') if val.get('profit_factor') is not None else -999),
            int(val.get('resolved') or 0),
            -int(meta.get('finalist_rank_selection_only') or 999),
        )
        prev=best.get(cid)
        if prev is None or score > prev[0]:
            best[cid]=(score,row)
    return [x[1] for x in best.values()]


def _dashboard_rows():
    """Small read-only payload for the browser. Never loads source trading rows."""
    limit = int(getattr(config, 'DASHBOARD_MAX_ROWS', 120))
    fetch_limit = min(1200 if config.ENGINE == 'validation' else 500, max(limit * (8 if config.ENGINE == 'validation' else 3), limit))
    if config.ENGINE == 'validation':
        raw = central.select('research_promotions_v1', params={
            'select': 'candidate_key,source_engine,experiment,stage,reason,scope,metrics,meta,research_version,updated_at',
            'research_version': f'eq.{config.VERSION}',
            'order': 'updated_at.desc',
            'limit': str(fetch_limit),
        })
    else:
        raw = central.select('research_findings_v1', params={
            'select': 'feature_key,engine,experiment,stage,scope,metrics,meta,research_version,updated_at',
            'engine': f'eq.{config.ENGINE}',
            'research_version': f'eq.{config.VERSION}',
            'order': 'updated_at.desc',
            'limit': str(fetch_limit),
        })
    current = [r for r in raw if (r.get('meta') or {}).get('is_current') is True and str(r.get('stage') or '') != 'STALE']
    if config.ENGINE == 'validation':
        # I.2: show one representative for each of the 54 symbol×TF cells,
        # while keeping all pre-declared finalists persisted for Validation.
        causal_all=[r for r in current if str(r.get('experiment') or '') in {'CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE'}]
        causal=_best_causal_per_cell(causal_all)
        rest=[r for r in current if r not in causal_all]
        causal.sort(key=lambda r: str(((r.get('meta') or {}).get('coverage_cell_id') or '')))
        remaining=max(0, limit-len(causal))
        return causal[:limit] + balanced_current_rows(rest, remaining)
    return balanced_current_rows(current, limit)


def _compact_metric(row):
    metrics = row.get('metrics') or {}
    allm = metrics.get('all') or {}
    val = metrics.get('validation') or {}
    return {
        'key': row.get('candidate_key') or row.get('feature_key'),
        'engine': row.get('source_engine') or row.get('engine') or config.ENGINE,
        'experiment': row.get('experiment'),
        'stage': row.get('stage'),
        'reason': row.get('reason'),
        'scope': row.get('scope') or {},
        'resolved': allm.get('resolved'),
        'expectancy_r': allm.get('expectancy_r'),
        'profit_factor': allm.get('profit_factor'),
        'drawdown_r': allm.get('max_drawdown_r'),
        'validation_n': val.get('resolved'),
        'validation_expectancy_r': val.get('expectancy_r'),
        'validation_profit_factor': val.get('profit_factor'),
        'net_evidence_pct': allm.get('net_evidence_pct'),
        'symbols': allm.get('symbols') or [],
        'timeframes': allm.get('timeframes') or [],
        'meta': row.get('meta') or {},
        'updated_at': row.get('updated_at'),
    }


def _markdown_report(items):
    title = f'Research Federation · {config.ENGINE.upper()} · {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'
    lines=[f'# {title}', '', f'- Versión: {config.VERSION}', f'- Autoridad: RESEARCH_ONLY', f'- RSS actual: {rss_mb():.1f} MB', f'- Elementos mostrados: {len(items)}', '']
    causal_all=[x for x in items if str(x.get('experiment') or '') in {'CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE'}]
    causal=_best_causal_per_cell(causal_all)
    if causal:
        required=int(getattr(config,'CAUSAL_REQUIRED_CELLS',54))
        selection_positive=sum(1 for x in causal if str((x.get('meta') or {}).get('coverage_status') or '')=='SELECTION_PROFITABLE')
        oos_positive=sum(1 for x in causal if x.get('validation_expectancy_r') is not None and float(x.get('validation_expectancy_r') or 0)>0 and (x.get('validation_profit_factor') is None or float(x.get('validation_profit_factor') or 0)>1.0))
        shadow_ready=sum(1 for x in causal if str(x.get('stage') or '') in {'SHADOW_READY','SHADOW_READY_FAST'})
        lines += [
            '## FINAL V1 RC4 · Cobertura especialista de rentabilidad',
            f'- Celdas símbolo×temporalidad visibles: {len(causal)}/{required}',
            f'- Celdas con Selection positiva: {selection_positive}',
            f'- Celdas con OOS final positivo: {oos_positive}',
            f'- Celdas SHADOW_READY: {shadow_ready}',
            f'- Celdas pendientes de especialista validado: {max(0, required-shadow_ready)}',
            f'- Cobertura investigada completa: {"SI" if len(causal)>=required else "NO"}',
            '> Objetivo: al menos un especialista rentable validado por cada celda. Si una celda no demuestra edge, permanece SEARCHING/VALIDATION y vuelve al ciclo; nunca se fuerza un resultado.',
            '> Discovery 60% → Selection Holdout 20% → Final OOS 20%. Final OOS no se usa para elegir finalistas.',
            ''
        ]
        for it in sorted(causal, key=lambda x: str((x.get('meta') or {}).get('coverage_cell_id') or '')):
            meta=it.get('meta') or {}; cell=meta.get('coverage_cell') or {}
            lines.append(f"- {it.get('stage')} | {cell.get('market_family')} {cell.get('symbol')} {cell.get('timeframe')} | {meta.get('causal_strategy_family')} | N={it.get('resolved')} | OOS.N={it.get('validation_n')} | OOS.Exp.R={it.get('validation_expectancy_r')} | OOS.PF={it.get('validation_profit_factor')}")
        lines.append('')
    ordered=sorted(items, key=lambda x: ((x.get('validation_expectancy_r') is not None), x.get('validation_expectancy_r') or -999, x.get('validation_n') or 0), reverse=True)
    lines.append('## Evidencia principal')
    lines.append('> Filas CAUSAL_COVERAGE_STRATEGY son replay causal. Las demás conservan atribución observacional con holdout temporal.')
    for it in ordered[:25]:
        scope=', '.join(f'{k}={v}' for k,v in (it.get('scope') or {}).items())
        lines.append(f"- {it.get('stage')} | {it.get('experiment')} | {scope} | N={it.get('resolved')} | OOS/Holdout.N={it.get('validation_n')} | Exp.R={it.get('expectancy_r')} | OOS/Holdout.Exp.R={it.get('validation_expectancy_r')} | PF={it.get('validation_profit_factor')}")
    if config.ENGINE == 'validation':
        lines += ['', '## Nota de gobernanza', 'Validation clasifica evidencia causal y observacional y recomienda Shadow/Canary. No concede autoridad productiva ni garantiza rentabilidad futura.']
    return '\n'.join(lines)


def _current_causal_cell_ids():
    try:
        rows=central.paged_select('research_findings_v1', params={
            'select':'feature_key,engine,experiment,meta,research_version,stage,updated_at',
            'research_version':f'eq.{config.VERSION}',
            'experiment':'eq.CAUSAL_COVERAGE_STRATEGY',
            'order':'updated_at.desc',
        }, max_rows=1200, page_size=300)
    except Exception:
        return set()
    out=set()
    for row in rows or []:
        meta=row.get('meta') or {}
        if meta.get('is_current') is False or str(row.get('stage') or '') == 'STALE':
            continue
        cid=str(meta.get('coverage_cell_id') or '')
        if cid:
            out.add(cid)
    return out


def _rescue_missing_causal_cells(run_id: str):
    """Validation is the fifth research instance: fill missing 54-cell specialists.

    Owners remain execution/risk/strategy/traders. Rescue computes only missing
    symbol×TF cells and persists all pre-declared finalists.
    """
    if not bool(getattr(config,'CAUSAL_VALIDATION_RESCUE',True)):
        return 0
    present=_current_causal_cell_ids()
    missing=[cell for cell in all_coverage_cells() if coverage_cell_id(cell) not in present]
    cap=int(getattr(config,'CAUSAL_VALIDATION_RESCUE_MAX_CELLS',12))
    missing=missing[:cap]
    if not missing:
        return 0
    print(f'🧯 [I.2 VALIDATION] rescate causal faltantes={len(missing)} presentes={len(present)}/{getattr(config, "CAUSAL_REQUIRED_CELLS", 54)}', flush=True)
    done=0
    for cell in missing:
        if rss_mb() >= getattr(config,'MEMORY_HARD_MB',430):
            break
        owner=owner_for_cell(cell)
        findings=optimize_cell_candidates(cell, owner_engine=owner)
        _upsert_findings(findings, run_id, f'validation-rescue:{coverage_cell_id(cell)}')
        done += 1
    return done


def _run_validation(run_id: str):
    _rescue_missing_causal_cells(run_id)
    raw = central.paged_select('research_findings_v1', params={
        'select':'engine,experiment,feature_key,scope,stage,metrics,meta,research_version,updated_at',
        'research_version':f'eq.{config.VERSION}',
        'order':'updated_at.desc',
    }, max_rows=6000, page_size=600)
    rows=[r for r in raw if (r.get('meta') or {}).get('is_current') is True and str(r.get('stage') or '')!='STALE']
    promotions = _engine.analyze_findings(rows)
    current_keys={str(p.get('candidate_key') or '') for p in promotions}
    for p in promotions:
        meta=dict(p.get('meta') or {})
        meta.update({'is_current':True,'validation_run_id':run_id})
        p['meta']=meta
    if promotions:
        central.upsert('research_promotions_v1', promotions, on_conflict='candidate_key')
    try:
        old=central.select('research_promotions_v1', params={
            'select':'candidate_key,meta,research_version',
            'research_version':f'eq.{config.VERSION}',
            'limit':'500',
        })
        for item in old:
            key=str(item.get('candidate_key') or '')
            if not key or key in current_keys:
                continue
            meta=dict(item.get('meta') or {})
            meta.update({'is_current':False,'stale_after_validation_run_id':run_id})
            central.patch('research_promotions_v1', {'stage':'STALE','meta':meta,'updated_at':utc_now()}, filters={'candidate_key':f'eq.{key}'})
    except Exception as exc:
        print(f'⚠️ stale promotions: {exc}', flush=True)
    return len(rows), len(promotions)


def _causal_retest_promotions_for_engine(engine: str):
    """Load incumbents and prioritize those whose Shadow/live diverges."""
    try:
        limit=max(30, int(getattr(config,'CAUSAL_REGISTRY_RETEST_LIMIT',16))*4)
        rows=central.select('research_promotions_v1', params={
            'select':'candidate_key,source_engine,experiment,stage,scope,metrics,meta,research_version,updated_at',
            'source_engine':f'eq.{engine}',
            'experiment':'in.(CAUSAL_COVERAGE_STRATEGY,CAUSAL_REGISTRY_RETEST,CAUSAL_SHADOW_RECYCLE)',
            'stage':'in.(SHADOW_READY,SHADOW_READY_FAST,VALIDATION_REQUIRED,REJECTED_OOS)',
            'order':'updated_at.desc',
            'limit':str(limit),
        })
        try:
            shadow=central.select('research_shadow_live_metrics_v1', params={
                'select':'candidate_key,resolved_n,signals_n,expectancy_r,profit_factor,updated_at',
                'order':'updated_at.desc','limit':'500',
            })
        except Exception:
            shadow=[]
        smap={}
        for item in shadow or []:
            key=str(item.get('candidate_key') or '')
            if key and key not in smap:
                smap[key]=item
        enriched=[]
        for row in rows or []:
            meta=dict(row.get('meta') or {})
            live=smap.get(str(row.get('candidate_key') or '')) or {}
            target=max(1,int(meta.get('recommended_shadow_target') or 0) or 1)
            resolved=int(live.get('resolved_n') or 0)
            exp=live.get('expectancy_r'); pf=live.get('profit_factor')
            priority=0
            try:
                if resolved>=target and (exp is None or float(exp)<=0.05 or (pf is not None and float(pf)<1.05)):
                    priority=3
                elif resolved>=max(3,target//2) and exp is not None and float(exp)<=-0.20:
                    priority=2
                elif resolved>0:
                    priority=1
            except Exception:
                priority=0
            meta['shadow_recycle_priority']=priority
            meta['shadow_live_snapshot']=live
            row=dict(row); row['meta']=meta
            enriched.append(row)
        enriched.sort(key=lambda r:(int((r.get('meta') or {}).get('shadow_recycle_priority') or 0), str(r.get('updated_at') or '')), reverse=True)
        return enriched
    except Exception as exc:
        print(f'⚠️ [I.2 RETEST] source: {exc}', flush=True)
        return []


def _priority_cells_for_engine(engine: str):
    """Put uncovered/unvalidated cells first when RAM/time is scarce."""
    try:
        rows=central.select('research_promotions_v1', params={
            'select':'source_engine,stage,meta,research_version,updated_at',
            'source_engine':f'eq.{engine}',
            'research_version':f'eq.{config.VERSION}',
            'order':'updated_at.desc','limit':'300',
        })
    except Exception:
        return set()
    seen=set(); priority=set()
    for row in rows or []:
        meta=row.get('meta') or {}; cid=str(meta.get('coverage_cell_id') or '')
        if not cid or cid in seen or meta.get('is_current') is False:
            continue
        seen.add(cid)
        if str(row.get('stage') or '').upper() not in {'SHADOW_READY','SHADOW_READY_FAST'}:
            priority.add(cid)
    # Cells with no promotion row are naturally missing from seen and are
    # prioritized too.
    for cell in all_coverage_cells():
        if owner_for_cell(cell)==str(engine).lower() and coverage_cell_id(cell) not in seen:
            priority.add(coverage_cell_id(cell))
    return priority


def _run_job(days: int, max_rows: int):
    run_id = str(uuid.uuid4())
    with _job_lock:
        _state.update(running=True, last_started_at=utc_now(), last_error=None, last_run_id=run_id)
        _record_run(run_id, 'RUNNING', days=days, max_rows=max_rows)
        print(f'🔬 [{config.ENGINE}] run={run_id[:8]} inicio rss={rss_mb():.1f}MB', flush=True)
        try:
            if rss_mb() >= config.MEMORY_HARD_MB:
                raise MemoryError(f'preflight RSS {rss_mb():.1f}MB >= {config.MEMORY_HARD_MB}MB')
            if config.ENGINE == 'validation':
                source_count, finding_count = _run_validation(run_id)
            else:
                rows = _source_rows(days, max_rows)
                source_count = len(rows)
                _state['last_source_coverage'] = source_coverage(rows)
                if rss_mb() >= config.MEMORY_HARD_MB:
                    raise MemoryError(f'RSS after source {rss_mb():.1f}MB >= {config.MEMORY_HARD_MB}MB')
                fingerprint = _dataset_fingerprint(rows)
                ai_proposals = _load_ai_strategy_proposals()
                _state['last_ai_proposals'] = len(ai_proposals)
                if config.ENGINE == 'strategy':
                    findings = _engine.analyze(rows, ai_proposals=ai_proposals)
                else:
                    findings = _engine.analyze(rows)

                # I.1 publishes observational evidence immediately, then every
                # causal cell as soon as it finishes. A slow 5m replay can no
                # longer hide already-completed sibling cells from Validation.
                current_keys=set(_upsert_findings(findings, run_id, fingerprint))
                causal_findings=[]
                def _publish_causal(finding):
                    current_keys.update(_upsert_findings([finding], run_id, fingerprint))
                causal_findings = analyze_coverage_for_engine(
                    config.ENGINE,
                    on_finding=_publish_causal,
                    priority_cell_ids=_priority_cells_for_engine(config.ENGINE),
                )
                _state['last_causal_cells'] = len({
                    str((x.get('meta') or {}).get('coverage_cell_id') or '')
                    for x in causal_findings if (x.get('meta') or {}).get('coverage_cell_id')
                })
                _state['last_causal_profitable'] = len({
                    str((x.get('meta') or {}).get('coverage_cell_id') or '')
                    for x in causal_findings
                    if str((x.get('meta') or {}).get('coverage_status') or '') == 'SELECTION_PROFITABLE'
                })

                # Rebacktest strategies already in Shadow/Validation on the newest
                # historical window. This is separate from live Shadow evidence.
                retest_source=_causal_retest_promotions_for_engine(config.ENGINE)
                retest_findings=retest_registry_promotions(retest_source, config.ENGINE, on_finding=_publish_causal)

                if config.ENGINE == 'strategy':
                    flow = current_exchange_flow_finding()
                    if flow:
                        findings.append(flow)
                        current_keys.update(_upsert_findings([flow], run_id, fingerprint))

                finding_count = len(findings) + len(causal_findings) + len(retest_findings)
                _mark_stale_findings(current_keys, run_id, engine=config.ENGINE)
                del rows, findings, causal_findings, retest_findings
            try:
                central.rpc('cleanup_research_federation_v1', {})
            except Exception:
                pass
            _state.update(last_source_rows=source_count, last_findings=finding_count, last_finished_at=utc_now())
            _record_run(run_id, 'SUCCESS', source_rows=source_count, findings=finding_count, rss_mb=round(rss_mb(),2))
            print(f'✅ [{config.ENGINE}] source={source_count} findings={finding_count} rss={rss_mb():.1f}MB', flush=True)
        except Exception as exc:
            _state['last_error'] = f'{type(exc).__name__}: {exc}'
            _state['last_finished_at'] = utc_now()
            _record_run(run_id, 'ERROR', error=_state['last_error'])
            print(f'❌ [{config.ENGINE}] {_state["last_error"]}\n{traceback.format_exc()}', flush=True)
        finally:
            _state['running'] = False
            gc.collect()
            _state['last_rss_mb'] = round(rss_mb(),2)
            _heartbeat(last_run_id=run_id, last_error=_state.get('last_error'))


def _start_job(days=None, max_rows=None):
    with _start_lock:
        if _state['running']:
            return False
        # Reserve the slot before spawning so /run and auto-loop cannot race.
        _state['running'] = True
        days = max(30, min(730, int(days or config.WINDOW_DAYS)))
        max_rows = max(200, min(20000, int(max_rows or config.MAX_SOURCE_ROWS)))
        try:
            threading.Thread(target=_run_job, args=(days,max_rows), name=f'research-{config.ENGINE}', daemon=True).start()
        except Exception:
            _state['running'] = False
            raise
        return True


@app.get('/health')
def health():
    return jsonify({
        'ok': True, 'engine': config.ENGINE, 'version': config.VERSION,
        'authority': 'RESEARCH_ONLY', 'running': _state['running'],
        'rss_mb': round(rss_mb(),2), 'central_db': central.ready, 'private_db': private.ready,
        'causal_cache': causal_cache_stats(),
    })


@app.get('/status')
def status():
    return jsonify({'ok': True, 'engine': config.ENGINE, 'state': _state, 'version': config.VERSION})


@app.post('/run')
def run_now():
    if not _auth_ok():
        return jsonify({'ok':False,'error':'unauthorized'}), 401
    body = request.get_json(silent=True) or {}
    started = _start_job(body.get('days'), body.get('max_rows'))
    return jsonify({'ok':True, 'started':started, 'running':_state['running'], 'engine':config.ENGINE}), (202 if started else 200)



@app.get('/')
def dashboard_page():
    return render_template('dashboard.html', engine=config.ENGINE, version=config.VERSION)


@app.get('/api/dashboard')
def dashboard_api():
    try:
        rows = _dashboard_rows()
        items = [_compact_metric(r) for r in rows]
        stages = {}
        for item in items:
            stage = str(item.get('stage') or 'UNKNOWN')
            stages[stage] = stages.get(stage, 0) + 1
        return jsonify({
            'ok': True, 'engine': config.ENGINE, 'version': config.VERSION,
            'authority': 'RESEARCH_ONLY', 'rss_mb': round(rss_mb(),2),
            'running': _state['running'], 'stages': stages, 'items': items,
            'coverage': source_coverage(rows), 'causal_cache': causal_cache_stats(),
            'last_run': {k:_state.get(k) for k in ('last_started_at','last_finished_at','last_error','last_source_rows','last_findings','last_source_coverage','last_ai_proposals','last_causal_cells','last_causal_profitable')},
        })
    except Exception as exc:
        return jsonify({'ok':False,'engine':config.ENGINE,'error':str(exc)[:240]}), 500


@app.get('/api/export/summary')
def export_summary():
    try:
        items=[_compact_metric(r) for r in _dashboard_rows()]
        return Response(_markdown_report(items), mimetype='text/markdown; charset=utf-8')
    except Exception as exc:
        return Response(f'# Error de exportación\n\n{type(exc).__name__}: {exc}', status=500, mimetype='text/plain; charset=utf-8')


def _auto_loop():
    # Validation starts later so the four evidence engines can publish their
    # current RF V1.3 snapshot first after a simultaneous Render deploy.
    initial_delay = config.BOOT_DELAY_SECONDS
    if config.ENGINE == 'validation':
        initial_delay = max(initial_delay, 180)
    time.sleep(initial_delay)
    while True:
        try:
            if not _state['running']:
                _start_job()
        except Exception as exc:
            print(f'⚠️ auto-loop {config.ENGINE}: {exc}', flush=True)
        time.sleep(config.AUTO_INTERVAL_MINUTES * 60)


if config.ENGINE not in config.VALID_ENGINES:
    raise RuntimeError(f'RESEARCH_ENGINE inválido: {config.ENGINE}')
if config.AUTO_RUN:
    threading.Thread(target=_auto_loop, name='research-auto-loop', daemon=True).start()
