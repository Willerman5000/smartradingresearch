-- RC8.1 · ACTION-SPECIFIC KNOWLEDGE CELLS
-- Authority key: market/system × symbol × timeframe × action.
-- Futures actions: LONG / SHORT. Spot actions: COMPRA_SPOT / VENTA_SPOT.
-- Regime remains inside StrategySpec; it is NOT a mandatory governance dimension.

begin;

alter table public.research_champions_v1 add column if not exists system_type text;
alter table public.research_champions_v1 add column if not exists action text;
alter table public.research_validated_history_v1 add column if not exists system_type text;
alter table public.research_validated_history_v1 add column if not exists action text;
alter table public.research_candidate_memory_v1 add column if not exists system_type text;
alter table public.research_candidate_memory_v1 add column if not exists action text;

-- Derive the explicit market/action dimension from the strategy direction already validated.
update public.research_champions_v1
set system_type = case when market_family='CRYPTO_FUTURES' then 'FUTURES' else 'SPOT' end,
    action = case
      when market_family='CRYPTO_FUTURES' and upper(direction)='LONG' then 'LONG'
      when market_family='CRYPTO_FUTURES' and upper(direction)='SHORT' then 'SHORT'
      when market_family<>'CRYPTO_FUTURES' and upper(direction)='LONG' then 'COMPRA_SPOT'
      when market_family<>'CRYPTO_FUTURES' and upper(direction)='SHORT' then 'VENTA_SPOT'
      else coalesce(action,'UNKNOWN') end;

update public.research_validated_history_v1 h
set system_type = case
      when coalesce(h.system_type,'')<>'' then h.system_type
      when split_part(h.cell_key,'|',1)='CRYPTO_FUTURES' then 'FUTURES'
      else 'SPOT' end,
    action = case
      when coalesce(h.action,'')<>'' then h.action
      when split_part(h.cell_key,'|',1)='CRYPTO_FUTURES' and upper(h.direction)='LONG' then 'LONG'
      when split_part(h.cell_key,'|',1)='CRYPTO_FUTURES' and upper(h.direction)='SHORT' then 'SHORT'
      when split_part(h.cell_key,'|',1)<>'CRYPTO_FUTURES' and upper(h.direction)='LONG' then 'COMPRA_SPOT'
      when split_part(h.cell_key,'|',1)<>'CRYPTO_FUTURES' and upper(h.direction)='SHORT' then 'VENTA_SPOT'
      else coalesce(h.action,'UNKNOWN') end;

update public.research_candidate_memory_v1 m
set system_type = case
      when coalesce(m.system_type,'')<>'' then m.system_type
      when split_part(m.cell_key,'|',1)='CRYPTO_FUTURES' then 'FUTURES'
      else 'SPOT' end,
    action = case
      when coalesce(m.action,'')<>'' then m.action
      when split_part(m.cell_key,'|',1)='CRYPTO_FUTURES' and upper(m.direction)='LONG' then 'LONG'
      when split_part(m.cell_key,'|',1)='CRYPTO_FUTURES' and upper(m.direction)='SHORT' then 'SHORT'
      when split_part(m.cell_key,'|',1)<>'CRYPTO_FUTURES' and upper(m.direction)='LONG' then 'COMPRA_SPOT'
      when split_part(m.cell_key,'|',1)<>'CRYPTO_FUTURES' and upper(m.direction)='SHORT' then 'VENTA_SPOT'
      else coalesce(m.action,'UNKNOWN') end;

-- Canonical keys now include action. No validated strategy is deleted.
update public.research_champions_v1
set cell_key = upper(concat_ws('|',system_type,market_family,symbol,timeframe,action));

update public.research_validated_history_v1
set cell_key = upper(concat_ws('|',system_type,split_part(cell_key,'|',1),split_part(cell_key,'|',2),split_part(cell_key,'|',3),action))
where array_length(string_to_array(cell_key,'|'),1)=3;

update public.research_candidate_memory_v1
set cell_key = upper(concat_ws('|',system_type,split_part(cell_key,'|',1),split_part(cell_key,'|',2),split_part(cell_key,'|',3),action))
where array_length(string_to_array(cell_key,'|'),1)=3;

-- Re-key compact memories so identical strategy IDs remain distinct by action cell.
-- Several legacy rows can collapse to the same action-specific fingerprint; deduplicate
-- transactionally, preserving the newest metrics and accumulated test count.
create temporary table rc81_candidate_memory_dedup on commit drop as
select distinct on (new_memory_key)
  new_memory_key as memory_key, cell_key, system_type, action, strategy_id, strategy_family, direction,
  disposition, reason, oos_n, oos_expectancy_r, oos_profit_factor, oos_max_drawdown_r,
  sum(test_count) over(partition by new_memory_key)::integer as test_count,
  min(first_tested_at) over(partition by new_memory_key) as first_tested_at,
  max(last_tested_at) over(partition by new_memory_key) as last_tested_at,
  max(retest_after) over(partition by new_memory_key) as retest_after,
  max(updated_at) over(partition by new_memory_key) as updated_at
from (
  select m.*, md5(m.cell_key || '|' || coalesce(m.strategy_id,'')) as new_memory_key
  from public.research_candidate_memory_v1 m
) q
order by new_memory_key, last_tested_at desc nulls last, updated_at desc;

truncate table public.research_candidate_memory_v1;
insert into public.research_candidate_memory_v1(
  memory_key,cell_key,system_type,action,strategy_id,strategy_family,direction,disposition,reason,oos_n,
  oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,test_count,first_tested_at,last_tested_at,retest_after,updated_at
)
select memory_key,cell_key,system_type,action,strategy_id,strategy_family,direction,disposition,reason,oos_n,
  oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,test_count,first_tested_at,last_tested_at,retest_after,updated_at
from rc81_candidate_memory_dedup;

alter table public.research_champions_v1 alter column system_type set not null;
alter table public.research_champions_v1 alter column action set not null;

create index if not exists idx_research_champions_action
  on public.research_champions_v1(system_type, symbol, timeframe, action, status);
create index if not exists idx_research_candidate_memory_action
  on public.research_candidate_memory_v1(system_type, cell_key, action, retest_after, updated_at desc);

alter table public.research_governance_snapshot_v1 alter column target_cells set default 60;
alter table public.research_governance_snapshot_v1 alter column pending_count set default 60;

create or replace function maintenance.refresh_research_governance_snapshot_v1()
returns void language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare v_version text; v_champions jsonb; v_shadow jsonb; v_alpha jsonb; v_count integer;
begin
  select research_version into v_version from public.research_champions_v1 order by updated_at desc limit 1;
  select count(*) into v_count from public.research_champions_v1 where status <> 'DEGRADED';
  select coalesce(jsonb_agg(jsonb_build_object(
      'cell_key',c.cell_key,'research_version',c.research_version,'system_type',c.system_type,
      'market_family',c.market_family,'symbol',c.symbol,'timeframe',c.timeframe,'action',c.action,
      'candidate_key',c.candidate_key,'source_engine',c.source_engine,'experiment',c.experiment,
      'strategy_id',c.strategy_id,'strategy_family',c.strategy_family,'direction',c.direction,'regime',c.regime,
      'strategy_fingerprint',c.strategy_fingerprint,'strategy_spec',c.strategy_spec,'strategy_card',c.strategy_card,
      'oos_n',c.oos_n,'oos_wr_pct',c.oos_wr_pct,'oos_expectancy_r',c.oos_expectancy_r,
      'oos_profit_factor',c.oos_profit_factor,'oos_max_drawdown_r',c.oos_max_drawdown_r,
      'alpha_state',c.alpha_state,'alpha_detail',c.alpha_detail,'status',c.status,
      'champion_since',c.champion_since,'last_validated_at',c.last_validated_at,'updated_at',c.updated_at,
      'shadow_target',c.shadow_target,'canary_target',c.canary_target,'runtime_trackable',c.runtime_trackable,
      'evidence',c.evidence
    ) order by c.system_type,c.symbol,c.timeframe,c.action),'[]'::jsonb)
    into v_champions from public.research_champions_v1 c where c.status <> 'DEGRADED';

  select coalesce(jsonb_agg(jsonb_build_object(
      'candidate_key',s.candidate_key,'source_engine',s.source_engine,'experiment',s.experiment,
      'research_stage',s.research_stage,'research_version',s.research_version,'market_family',s.market_family,
      'symbol',s.symbol,'timeframe',s.timeframe,'direction',s.direction,'signals_n',s.signals_n,
      'resolved_n',s.resolved_n,'wins',s.wins,'losses',s.losses,'win_rate_pct',s.win_rate_pct,
      'expectancy_r',s.expectancy_r,'profit_factor',s.profit_factor,'pnl_pct_sum',s.pnl_pct_sum,
      'avg_safety',s.avg_safety,'recent8_n',s.recent8_n,'recent8_expectancy_r',s.recent8_expectancy_r,
      'recent8_profit_factor',s.recent8_profit_factor,'recent8_trade_sharpe',s.recent8_trade_sharpe,
      'previous8_n',s.previous8_n,'previous8_expectancy_r',s.previous8_expectancy_r,
      'previous8_trade_sharpe',s.previous8_trade_sharpe,'current_loss_streak',s.current_loss_streak,
      'updated_at',s.updated_at
    ) order by s.updated_at desc),'[]'::jsonb)
    into v_shadow
  from public.research_shadow_live_metrics_v1 s
  join public.research_champions_v1 c on c.candidate_key=s.candidate_key
  where c.status <> 'DEGRADED';

  select jsonb_build_object(
    'healthy',count(*) filter(where alpha_state='HEALTHY'),
    'watch',count(*) filter(where alpha_state='WATCH'),
    'degraded',count(*) filter(where alpha_state like 'DEGRADED%')
  ) into v_alpha from public.research_champions_v1;

  insert into public.research_governance_snapshot_v1(
    id,research_version,updated_at,target_cells,champion_count,pending_count,champions,shadow,alpha_summary
  ) values(1,v_version,now(),60,v_count,greatest(0,60-v_count),v_champions,v_shadow,v_alpha)
  on conflict(id) do update set research_version=excluded.research_version,updated_at=excluded.updated_at,
    target_cells=60,champion_count=excluded.champion_count,pending_count=excluded.pending_count,
    champions=excluded.champions,shadow=excluded.shadow,alpha_summary=excluded.alpha_summary;
end $$;
revoke all on function maintenance.refresh_research_governance_snapshot_v1() from public,anon,authenticated;

create or replace function maintenance.sync_research_champion_from_promotion_v1()
returns trigger language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare
  v_cell text; v_system text; v_action text; v_direction text;
  v_existing public.research_champions_v1%rowtype; v_fp text; v_original text; v_changed boolean:=false;
begin
  v_system:=case when coalesce(new.scope->>'market_family','')='CRYPTO_FUTURES' then 'FUTURES' else 'SPOT' end;
  v_direction:=upper(coalesce(new.scope->>'direction',new.meta#>>'{causal_strategy_spec,direction_mode}',''));
  v_action:=upper(coalesce(new.scope->>'action',''));
  if v_action='' then
    v_action:=case
      when v_system='FUTURES' and v_direction in ('LONG','SHORT') then v_direction
      when v_system='SPOT' and v_direction='LONG' then 'COMPRA_SPOT'
      when v_system='SPOT' and v_direction='SHORT' then 'VENTA_SPOT'
      else 'UNKNOWN' end;
  end if;
  v_cell:=upper(concat_ws('|',v_system,coalesce(new.scope->>'market_family','UNKNOWN'),coalesce(new.scope->>'symbol','ALL'),coalesce(new.scope->>'timeframe','ALL'),v_action));

  if new.stage in ('SHADOW_READY','SHADOW_READY_FAST') and coalesce(new.meta->'causal_strategy_spec','{}'::jsonb)<>'{}'::jsonb then
    select * into v_existing from public.research_champions_v1 where cell_key=v_cell;
    if not found or v_existing.status='DEGRADED' or v_existing.candidate_key=new.candidate_key then
      v_fp:=coalesce(nullif(new.meta#>>'{strategy_card,fingerprint_sha256}',''),nullif(new.meta->>'causal_strategy_id',''),md5(new.candidate_key));
      insert into public.research_champions_v1(
        cell_key,research_version,system_type,market_family,symbol,timeframe,action,candidate_key,source_engine,experiment,
        strategy_id,strategy_family,direction,regime,strategy_fingerprint,strategy_spec,strategy_card,
        oos_n,oos_wr_pct,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,
        alpha_state,alpha_detail,status,champion_since,last_validated_at,updated_at,
        shadow_target,canary_target,runtime_trackable,evidence
      ) values(
        v_cell,new.research_version,v_system,coalesce(new.scope->>'market_family','UNKNOWN'),upper(coalesce(new.scope->>'symbol','ALL')),
        upper(coalesce(new.scope->>'timeframe','ALL')),v_action,new.candidate_key,new.source_engine,new.experiment,
        new.meta->>'causal_strategy_id',coalesce(new.meta#>>'{causal_strategy_spec,family}',new.meta->>'causal_strategy_family',new.experiment),
        v_direction,coalesce(new.scope->>'regime','ALL'),v_fp,coalesce(new.meta->'causal_strategy_spec','{}'::jsonb),
        coalesce(new.meta->'strategy_card','{}'::jsonb),coalesce((new.metrics#>>'{validation,resolved}')::int,0),
        nullif(new.metrics#>>'{validation,win_rate_pct}','')::numeric,nullif(new.metrics#>>'{validation,expectancy_r}','')::numeric,
        nullif(new.metrics#>>'{validation,profit_factor}','')::numeric,nullif(new.metrics#>>'{validation,max_drawdown_r}','')::numeric,
        'HEALTHY','{}'::jsonb,'SHADOW',now(),new.updated_at,now(),coalesce((new.meta->>'recommended_shadow_target')::int,0),
        coalesce((new.meta->>'recommended_canary_target')::int,0),coalesce((new.meta->>'runtime_trackable')::boolean,true),
        jsonb_build_object('all',coalesce(new.metrics->'all','{}'::jsonb),'validation',coalesce(new.metrics->'validation','{}'::jsonb),
          'walk_forward',coalesce(new.metrics->'walk_forward','{}'::jsonb),'guardian_operational_replay',coalesce(new.meta->'guardian_operational_replay','{}'::jsonb),
          'full_stack_profitability_certification',coalesce(new.meta->'full_stack_profitability_certification','{}'::jsonb),'walk_forward_positive_ratio',new.meta->'walk_forward_positive_ratio')
      ) on conflict(cell_key) do update set
        research_version=excluded.research_version,system_type=excluded.system_type,market_family=excluded.market_family,
        symbol=excluded.symbol,timeframe=excluded.timeframe,action=excluded.action,candidate_key=excluded.candidate_key,
        source_engine=excluded.source_engine,experiment=excluded.experiment,strategy_id=excluded.strategy_id,
        strategy_family=excluded.strategy_family,direction=excluded.direction,regime=excluded.regime,
        strategy_fingerprint=excluded.strategy_fingerprint,strategy_spec=excluded.strategy_spec,strategy_card=excluded.strategy_card,
        oos_n=excluded.oos_n,oos_wr_pct=excluded.oos_wr_pct,oos_expectancy_r=excluded.oos_expectancy_r,
        oos_profit_factor=excluded.oos_profit_factor,oos_max_drawdown_r=excluded.oos_max_drawdown_r,
        alpha_state=case when public.research_champions_v1.candidate_key=excluded.candidate_key then public.research_champions_v1.alpha_state else 'HEALTHY' end,
        alpha_detail=case when public.research_champions_v1.candidate_key=excluded.candidate_key then public.research_champions_v1.alpha_detail else '{}'::jsonb end,
        status='SHADOW',champion_since=case when public.research_champions_v1.candidate_key=excluded.candidate_key then public.research_champions_v1.champion_since else now() end,
        last_validated_at=excluded.last_validated_at,updated_at=now(),shadow_target=excluded.shadow_target,
        canary_target=excluded.canary_target,runtime_trackable=excluded.runtime_trackable,evidence=excluded.evidence;

      insert into public.research_validated_history_v1(
        strategy_fingerprint,cell_key,research_version,system_type,action,candidate_key,strategy_id,strategy_family,direction,regime,
        strategy_spec,oos_n,oos_wr_pct,oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,
        disposition,first_validated_at,last_validated_at,updated_at
      ) values(
        v_fp,v_cell,new.research_version,v_system,v_action,new.candidate_key,new.meta->>'causal_strategy_id',
        coalesce(new.meta#>>'{causal_strategy_spec,family}',new.meta->>'causal_strategy_family',new.experiment),v_direction,
        coalesce(new.scope->>'regime','ALL'),coalesce(new.meta->'causal_strategy_spec','{}'::jsonb),
        coalesce((new.metrics#>>'{validation,resolved}')::int,0),nullif(new.metrics#>>'{validation,win_rate_pct}','')::numeric,
        nullif(new.metrics#>>'{validation,expectancy_r}','')::numeric,nullif(new.metrics#>>'{validation,profit_factor}','')::numeric,
        nullif(new.metrics#>>'{validation,max_drawdown_r}','')::numeric,'VALIDATED_HISTORY',new.updated_at,new.updated_at,now()
      ) on conflict(strategy_fingerprint) do update set
        cell_key=excluded.cell_key,system_type=excluded.system_type,action=excluded.action,last_validated_at=excluded.last_validated_at,
        oos_n=excluded.oos_n,oos_wr_pct=excluded.oos_wr_pct,oos_expectancy_r=excluded.oos_expectancy_r,
        oos_profit_factor=excluded.oos_profit_factor,oos_max_drawdown_r=excluded.oos_max_drawdown_r,updated_at=now();
      v_changed:=true;
    end if;
  elsif new.stage='REJECTED_OOS' and new.experiment in ('CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') then
    v_original:=new.meta->>'original_candidate_key';
    if coalesce(v_original,'')<>'' then
      update public.research_champions_v1 set status='DEGRADED',alpha_state='DEGRADED_RETEST',
        alpha_detail=jsonb_build_object('reason','same-lineage causal retest failed','retest_candidate_key',new.candidate_key,'retest_updated_at',new.updated_at),
        updated_at=now()
      where candidate_key=v_original and status<>'DEGRADED';
      if found then v_changed:=true; end if;
    end if;
  end if;
  if v_changed then perform maintenance.refresh_research_governance_snapshot_v1(); end if;
  return new;
end $$;
revoke all on function maintenance.sync_research_champion_from_promotion_v1() from public,anon,authenticated;

create or replace function maintenance.compact_research_laboratory_v1()
returns jsonb language plpgsql security definer set search_path=public,maintenance,pg_temp as $$
declare v_active text; v_prom_before int; v_prom_after int; v_find_before int; v_find_after int; v_mem_before int; v_mem_after int;
begin
  select research_version into v_active from public.research_engine_state_v1 order by last_seen_at desc nulls last limit 1;
  if v_active is null then select research_version into v_active from public.research_governance_snapshot_v1 where id=1; end if;
  select count(*) into v_prom_before from public.research_promotions_v1;
  select count(*) into v_find_before from public.research_findings_v1;
  select count(*) into v_mem_before from public.research_candidate_memory_v1;

  with raw as (
    select
      case when coalesce(p.scope->>'market_family','')='CRYPTO_FUTURES' then 'FUTURES' else 'SPOT' end system_type,
      upper(coalesce(p.scope->>'direction',p.meta#>>'{causal_strategy_spec,direction_mode}','')) direction,
      p.*
    from public.research_promotions_v1 p
    where not exists(select 1 from public.research_champions_v1 c where c.candidate_key=p.candidate_key)
  ), shaped as (
    select r.*,
      upper(coalesce(r.scope->>'action',case
        when r.system_type='FUTURES' and r.direction in ('LONG','SHORT') then r.direction
        when r.system_type='SPOT' and r.direction='LONG' then 'COMPRA_SPOT'
        when r.system_type='SPOT' and r.direction='SHORT' then 'VENTA_SPOT'
        else 'UNKNOWN' end)) action
    from raw r
  ), mem as (
    select
      md5(upper(concat_ws('|',system_type,coalesce(scope->>'market_family','UNKNOWN'),coalesce(scope->>'symbol','ALL'),coalesce(scope->>'timeframe','ALL'),action)) || '|' || coalesce(meta->>'causal_strategy_id',candidate_key)) memory_key,
      upper(concat_ws('|',system_type,coalesce(scope->>'market_family','UNKNOWN'),coalesce(scope->>'symbol','ALL'),coalesce(scope->>'timeframe','ALL'),action)) cell_key,
      system_type,action,meta->>'causal_strategy_id' strategy_id,
      coalesce(meta#>>'{causal_strategy_spec,family}',meta->>'causal_strategy_family',experiment) strategy_family,
      direction,stage disposition,reason,coalesce((metrics#>>'{validation,resolved}')::int,0) oos_n,
      nullif(metrics#>>'{validation,expectancy_r}','')::numeric oos_expectancy_r,
      nullif(metrics#>>'{validation,profit_factor}','')::numeric oos_profit_factor,
      nullif(metrics#>>'{validation,max_drawdown_r}','')::numeric oos_max_drawdown_r,updated_at,
      case when stage='REJECTED_OOS' then updated_at+interval '7 days' when stage='STALE' then updated_at+interval '3 days' else updated_at+interval '1 day' end retest_after,
      row_number() over(partition by md5(upper(concat_ws('|',system_type,coalesce(scope->>'market_family','UNKNOWN'),coalesce(scope->>'symbol','ALL'),coalesce(scope->>'timeframe','ALL'),action)) || '|' || coalesce(meta->>'causal_strategy_id',candidate_key)) order by updated_at desc) rn,
      min(updated_at) over(partition by md5(upper(concat_ws('|',system_type,coalesce(scope->>'market_family','UNKNOWN'),coalesce(scope->>'symbol','ALL'),coalesce(scope->>'timeframe','ALL'),action)) || '|' || coalesce(meta->>'causal_strategy_id',candidate_key))) first_seen_at
    from shaped
  )
  insert into public.research_candidate_memory_v1(
    memory_key,cell_key,system_type,action,strategy_id,strategy_family,direction,disposition,reason,oos_n,
    oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,test_count,first_tested_at,last_tested_at,retest_after,updated_at
  )
  select memory_key,cell_key,system_type,action,strategy_id,strategy_family,direction,disposition,reason,oos_n,
    oos_expectancy_r,oos_profit_factor,oos_max_drawdown_r,1,first_seen_at,updated_at,retest_after,now()
  from mem where rn=1
  on conflict(memory_key) do update set
    cell_key=excluded.cell_key,system_type=excluded.system_type,action=excluded.action,disposition=excluded.disposition,
    reason=excluded.reason,oos_n=excluded.oos_n,oos_expectancy_r=excluded.oos_expectancy_r,
    oos_profit_factor=excluded.oos_profit_factor,oos_max_drawdown_r=excluded.oos_max_drawdown_r,
    test_count=case when excluded.last_tested_at>public.research_candidate_memory_v1.last_tested_at then public.research_candidate_memory_v1.test_count+1 else public.research_candidate_memory_v1.test_count end,
    first_tested_at=least(public.research_candidate_memory_v1.first_tested_at,excluded.first_tested_at),
    last_tested_at=greatest(public.research_candidate_memory_v1.last_tested_at,excluded.last_tested_at),
    retest_after=greatest(public.research_candidate_memory_v1.retest_after,excluded.retest_after),updated_at=now();

  delete from public.research_promotions_v1 p
  where not exists(select 1 from public.research_champions_v1 c where c.candidate_key=p.candidate_key)
    and p.candidate_key not in (
      select candidate_key from (
        select p2.candidate_key,row_number() over(
          partition by upper(concat_ws('|',
            case when coalesce(p2.scope->>'market_family','')='CRYPTO_FUTURES' then 'FUTURES' else 'SPOT' end,
            coalesce(p2.scope->>'market_family','UNKNOWN'),coalesce(p2.scope->>'symbol','ALL'),coalesce(p2.scope->>'timeframe','ALL'),
            coalesce(p2.scope->>'action',case
              when coalesce(p2.scope->>'market_family','')='CRYPTO_FUTURES' then coalesce(p2.scope->>'direction',p2.meta#>>'{causal_strategy_spec,direction_mode}','UNKNOWN')
              when coalesce(p2.scope->>'direction',p2.meta#>>'{causal_strategy_spec,direction_mode}','')='LONG' then 'COMPRA_SPOT'
              when coalesce(p2.scope->>'direction',p2.meta#>>'{causal_strategy_spec,direction_mode}','')='SHORT' then 'VENTA_SPOT'
              else 'UNKNOWN' end))) order by p2.updated_at desc) rn
        from public.research_promotions_v1 p2 where p2.research_version=v_active and p2.stage<>'STALE'
      ) q where rn=1
    );

  delete from public.research_findings_v1 f
  where f.feature_key not in (
    select feature_key from (
      select feature_key,experiment,row_number() over(
        partition by case
          when experiment in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE')
            then coalesce(meta->>'coverage_cell_id',upper(concat_ws('|',scope->>'market_family',scope->>'symbol',scope->>'timeframe',scope->>'action')))
          else concat_ws('|',engine,experiment,scope->>'market_family',scope->>'symbol',scope->>'timeframe',scope->>'action',scope->>'direction',scope->>'regime')
        end order by updated_at desc) rn
      from public.research_findings_v1
      where research_version=v_active and stage<>'STALE' and coalesce((meta->>'is_current')::boolean,true)
    ) q
    where (experiment in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') and rn<=4)
       or (experiment not in ('CAUSAL_COVERAGE_STRATEGY','CAUSAL_REGISTRY_RETEST','CAUSAL_SHADOW_RECYCLE') and rn=1)
  );

  delete from public.research_candidate_memory_v1 where last_tested_at<now()-interval '180 days';
  delete from public.research_candidate_memory_v1 m using (
    select memory_key,row_number() over(partition by cell_key order by last_tested_at desc nulls last,updated_at desc) rn
    from public.research_candidate_memory_v1
  ) r where m.memory_key=r.memory_key and r.rn>128;

  perform maintenance.refresh_research_alpha_decay_v1();
  select count(*) into v_prom_after from public.research_promotions_v1;
  select count(*) into v_find_after from public.research_findings_v1;
  select count(*) into v_mem_after from public.research_candidate_memory_v1;
  return jsonb_build_object('active_version',v_active,'promotions_before',v_prom_before,'promotions_after',v_prom_after,
    'findings_before',v_find_before,'findings_after',v_find_after,'memory_before',v_mem_before,'memory_after',v_mem_after,'ran_at',now());
end $$;
revoke all on function maintenance.compact_research_laboratory_v1() from public,anon,authenticated;

select maintenance.refresh_research_alpha_decay_v1();
select maintenance.refresh_research_governance_snapshot_v1();

commit;
