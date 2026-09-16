-- RC8 Knowledge Core · Free Plan
-- Guarda conocimiento validado; laboratorio no validado queda efimero/compactado.

create schema if not exists maintenance;

create table if not exists public.research_champions_v1 (
    cell_key text primary key,
    research_version text not null,
    market_family text not null,
    symbol text not null,
    timeframe text not null,
    candidate_key text not null,
    source_engine text,
    experiment text,
    strategy_id text,
    strategy_family text,
    direction text,
    regime text,
    strategy_fingerprint text,
    strategy_spec jsonb not null default '{}'::jsonb,
    strategy_card jsonb not null default '{}'::jsonb,
    oos_n integer not null default 0,
    oos_wr_pct numeric,
    oos_expectancy_r numeric,
    oos_profit_factor numeric,
    oos_max_drawdown_r numeric,
    alpha_state text not null default 'HEALTHY',
    alpha_detail jsonb not null default '{}'::jsonb,
    status text not null default 'SHADOW',
    champion_since timestamptz not null default now(),
    last_validated_at timestamptz,
    updated_at timestamptz not null default now()
);

create index if not exists idx_research_champions_status on public.research_champions_v1(status, updated_at desc);
create unique index if not exists uq_research_champions_candidate on public.research_champions_v1(candidate_key);
alter table public.research_champions_v1 enable row level security;

create table if not exists public.research_validated_history_v1 (
    strategy_fingerprint text primary key,
    cell_key text not null,
    research_version text,
    candidate_key text,
    strategy_id text,
    strategy_family text,
    direction text,
    regime text,
    strategy_spec jsonb not null default '{}'::jsonb,
    oos_n integer not null default 0,
    oos_wr_pct numeric,
    oos_expectancy_r numeric,
    oos_profit_factor numeric,
    oos_max_drawdown_r numeric,
    disposition text not null default 'VALIDATED_HISTORY',
    first_validated_at timestamptz,
    last_validated_at timestamptz,
    updated_at timestamptz not null default now()
);
create index if not exists idx_research_validated_history_cell on public.research_validated_history_v1(cell_key, updated_at desc);
alter table public.research_validated_history_v1 enable row level security;

create table if not exists public.research_candidate_memory_v1 (
    memory_key text primary key,
    cell_key text not null,
    strategy_id text,
    strategy_family text,
    direction text,
    disposition text not null,
    reason text,
    oos_n integer not null default 0,
    oos_expectancy_r numeric,
    oos_profit_factor numeric,
    oos_max_drawdown_r numeric,
    test_count integer not null default 1,
    first_tested_at timestamptz,
    last_tested_at timestamptz,
    retest_after timestamptz,
    updated_at timestamptz not null default now()
);
create index if not exists idx_research_candidate_memory_cell on public.research_candidate_memory_v1(cell_key, retest_after, updated_at desc);
create index if not exists idx_research_candidate_memory_strategy on public.research_candidate_memory_v1(strategy_id) where strategy_id is not null;
alter table public.research_candidate_memory_v1 enable row level security;

create table if not exists public.research_governance_snapshot_v1 (
    id smallint primary key default 1 check (id = 1),
    research_version text,
    updated_at timestamptz not null default now(),
    target_cells integer not null default 46,
    champion_count integer not null default 0,
    pending_count integer not null default 46,
    champions jsonb not null default '[]'::jsonb,
    shadow jsonb not null default '[]'::jsonb,
    alpha_summary jsonb not null default '{}'::jsonb
);
alter table public.research_governance_snapshot_v1 enable row level security;

-- Backfill every historically validated StrategySpec into compact validated history.
insert into public.research_validated_history_v1 (
    strategy_fingerprint,cell_key,research_version,candidate_key,strategy_id,strategy_family,direction,regime,
    strategy_spec,oos_n,oos_wr_pct,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,
    disposition,first_validated_at,last_validated_at,updated_at
)
select distinct on (fp)
    fp,
    cell_key,
    p.research_version,
    p.candidate_key,
    p.meta->>'causal_strategy_id',
    coalesce(p.meta#>>'{causal_strategy_spec,family}',p.meta->>'causal_strategy_family','UNKNOWN'),
    coalesce(p.scope->>'direction',p.meta#>>'{causal_strategy_spec,direction_mode}','BOTH'),
    coalesce(p.scope->>'regime','ALL'),
    coalesce(p.meta->'causal_strategy_spec','{}'::jsonb),
    coalesce((p.metrics#>>'{validation,resolved}')::int,0),
    nullif(p.metrics#>>'{validation,win_rate_pct}','')::numeric,
    nullif(p.metrics#>>'{validation,expectancy_r}','')::numeric,
    nullif(p.metrics#>>'{validation,profit_factor}','')::numeric,
    nullif(p.metrics#>>'{validation,max_drawdown_r}','')::numeric,
    'VALIDATED_HISTORY',p.updated_at,p.updated_at,now()
from (
    select p.*,
      coalesce(
        nullif(p.meta#>>'{strategy_card,fingerprint_sha256}',''),
        nullif(p.meta->>'causal_strategy_id',''),
        md5(p.candidate_key)
      ) as fp,
      upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) as cell_key
    from public.research_promotions_v1 p
    where p.stage in ('SHADOW_READY','SHADOW_READY_FAST')
      and coalesce(p.meta->'causal_strategy_spec','{}'::jsonb) <> '{}'::jsonb
) p
order by fp,p.updated_at desc
on conflict (strategy_fingerprint) do update set
    last_validated_at=excluded.last_validated_at,
    oos_n=excluded.oos_n,
    oos_wr_pct=excluded.oos_wr_pct,
    oos_expectancy_r=excluded.oos_expectancy_r,
    oos_profit_factor=excluded.oos_profit_factor,
    oos_max_drawdown_r=excluded.oos_max_drawdown_r,
    updated_at=now();

-- Seed ONLY the nine active Champions certified by the latest Validation report.
with desired(market_family,symbol,timeframe,strategy_family) as (
    values
      ('CRYPTO_FUTURES','ADA-USDT','2H','SWEEP_REVERSAL'),
      ('CRYPTO_FUTURES','ADA-USDT','4H','BOLLINGER_SQUEEZE'),
      ('CRYPTO_FUTURES','ETH-USDT','2H','RSI_TREND'),
      ('CRYPTO_FUTURES','LINK-USDT','2H','RSI_MAVERICK_REVERSAL'),
      ('CRYPTO_FUTURES','SOL-USDT','2H','SUPERTREND_PULLBACK'),
      ('CRYPTO_FUTURES','XRP-USDT','2H','TREND_CONTINUATION'),
      ('CRYPTO_SPOT','BTC-USDT','12H','MOMENTUM_BREAKOUT'),
      ('PAXG_BTC','PAXG-BTC','12H','VWAP_REVERSION'),
      ('PAXG_USDT','PAXG-USDT','12H','MULTI_RSI_TREND')
), ranked as (
  select p.*,
         d.strategy_family as wanted_family,
         row_number() over (
           partition by d.market_family,d.symbol,d.timeframe
           order by p.updated_at desc
         ) rn
  from desired d
  join public.research_promotions_v1 p
    on p.scope->>'market_family'=d.market_family
   and upper(p.scope->>'symbol')=d.symbol
   and upper(p.scope->>'timeframe')=d.timeframe
   and p.meta#>>'{causal_strategy_spec,family}'=d.strategy_family
   and p.stage in ('SHADOW_READY','SHADOW_READY_FAST')
   and p.research_version='RFV1_12_RC5_ITERATIVE_EDGE_46CELL_20260915'
)
insert into public.research_champions_v1 (
  cell_key,research_version,market_family,symbol,timeframe,candidate_key,source_engine,experiment,
  strategy_id,strategy_family,direction,regime,strategy_fingerprint,strategy_spec,strategy_card,
  oos_n,oos_wr_pct,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,alpha_state,alpha_detail,
  status,champion_since,last_validated_at,updated_at
)
select
  upper(concat_ws('|',scope->>'market_family',scope->>'symbol',scope->>'timeframe')),
  research_version,scope->>'market_family',upper(scope->>'symbol'),upper(scope->>'timeframe'),candidate_key,
  source_engine,experiment,meta->>'causal_strategy_id',wanted_family,
  coalesce(scope->>'direction',meta#>>'{causal_strategy_spec,direction_mode}','BOTH'),coalesce(scope->>'regime','ALL'),
  coalesce(nullif(meta#>>'{strategy_card,fingerprint_sha256}',''),nullif(meta->>'causal_strategy_id',''),md5(candidate_key)),
  coalesce(meta->'causal_strategy_spec','{}'::jsonb),coalesce(meta->'strategy_card','{}'::jsonb),
  coalesce((metrics#>>'{validation,resolved}')::int,0),nullif(metrics#>>'{validation,win_rate_pct}','')::numeric,
  nullif(metrics#>>'{validation,expectancy_r}','')::numeric,nullif(metrics#>>'{validation,profit_factor}','')::numeric,
  nullif(metrics#>>'{validation,max_drawdown_r}','')::numeric,
  coalesce(meta#>>'{alpha_decay_health,state}','HEALTHY'),coalesce(meta->'alpha_decay_health','{}'::jsonb),
  'SHADOW',updated_at,updated_at,now()
from ranked where rn=1
on conflict (cell_key) do update set
  research_version=excluded.research_version,candidate_key=excluded.candidate_key,source_engine=excluded.source_engine,
  experiment=excluded.experiment,strategy_id=excluded.strategy_id,strategy_family=excluded.strategy_family,
  direction=excluded.direction,regime=excluded.regime,strategy_fingerprint=excluded.strategy_fingerprint,
  strategy_spec=excluded.strategy_spec,strategy_card=excluded.strategy_card,oos_n=excluded.oos_n,
  oos_wr_pct=excluded.oos_wr_pct,oos_expectancy_r=excluded.oos_expectancy_r,oos_profit_factor=excluded.oos_profit_factor,
  oos_max_drawdown_r=excluded.oos_max_drawdown_r,last_validated_at=excluded.last_validated_at,updated_at=now();

-- Compact failed/non-Champion candidates into tiny memory: enough to avoid exact repeats.
with raw as (
 select
  md5(upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) || '|' || coalesce(p.meta->>'causal_strategy_id',p.candidate_key)) memory_key,
  upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) cell_key,
  p.meta->>'causal_strategy_id' strategy_id,
  coalesce(p.meta#>>'{causal_strategy_spec,family}',p.meta->>'causal_strategy_family',p.experiment) strategy_family,
  coalesce(p.scope->>'direction',p.meta#>>'{causal_strategy_spec,direction_mode}','BOTH') direction,
  p.stage disposition,p.reason,coalesce((p.metrics#>>'{validation,resolved}')::int,0) oos_n,
  nullif(p.metrics#>>'{validation,expectancy_r}','')::numeric oos_expectancy_r,
  nullif(p.metrics#>>'{validation,profit_factor}','')::numeric oos_profit_factor,
  nullif(p.metrics#>>'{validation,max_drawdown_r}','')::numeric oos_max_drawdown_r,
  p.updated_at,
  case when p.stage='REJECTED_OOS' then p.updated_at+interval '7 days' when p.stage='STALE' then p.updated_at+interval '3 days' else p.updated_at+interval '1 day' end retest_after,
  row_number() over(partition by md5(upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) || '|' || coalesce(p.meta->>'causal_strategy_id',p.candidate_key)) order by p.updated_at desc) rn,
  count(*) over(partition by md5(upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) || '|' || coalesce(p.meta->>'causal_strategy_id',p.candidate_key))) seen_count,
  min(p.updated_at) over(partition by md5(upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) || '|' || coalesce(p.meta->>'causal_strategy_id',p.candidate_key))) first_seen_at
 from public.research_promotions_v1 p
 where not exists(select 1 from public.research_champions_v1 c where c.candidate_key=p.candidate_key)
)
insert into public.research_candidate_memory_v1(memory_key,cell_key,strategy_id,strategy_family,direction,disposition,reason,oos_n,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,test_count,first_tested_at,last_tested_at,retest_after,updated_at)
select memory_key,cell_key,strategy_id,strategy_family,direction,disposition,reason,oos_n,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,seen_count,first_seen_at,updated_at,retest_after,now()
from raw where rn=1
on conflict(memory_key) do update set disposition=excluded.disposition,reason=excluded.reason,oos_n=excluded.oos_n,
 oos_expectancy_r=excluded.oos_expectancy_r,oos_profit_factor=excluded.oos_profit_factor,oos_max_drawdown_r=excluded.oos_max_drawdown_r,
 test_count=greatest(public.research_candidate_memory_v1.test_count,excluded.test_count),
 first_tested_at=least(public.research_candidate_memory_v1.first_tested_at,excluded.first_tested_at),
 last_tested_at=greatest(public.research_candidate_memory_v1.last_tested_at,excluded.last_tested_at),
 retest_after=greatest(public.research_candidate_memory_v1.retest_after,excluded.retest_after),updated_at=now();

create or replace function maintenance.refresh_research_governance_snapshot_v1()
returns void language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare
  v_version text;
  v_champions jsonb;
  v_shadow jsonb;
  v_alpha jsonb;
  v_count integer;
begin
  select research_version into v_version from public.research_champions_v1 order by updated_at desc limit 1;
  select count(*) into v_count from public.research_champions_v1 where status <> 'DEGRADED';
  select coalesce(jsonb_agg(jsonb_build_object(
    'cell_key',c.cell_key,'market_family',c.market_family,'symbol',c.symbol,'timeframe',c.timeframe,
    'candidate_key',c.candidate_key,'source_engine',c.source_engine,'experiment',c.experiment,
    'strategy_id',c.strategy_id,'strategy_family',c.strategy_family,'direction',c.direction,'regime',c.regime,
    'strategy_fingerprint',c.strategy_fingerprint,'strategy_spec',c.strategy_spec,'strategy_card',c.strategy_card,
    'oos_n',c.oos_n,'oos_wr_pct',c.oos_wr_pct,'oos_expectancy_r',c.oos_expectancy_r,
    'oos_profit_factor',c.oos_profit_factor,'oos_max_drawdown_r',c.oos_max_drawdown_r,
    'alpha_state',c.alpha_state,'alpha_detail',c.alpha_detail,'status',c.status,
    'champion_since',c.champion_since,'last_validated_at',c.last_validated_at,'updated_at',c.updated_at
  ) order by c.market_family,c.symbol,c.timeframe),'[]'::jsonb) into v_champions
  from public.research_champions_v1 c where c.status <> 'DEGRADED';

  begin
    select coalesce(jsonb_agg(jsonb_build_object(
      'candidate_key',s.candidate_key,'market_family',s.market_family,'symbol',s.symbol,'timeframe',s.timeframe,
      'signals_n',s.signals_n,'resolved_n',s.resolved_n,'win_rate_pct',s.win_rate_pct,
      'expectancy_r',s.expectancy_r,'profit_factor',s.profit_factor,'pnl_pct_sum',s.pnl_pct_sum,
      'recent8_n',s.recent8_n,'recent8_expectancy_r',s.recent8_expectancy_r,
      'recent8_profit_factor',s.recent8_profit_factor,'recent8_trade_sharpe',s.recent8_trade_sharpe,
      'previous8_n',s.previous8_n,'previous8_expectancy_r',s.previous8_expectancy_r,
      'previous8_trade_sharpe',s.previous8_trade_sharpe,'current_loss_streak',s.current_loss_streak,
      'updated_at',s.updated_at
    ) order by s.updated_at desc),'[]'::jsonb) into v_shadow
    from public.research_shadow_live_metrics_v1 s
    join public.research_champions_v1 c on c.candidate_key=s.candidate_key;
  exception when undefined_column then
    select coalesce(jsonb_agg(jsonb_build_object(
      'candidate_key',s.candidate_key,'market_family',s.market_family,'symbol',s.symbol,'timeframe',s.timeframe,
      'signals_n',s.signals_n,'resolved_n',s.resolved_n,'win_rate_pct',s.win_rate_pct,
      'expectancy_r',s.expectancy_r,'profit_factor',s.profit_factor,'pnl_pct_sum',s.pnl_pct_sum,'updated_at',s.updated_at
    ) order by s.updated_at desc),'[]'::jsonb) into v_shadow
    from public.research_shadow_live_metrics_v1 s
    join public.research_champions_v1 c on c.candidate_key=s.candidate_key;
  end;

  select jsonb_build_object(
    'healthy',count(*) filter(where alpha_state='HEALTHY'),
    'watch',count(*) filter(where alpha_state='WATCH'),
    'degraded',count(*) filter(where alpha_state like 'DEGRADED%')
  ) into v_alpha from public.research_champions_v1;

  insert into public.research_governance_snapshot_v1(id,research_version,updated_at,target_cells,champion_count,pending_count,champions,shadow,alpha_summary)
  values(1,v_version,now(),46,v_count,greatest(0,46-v_count),v_champions,v_shadow,v_alpha)
  on conflict(id) do update set research_version=excluded.research_version,updated_at=excluded.updated_at,
    target_cells=46,champion_count=excluded.champion_count,pending_count=excluded.pending_count,
    champions=excluded.champions,shadow=excluded.shadow,alpha_summary=excluded.alpha_summary;
end $$;

revoke all on function maintenance.refresh_research_governance_snapshot_v1() from public,anon,authenticated;

do $$ begin perform maintenance.refresh_research_governance_snapshot_v1(); end $$;

-- One-time physical compaction. Keep active Champions plus ONE current working promotion per cell.
create temporary table rc8_keep_promotions on commit drop as
with ranked as (
  select p.candidate_key,
    row_number() over(
      partition by upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL')))
      order by p.updated_at desc
    ) rn
  from public.research_promotions_v1 p
  where p.research_version='RFV1_12_RC5_ITERATIVE_EDGE_46CELL_20260915'
    and p.stage <> 'STALE'
), keep_keys as (
  select candidate_key from ranked where rn=1
  union
  select candidate_key from public.research_champions_v1
)
select p.* from public.research_promotions_v1 p join keep_keys k using(candidate_key);

truncate table public.research_promotions_v1;
insert into public.research_promotions_v1 select * from rc8_keep_promotions;

-- Keep at most 4 causal finalists per active cell; observational laboratory keeps only newest row per exact scope.
create temporary table rc8_keep_findings on commit drop as
with ranked as (
  select f.feature_key,f.experiment,
    row_number() over(
      partition by case
        when f.experiment in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE')
          then coalesce(f.meta->>'coverage_cell_id',upper(concat_ws('|',f.scope->>'market_family',f.scope->>'symbol',f.scope->>'timeframe')))
        else concat_ws('|',f.engine,f.experiment,f.scope->>'market_family',f.scope->>'symbol',f.scope->>'timeframe',f.scope->>'direction',f.scope->>'regime')
      end
      order by f.updated_at desc
    ) rn
  from public.research_findings_v1 f
  where f.research_version='RFV1_12_RC5_ITERATIVE_EDGE_46CELL_20260915'
    and f.stage <> 'STALE'
    and coalesce((f.meta->>'is_current')::boolean,true)
), keep_keys as (
  select feature_key from ranked
  where (experiment in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') and rn<=4)
     or (experiment not in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') and rn=1)
)
select f.* from public.research_findings_v1 f join keep_keys k using(feature_key);

truncate table public.research_findings_v1;
insert into public.research_findings_v1 select * from rc8_keep_findings;

-- Final Knowledge-Core runtime contract.
alter table public.research_champions_v1 add column if not exists shadow_target integer not null default 0;
alter table public.research_champions_v1 add column if not exists canary_target integer not null default 0;
alter table public.research_champions_v1 add column if not exists runtime_trackable boolean not null default true;
alter table public.research_champions_v1 add column if not exists evidence jsonb not null default '{}'::jsonb;

update public.research_champions_v1 c set
 shadow_target=coalesce((p.meta->>'recommended_shadow_target')::int,0),
 canary_target=coalesce((p.meta->>'recommended_canary_target')::int,0),
 runtime_trackable=coalesce((p.meta->>'runtime_trackable')::boolean,true),
 evidence=jsonb_build_object(
  'all',coalesce(p.metrics->'all','{}'::jsonb),'validation',coalesce(p.metrics->'validation','{}'::jsonb),
  'walk_forward',coalesce(p.metrics->'walk_forward','{}'::jsonb),
  'guardian_operational_replay',coalesce(p.meta->'guardian_operational_replay','{}'::jsonb),
  'full_stack_profitability_certification',coalesce(p.meta->'full_stack_profitability_certification','{}'::jsonb),
  'walk_forward_positive_ratio',p.meta->'walk_forward_positive_ratio'),updated_at=now()
from public.research_promotions_v1 p where p.candidate_key=c.candidate_key;

-- Rolling 8-trade Alpha Decay metrics computed inside Postgres: no client egress.
create or replace view public.research_shadow_live_metrics_v1 as
with joined as (
 select s.candidate_key,s.signal_id,s.source_engine,s.experiment,s.research_stage,s.research_version,
        s.market_family,s.symbol,s.timeframe,s.direction,s.regime,s.execution_safety,s.risk_reward,s.observed_at,
        r.status result_status,
        coalesce(r.modeled_net_r::double precision,r.gross_r::double precision,
          case when r.status=any(array['tp_hit','win_tp','win_protected','tp']) then nullif(s.risk_reward,0::double precision)
               when r.status=any(array['sl_hit','loss_sl','sl']) then -1.0::double precision else null end) result_r,
        coalesce(r.net_pnl_pct,r.modeled_net_pnl_pct_margin,r.pnl_pct)::double precision result_pnl_pct,
        r.created_at result_created_at
 from public.research_shadow_live_v1 s
 left join lateral (
  select sr.status,sr.modeled_net_r,sr.gross_r,sr.net_pnl_pct,sr.modeled_net_pnl_pct_margin,sr.pnl_pct,sr.created_at
  from public.signal_results sr where sr.signal_id=s.signal_id order by sr.created_at desc limit 1
 ) r on true
), grouped as (
 select candidate_key,max(source_engine) source_engine,max(experiment) experiment,max(research_stage) research_stage,
        max(research_version) research_version,max(market_family) market_family,max(symbol) symbol,max(timeframe) timeframe,
        max(direction) direction,max(regime) regime,count(*)::integer signals_n,count(result_r)::integer resolved_n,
        count(*) filter(where result_r>0)::integer wins,count(*) filter(where result_r<0)::integer losses,
        case when count(result_r)>0 then round(100.0*count(*) filter(where result_r>0)::numeric/count(result_r)::numeric,2) end win_rate_pct,
        case when count(result_r)>0 then round(avg(result_r)::numeric,5) end expectancy_r,
        case when abs(sum(result_r) filter(where result_r<0))>0 then round((sum(result_r) filter(where result_r>0)/abs(sum(result_r) filter(where result_r<0)))::numeric,4)
             when sum(result_r) filter(where result_r>0)>0 then 999.0 end profit_factor,
        round(coalesce(sum(result_pnl_pct),0)::numeric,4) pnl_pct_sum,round(avg(execution_safety)::numeric,2) avg_safety,
        max(coalesce(result_created_at,observed_at)) updated_at
 from joined group by candidate_key
), resolved_ranked as (
 select candidate_key,result_r,coalesce(result_created_at,observed_at) event_at,
        row_number() over(partition by candidate_key order by coalesce(result_created_at,observed_at) desc,signal_id desc) rn
 from joined where result_r is not null
), rolling as (
 select candidate_key,
  count(*) filter(where rn<=8)::integer recent8_n,
  case when count(*) filter(where rn<=8)>0 then round(avg(result_r) filter(where rn<=8)::numeric,5) end recent8_expectancy_r,
  case when abs(sum(result_r) filter(where rn<=8 and result_r<0))>0
       then round((sum(result_r) filter(where rn<=8 and result_r>0)/abs(sum(result_r) filter(where rn<=8 and result_r<0)))::numeric,4)
       when sum(result_r) filter(where rn<=8 and result_r>0)>0 then 999.0 end recent8_profit_factor,
  case when count(*) filter(where rn<=8)>=2 and stddev_samp(result_r) filter(where rn<=8)>0
       then round((avg(result_r) filter(where rn<=8)/stddev_samp(result_r) filter(where rn<=8))::numeric,4) end recent8_trade_sharpe,
  count(*) filter(where rn between 9 and 16)::integer previous8_n,
  case when count(*) filter(where rn between 9 and 16)>0 then round(avg(result_r) filter(where rn between 9 and 16)::numeric,5) end previous8_expectancy_r,
  case when count(*) filter(where rn between 9 and 16)>=2 and stddev_samp(result_r) filter(where rn between 9 and 16)>0
       then round((avg(result_r) filter(where rn between 9 and 16)/stddev_samp(result_r) filter(where rn between 9 and 16))::numeric,4) end previous8_trade_sharpe,
  case when count(*)=0 then 0 else coalesce((min(rn) filter(where result_r>=0))-1,count(*))::integer end current_loss_streak
 from resolved_ranked group by candidate_key
)
select g.candidate_key,g.source_engine,g.experiment,g.research_stage,g.research_version,g.market_family,g.symbol,g.timeframe,g.direction,g.regime,
       g.signals_n,g.resolved_n,g.wins,g.losses,g.win_rate_pct,g.expectancy_r,g.profit_factor,g.pnl_pct_sum,g.avg_safety,g.updated_at,
       coalesce(r.recent8_n,0) recent8_n,r.recent8_expectancy_r,r.recent8_profit_factor,r.recent8_trade_sharpe,
       coalesce(r.previous8_n,0) previous8_n,r.previous8_expectancy_r,r.previous8_trade_sharpe,coalesce(r.current_loss_streak,0) current_loss_streak
from grouped g left join rolling r using(candidate_key);

create or replace function maintenance.refresh_research_governance_snapshot_v1()
returns void language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare v_version text; v_champions jsonb; v_shadow jsonb; v_alpha jsonb; v_count integer;
begin
 select research_version into v_version from public.research_champions_v1 order by updated_at desc limit 1;
 select count(*) into v_count from public.research_champions_v1 where status<>'DEGRADED';
 select coalesce(jsonb_agg(jsonb_build_object(
  'cell_key',c.cell_key,'research_version',c.research_version,'market_family',c.market_family,'symbol',c.symbol,'timeframe',c.timeframe,
  'candidate_key',c.candidate_key,'source_engine',c.source_engine,'experiment',c.experiment,'strategy_id',c.strategy_id,'strategy_family',c.strategy_family,
  'direction',c.direction,'regime',c.regime,'strategy_fingerprint',c.strategy_fingerprint,'strategy_spec',c.strategy_spec,'strategy_card',c.strategy_card,
  'oos_n',c.oos_n,'oos_wr_pct',c.oos_wr_pct,'oos_expectancy_r',c.oos_expectancy_r,'oos_profit_factor',c.oos_profit_factor,
  'oos_max_drawdown_r',c.oos_max_drawdown_r,'shadow_target',c.shadow_target,'canary_target',c.canary_target,'runtime_trackable',c.runtime_trackable,
  'evidence',c.evidence,'alpha_state',c.alpha_state,'alpha_detail',c.alpha_detail,'status',c.status,'champion_since',c.champion_since,
  'last_validated_at',c.last_validated_at,'updated_at',c.updated_at) order by c.market_family,c.symbol,c.timeframe),'[]'::jsonb)
 into v_champions from public.research_champions_v1 c where c.status<>'DEGRADED';
 select coalesce(jsonb_agg(jsonb_build_object(
  'candidate_key',s.candidate_key,'source_engine',s.source_engine,'experiment',s.experiment,'research_stage',s.research_stage,
  'research_version',s.research_version,'market_family',s.market_family,'symbol',s.symbol,'timeframe',s.timeframe,'direction',s.direction,'regime',s.regime,
  'signals_n',s.signals_n,'resolved_n',s.resolved_n,'wins',s.wins,'losses',s.losses,'win_rate_pct',s.win_rate_pct,'expectancy_r',s.expectancy_r,
  'profit_factor',s.profit_factor,'pnl_pct_sum',s.pnl_pct_sum,'avg_safety',s.avg_safety,'recent8_n',s.recent8_n,
  'recent8_expectancy_r',s.recent8_expectancy_r,'recent8_profit_factor',s.recent8_profit_factor,'recent8_trade_sharpe',s.recent8_trade_sharpe,
  'previous8_n',s.previous8_n,'previous8_expectancy_r',s.previous8_expectancy_r,'previous8_trade_sharpe',s.previous8_trade_sharpe,
  'current_loss_streak',s.current_loss_streak,'updated_at',s.updated_at) order by s.updated_at desc),'[]'::jsonb)
 into v_shadow from public.research_shadow_live_metrics_v1 s join public.research_champions_v1 c on c.candidate_key=s.candidate_key and c.status<>'DEGRADED';
 select jsonb_build_object('healthy',count(*) filter(where alpha_state='HEALTHY'),'watch',count(*) filter(where alpha_state='WATCH'),
  'degraded',count(*) filter(where alpha_state like 'DEGRADED%')) into v_alpha from public.research_champions_v1;
 insert into public.research_governance_snapshot_v1(id,research_version,updated_at,target_cells,champion_count,pending_count,champions,shadow,alpha_summary)
 values(1,v_version,now(),46,v_count,greatest(0,46-v_count),v_champions,v_shadow,v_alpha)
 on conflict(id) do update set research_version=excluded.research_version,updated_at=excluded.updated_at,target_cells=excluded.target_cells,
  champion_count=excluded.champion_count,pending_count=excluded.pending_count,champions=excluded.champions,shadow=excluded.shadow,alpha_summary=excluded.alpha_summary;
end $$;
revoke all on function maintenance.refresh_research_governance_snapshot_v1() from public,anon,authenticated;

create or replace function maintenance.refresh_research_alpha_decay_v1()
returns jsonb language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare v_updated int:=0; v_degraded int:=0; v_watch int:=0;
begin
 with health as (
  select c.cell_key,c.status old_status,c.oos_expectancy_r baseline_exp,c.oos_profit_factor baseline_pf,
   coalesce(s.recent8_n,0) recent_n,s.recent8_expectancy_r recent_exp,s.recent8_profit_factor recent_pf,s.recent8_trade_sharpe recent_sharpe,
   coalesce(s.previous8_n,0) previous_n,s.previous8_expectancy_r previous_exp,s.previous8_trade_sharpe previous_sharpe,
   coalesce(s.current_loss_streak,0) loss_streak,
   (coalesce(s.previous8_n,0)>=6 and ((s.recent8_trade_sharpe is not null and s.previous8_trade_sharpe is not null and s.recent8_trade_sharpe<=s.previous8_trade_sharpe-0.25)
    or (s.recent8_expectancy_r is not null and s.previous8_expectancy_r is not null and s.recent8_expectancy_r<=s.previous8_expectancy_r-0.15))) trajectory_bad,
   (c.oos_expectancy_r is not null and c.oos_expectancy_r>0 and s.recent8_expectancy_r is not null and s.recent8_expectancy_r<=0) baseline_bad
  from public.research_champions_v1 c left join public.research_shadow_live_metrics_v1 s on s.candidate_key=c.candidate_key
 ), classified as (
  select *,case
   when old_status='DEGRADED' then 'DEGRADED'
   when loss_streak>=8 then 'DEGRADED_LOSS_STREAK'
   when recent_n>=8 and recent_exp<0 and (coalesce(recent_pf,999)<0.90 or coalesce(recent_sharpe,999)<0) and (trajectory_bad or baseline_bad) then 'DEGRADED_ROLLING'
   when recent_n>=8 and ((baseline_exp is not null and baseline_exp>0 and recent_exp is not null and recent_exp<baseline_exp*0.50)
    or coalesce(recent_sharpe,999)<=0.10 or coalesce(recent_pf,999)<1.05 or trajectory_bad) then 'WATCH'
   else 'HEALTHY' end new_alpha
  from health
 ), upd as (
  update public.research_champions_v1 c set alpha_state=x.new_alpha,
   alpha_detail=jsonb_build_object('window',8,'baseline_expectancy_r',x.baseline_exp,'baseline_profit_factor',x.baseline_pf,
    'recent8_n',x.recent_n,'recent8_expectancy_r',x.recent_exp,'recent8_profit_factor',x.recent_pf,'recent8_trade_sharpe',x.recent_sharpe,
    'previous8_n',x.previous_n,'previous8_expectancy_r',x.previous_exp,'previous8_trade_sharpe',x.previous_sharpe,
    'current_loss_streak',x.loss_streak,'trajectory_bad',x.trajectory_bad,'baseline_bad',x.baseline_bad,'evaluated_at',now()),
   status=case when x.new_alpha like 'DEGRADED%' then 'DEGRADED' else c.status end,
   updated_at=case when c.alpha_state is distinct from x.new_alpha or (x.new_alpha like 'DEGRADED%' and c.status<>'DEGRADED') then now() else c.updated_at end
  from classified x where c.cell_key=x.cell_key returning c.alpha_state,c.status
 )
 select count(*),count(*) filter(where alpha_state like 'DEGRADED%'),count(*) filter(where alpha_state='WATCH') into v_updated,v_degraded,v_watch from upd;
 perform maintenance.refresh_research_governance_snapshot_v1();
 return jsonb_build_object('champions_evaluated',v_updated,'degraded',v_degraded,'watch',v_watch,'refreshed_at',now());
end $$;
revoke all on function maintenance.refresh_research_alpha_decay_v1() from public,anon,authenticated;

create or replace function maintenance.sync_research_champion_from_promotion_v1()
returns trigger language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare v_cell text; v_existing public.research_champions_v1%rowtype; v_fp text; v_original text; v_changed boolean:=false;
begin
 v_cell:=upper(concat_ws('|',coalesce(new.scope->>'market_family','UNKNOWN'),coalesce(new.scope->>'symbol','ALL'),coalesce(new.scope->>'timeframe','ALL')));
 if new.stage in ('SHADOW_READY','SHADOW_READY_FAST') and coalesce(new.meta->'causal_strategy_spec','{}'::jsonb)<>'{}'::jsonb then
  select * into v_existing from public.research_champions_v1 where cell_key=v_cell;
  if not found or v_existing.status='DEGRADED' or v_existing.candidate_key=new.candidate_key then
   v_fp:=coalesce(nullif(new.meta#>>'{strategy_card,fingerprint_sha256}',''),nullif(new.meta->>'causal_strategy_id',''),md5(new.candidate_key));
   insert into public.research_champions_v1(cell_key,research_version,market_family,symbol,timeframe,candidate_key,source_engine,experiment,strategy_id,strategy_family,
    direction,regime,strategy_fingerprint,strategy_spec,strategy_card,oos_n,oos_wr_pct,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,
    alpha_state,alpha_detail,status,champion_since,last_validated_at,updated_at,shadow_target,canary_target,runtime_trackable,evidence)
   values(v_cell,new.research_version,coalesce(new.scope->>'market_family','UNKNOWN'),upper(coalesce(new.scope->>'symbol','ALL')),upper(coalesce(new.scope->>'timeframe','ALL')),
    new.candidate_key,new.source_engine,new.experiment,new.meta->>'causal_strategy_id',coalesce(new.meta#>>'{causal_strategy_spec,family}',new.meta->>'causal_strategy_family',new.experiment),
    coalesce(new.scope->>'direction',new.meta#>>'{causal_strategy_spec,direction_mode}','BOTH'),coalesce(new.scope->>'regime','ALL'),v_fp,
    coalesce(new.meta->'causal_strategy_spec','{}'::jsonb),coalesce(new.meta->'strategy_card','{}'::jsonb),coalesce((new.metrics#>>'{validation,resolved}')::int,0),
    nullif(new.metrics#>>'{validation,win_rate_pct}','')::numeric,nullif(new.metrics#>>'{validation,expectancy_r}','')::numeric,
    nullif(new.metrics#>>'{validation,profit_factor}','')::numeric,nullif(new.metrics#>>'{validation,max_drawdown_r}','')::numeric,
    'HEALTHY','{}'::jsonb,'SHADOW',now(),new.updated_at,now(),coalesce((new.meta->>'recommended_shadow_target')::int,0),coalesce((new.meta->>'recommended_canary_target')::int,0),
    coalesce((new.meta->>'runtime_trackable')::boolean,true),jsonb_build_object('all',coalesce(new.metrics->'all','{}'::jsonb),'validation',coalesce(new.metrics->'validation','{}'::jsonb),
     'walk_forward',coalesce(new.metrics->'walk_forward','{}'::jsonb),'guardian_operational_replay',coalesce(new.meta->'guardian_operational_replay','{}'::jsonb),
     'full_stack_profitability_certification',coalesce(new.meta->'full_stack_profitability_certification','{}'::jsonb),'walk_forward_positive_ratio',new.meta->'walk_forward_positive_ratio'))
   on conflict(cell_key) do update set research_version=excluded.research_version,market_family=excluded.market_family,symbol=excluded.symbol,timeframe=excluded.timeframe,
    candidate_key=excluded.candidate_key,source_engine=excluded.source_engine,experiment=excluded.experiment,strategy_id=excluded.strategy_id,strategy_family=excluded.strategy_family,
    direction=excluded.direction,regime=excluded.regime,strategy_fingerprint=excluded.strategy_fingerprint,strategy_spec=excluded.strategy_spec,strategy_card=excluded.strategy_card,
    oos_n=excluded.oos_n,oos_wr_pct=excluded.oos_wr_pct,oos_expectancy_r=excluded.oos_expectancy_r,oos_profit_factor=excluded.oos_profit_factor,
    oos_max_drawdown_r=excluded.oos_max_drawdown_r,alpha_state=case when public.research_champions_v1.candidate_key=excluded.candidate_key then public.research_champions_v1.alpha_state else 'HEALTHY' end,
    alpha_detail=case when public.research_champions_v1.candidate_key=excluded.candidate_key then public.research_champions_v1.alpha_detail else '{}'::jsonb end,
    status='SHADOW',champion_since=case when public.research_champions_v1.candidate_key=excluded.candidate_key then public.research_champions_v1.champion_since else now() end,
    last_validated_at=excluded.last_validated_at,updated_at=now(),shadow_target=excluded.shadow_target,canary_target=excluded.canary_target,
    runtime_trackable=excluded.runtime_trackable,evidence=excluded.evidence;
   insert into public.research_validated_history_v1(strategy_fingerprint,cell_key,research_version,candidate_key,strategy_id,strategy_family,direction,regime,
    strategy_spec,oos_n,oos_wr_pct,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,disposition,first_validated_at,last_validated_at,updated_at)
   values(v_fp,v_cell,new.research_version,new.candidate_key,new.meta->>'causal_strategy_id',coalesce(new.meta#>>'{causal_strategy_spec,family}',new.meta->>'causal_strategy_family',new.experiment),
    coalesce(new.scope->>'direction',new.meta#>>'{causal_strategy_spec,direction_mode}','BOTH'),coalesce(new.scope->>'regime','ALL'),coalesce(new.meta->'causal_strategy_spec','{}'::jsonb),
    coalesce((new.metrics#>>'{validation,resolved}')::int,0),nullif(new.metrics#>>'{validation,win_rate_pct}','')::numeric,nullif(new.metrics#>>'{validation,expectancy_r}','')::numeric,
    nullif(new.metrics#>>'{validation,profit_factor}','')::numeric,nullif(new.metrics#>>'{validation,max_drawdown_r}','')::numeric,'VALIDATED_HISTORY',new.updated_at,new.updated_at,now())
   on conflict(strategy_fingerprint) do update set last_validated_at=excluded.last_validated_at,oos_n=excluded.oos_n,oos_wr_pct=excluded.oos_wr_pct,
    oos_expectancy_r=excluded.oos_expectancy_r,oos_profit_factor=excluded.oos_profit_factor,oos_max_drawdown_r=excluded.oos_max_drawdown_r,updated_at=now();
   v_changed:=true;
  end if;
 elsif new.stage='REJECTED_OOS' and new.experiment in ('CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') then
  v_original:=new.meta->>'original_candidate_key';
  if coalesce(v_original,'')<>'' then
   update public.research_champions_v1 set status='DEGRADED',alpha_state='DEGRADED_RETEST',
    alpha_detail=jsonb_build_object('reason','same-lineage causal retest failed','retest_candidate_key',new.candidate_key,'retest_updated_at',new.updated_at),updated_at=now()
   where candidate_key=v_original and status<>'DEGRADED';
   if found then v_changed:=true; end if;
  end if;
 end if;
 if v_changed then perform maintenance.refresh_research_governance_snapshot_v1(); end if;
 return new;
end $$;
revoke all on function maintenance.sync_research_champion_from_promotion_v1() from public,anon,authenticated;
drop trigger if exists trg_rc8_sync_research_champion on public.research_promotions_v1;
create trigger trg_rc8_sync_research_champion after insert or update of stage,metrics,meta,updated_at on public.research_promotions_v1
for each row execute function maintenance.sync_research_champion_from_promotion_v1();

create or replace function maintenance.compact_research_laboratory_v1()
returns jsonb language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare v_active text; v_prom_before int; v_prom_after int; v_find_before int; v_find_after int; v_mem_before int; v_mem_after int;
begin
 select research_version into v_active from public.research_engine_state_v1 order by last_seen_at desc nulls last limit 1;
 if v_active is null then select research_version into v_active from public.research_governance_snapshot_v1 where id=1; end if;
 select count(*) into v_prom_before from public.research_promotions_v1; select count(*) into v_find_before from public.research_findings_v1; select count(*) into v_mem_before from public.research_candidate_memory_v1;
 with raw as (
  select md5(upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) || '|' || coalesce(p.meta->>'causal_strategy_id',p.candidate_key)) memory_key,
   upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) cell_key,
   p.meta->>'causal_strategy_id' strategy_id,coalesce(p.meta#>>'{causal_strategy_spec,family}',p.meta->>'causal_strategy_family',p.experiment) strategy_family,
   coalesce(p.scope->>'direction',p.meta#>>'{causal_strategy_spec,direction_mode}','BOTH') direction,p.stage disposition,p.reason,
   coalesce((p.metrics#>>'{validation,resolved}')::int,0) oos_n,nullif(p.metrics#>>'{validation,expectancy_r}','')::numeric oos_expectancy_r,
   nullif(p.metrics#>>'{validation,profit_factor}','')::numeric oos_profit_factor,nullif(p.metrics#>>'{validation,max_drawdown_r}','')::numeric oos_max_drawdown_r,p.updated_at,
   case when p.stage='REJECTED_OOS' then p.updated_at+interval '7 days' when p.stage='STALE' then p.updated_at+interval '3 days' else p.updated_at+interval '1 day' end retest_after,
   row_number() over(partition by md5(upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) || '|' || coalesce(p.meta->>'causal_strategy_id',p.candidate_key)) order by p.updated_at desc) rn,
   min(p.updated_at) over(partition by md5(upper(concat_ws('|',coalesce(p.scope->>'market_family','UNKNOWN'),coalesce(p.scope->>'symbol','ALL'),coalesce(p.scope->>'timeframe','ALL'))) || '|' || coalesce(p.meta->>'causal_strategy_id',p.candidate_key))) first_seen_at
  from public.research_promotions_v1 p where not exists(select 1 from public.research_champions_v1 c where c.candidate_key=p.candidate_key)
 )
 insert into public.research_candidate_memory_v1(memory_key,cell_key,strategy_id,strategy_family,direction,disposition,reason,oos_n,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,test_count,first_tested_at,last_tested_at,retest_after,updated_at)
 select memory_key,cell_key,strategy_id,strategy_family,direction,disposition,reason,oos_n,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,1,first_seen_at,updated_at,retest_after,now() from raw where rn=1
 on conflict(memory_key) do update set disposition=excluded.disposition,reason=excluded.reason,oos_n=excluded.oos_n,oos_expectancy_r=excluded.oos_expectancy_r,
  oos_profit_factor=excluded.oos_profit_factor,oos_max_drawdown_r=excluded.oos_max_drawdown_r,
  test_count=case when excluded.last_tested_at>public.research_candidate_memory_v1.last_tested_at then public.research_candidate_memory_v1.test_count+1 else public.research_candidate_memory_v1.test_count end,
  first_tested_at=least(public.research_candidate_memory_v1.first_tested_at,excluded.first_tested_at),last_tested_at=greatest(public.research_candidate_memory_v1.last_tested_at,excluded.last_tested_at),
  retest_after=greatest(public.research_candidate_memory_v1.retest_after,excluded.retest_after),updated_at=now();
 delete from public.research_promotions_v1 p where not exists(select 1 from public.research_champions_v1 c where c.candidate_key=p.candidate_key)
  and p.candidate_key not in (select candidate_key from (select candidate_key,row_number() over(partition by upper(concat_ws('|',coalesce(scope->>'market_family','UNKNOWN'),coalesce(scope->>'symbol','ALL'),coalesce(scope->>'timeframe','ALL'))) order by updated_at desc) rn
   from public.research_promotions_v1 where research_version=v_active and stage<>'STALE') q where rn=1);
 delete from public.research_findings_v1 f where f.feature_key not in (select feature_key from (select feature_key,experiment,row_number() over(
  partition by case when experiment in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') then coalesce(meta->>'coverage_cell_id',upper(concat_ws('|',scope->>'market_family',scope->>'symbol',scope->>'timeframe')))
  else concat_ws('|',engine,experiment,scope->>'market_family',scope->>'symbol',scope->>'timeframe',scope->>'direction',scope->>'regime') end order by updated_at desc) rn
  from public.research_findings_v1 where research_version=v_active and stage<>'STALE' and coalesce((meta->>'is_current')::boolean,true)) q
  where (experiment in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') and rn<=4) or (experiment not in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') and rn=1));
 delete from public.research_candidate_memory_v1 where last_tested_at<now()-interval '180 days';
 delete from public.research_candidate_memory_v1 m using (select memory_key,row_number() over(partition by cell_key order by last_tested_at desc nulls last,updated_at desc) rn from public.research_candidate_memory_v1) r where m.memory_key=r.memory_key and r.rn>192;
 perform maintenance.refresh_research_alpha_decay_v1();
 select count(*) into v_prom_after from public.research_promotions_v1; select count(*) into v_find_after from public.research_findings_v1; select count(*) into v_mem_after from public.research_candidate_memory_v1;
 return jsonb_build_object('active_version',v_active,'promotions_before',v_prom_before,'promotions_after',v_prom_after,'findings_before',v_find_before,'findings_after',v_find_after,'memory_before',v_mem_before,'memory_after',v_mem_after,'ran_at',now());
end $$;
revoke all on function maintenance.compact_research_laboratory_v1() from public,anon,authenticated;

select maintenance.refresh_research_alpha_decay_v1();
select maintenance.refresh_research_governance_snapshot_v1();

do $$ begin
 if exists(select 1 from cron.job where jobname='research-alpha-decay-v1') then perform cron.unschedule('research-alpha-decay-v1'); end if;
 if exists(select 1 from cron.job where jobname='research-governance-snapshot-v1') then perform cron.unschedule('research-governance-snapshot-v1'); end if;
 if exists(select 1 from cron.job where jobname='research-knowledge-gc-v1') then perform cron.unschedule('research-knowledge-gc-v1'); end if;
 perform cron.schedule('research-alpha-decay-v1','*/15 * * * *','select maintenance.refresh_research_alpha_decay_v1();');
 perform cron.schedule('research-governance-snapshot-v1','*/5 * * * *','select maintenance.refresh_research_governance_snapshot_v1();');
 perform cron.schedule('research-knowledge-gc-v1','17 * * * *','select maintenance.compact_research_laboratory_v1();');
end $$;

create or replace function maintenance.skip_candidate_memory_noop_v1()
returns trigger language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
begin
 if new.last_tested_at is not distinct from old.last_tested_at
    and new.disposition is not distinct from old.disposition
    and new.reason is not distinct from old.reason
    and new.oos_n is not distinct from old.oos_n
    and new.oos_expectancy_r is not distinct from old.oos_expectancy_r
    and new.oos_profit_factor is not distinct from old.oos_profit_factor
    and new.oos_max_drawdown_r is not distinct from old.oos_max_drawdown_r
    and new.retest_after is not distinct from old.retest_after then
   return null;
 end if;
 return new;
end $$;
revoke all on function maintenance.skip_candidate_memory_noop_v1() from public,anon,authenticated;
drop trigger if exists trg_rc8_candidate_memory_noop on public.research_candidate_memory_v1;
create trigger trg_rc8_candidate_memory_noop before update on public.research_candidate_memory_v1
for each row execute function maintenance.skip_candidate_memory_noop_v1();
