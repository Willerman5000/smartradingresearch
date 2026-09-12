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
from engines.base import rss_mb

app = Flask(__name__)
central = RestDB(config.CENTRAL_URL, config.CENTRAL_KEY)
private = RestDB(config.PRIVATE_URL, config.PRIVATE_KEY)
_engine = ENGINES.get(config.ENGINE)
_job_lock = threading.Lock()
_start_lock = threading.Lock()
_state = {
    'running': False, 'last_started_at': None, 'last_finished_at': None,
    'last_error': None, 'last_run_id': None, 'last_source_rows': 0,
    'last_findings': 0, 'last_rss_mb': 0.0,
}


def _auth_ok() -> bool:
    if not config.RUN_TOKEN:
        return True
    supplied = request.headers.get('X-Research-Token') or request.args.get('token') or ''
    return supplied == config.RUN_TOKEN


def _source_rows(days: int, max_rows: int):
    # Read the newest bounded window first.  The old ASC+limit behaviour could
    # keep the oldest rows forever once the table grew beyond max_rows.
    rows = central.paged_select(
        'analytics_quality_v2_compact_v1',
        params={
            'select': 'id,symbol,timeframe,system_type,action_normalized,status,created_at,entry_price,stop_loss,take_profit,risk_reward,context,signal_results',
            'created_at': f'gte.{since_iso(days)}',
            'order': 'created_at.desc',
        }, max_rows=max_rows, page_size=config.PAGE_SIZE,
    )
    rows.reverse()
    return rows


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


def _persist_findings(findings, run_id: str, dataset_fingerprint: str):
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

    # Invalidate findings from previous runs of this same engine. Validation
    # must never promote a candidate that vanished from the latest evidence.
    try:
        previous=central.select('research_findings_v1', params={
            'select':'feature_key,meta,research_version',
            'engine':f'eq.{config.ENGINE}',
            'limit':'500',
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




def _dashboard_rows():
    """Small read-only payload for the browser. Never loads source trading rows."""
    limit = int(getattr(config, 'DASHBOARD_MAX_ROWS', 120))
    fetch_limit = min(500, max(limit * 3, limit))
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
    return [r for r in raw if (r.get('meta') or {}).get('is_current') is True][:limit]


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
    ordered=sorted(items, key=lambda x: ((x.get('validation_expectancy_r') is not None), x.get('validation_expectancy_r') or -999, x.get('validation_n') or 0), reverse=True)
    lines.append('## Evidencia principal · Discovery / Holdout temporal')
    lines.append('> Nota: RF V1.3 mide atribución observacional. No presenta estas filas como replay causal de velas.')
    for it in ordered[:20]:
        scope=', '.join(f'{k}={v}' for k,v in (it.get('scope') or {}).items())
        lines.append(f"- {it.get('stage')} | {it.get('experiment')} | {scope} | Discovery.N={it.get('resolved')} | Holdout.N={it.get('validation_n')} | Discovery.Exp.R={it.get('expectancy_r')} | Holdout.Exp.R={it.get('validation_expectancy_r')} | PF.Holdout={it.get('validation_profit_factor')}")
    if config.ENGINE == 'validation':
        lines += ['', '## Nota de gobernanza', 'Validation sólo clasifica evidencia y recomienda Shadow/Canary. No concede autoridad de producción ni garantiza rentabilidad futura.']
    return '\n'.join(lines)


def _run_validation(run_id: str):
    raw = central.paged_select('research_findings_v1', params={
        'select':'engine,experiment,feature_key,scope,stage,metrics,meta,research_version,updated_at',
        'research_version':f'eq.{config.VERSION}',
        'order':'updated_at.desc',
    }, max_rows=3000, page_size=500)
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
                if rss_mb() >= config.MEMORY_HARD_MB:
                    raise MemoryError(f'RSS after source {rss_mb():.1f}MB >= {config.MEMORY_HARD_MB}MB')
                fingerprint = _dataset_fingerprint(rows)
                findings = _engine.analyze(rows)
                finding_count = len(findings)
                _persist_findings(findings, run_id, fingerprint)
                del rows, findings
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
            'last_run': {k:_state.get(k) for k in ('last_started_at','last_finished_at','last_error','last_source_rows','last_findings')},
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
