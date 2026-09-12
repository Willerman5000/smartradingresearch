-- ============================================================================
-- COMMIT 16 · RESEARCH FEDERATION V1.2 + SHADOW CENTRAL
-- Seguro / aditivo. No borra signals, signal_results, saved_signals ni portfolio.
-- Ejecutar DESPUÉS de schema_research_federation_v1_1.sql.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.research_shadow_live_v1 (
    candidate_key text NOT NULL,
    signal_id uuid NOT NULL REFERENCES public.signals(id) ON DELETE CASCADE,
    source_engine text NOT NULL,
    experiment text NOT NULL,
    research_stage text NOT NULL,
    research_version text NOT NULL,
    system_type text NOT NULL,
    market_family text NOT NULL,
    symbol text NOT NULL,
    timeframe text NOT NULL,
    direction text NOT NULL,
    regime text,
    execution_safety double precision,
    risk_reward double precision,
    entry numeric(20,8),
    stop_loss numeric(20,8),
    take_profit numeric(20,8),
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    matched_features jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(candidate_key, signal_id)
);
CREATE INDEX IF NOT EXISTS idx_research_shadow_live_candidate_v1
ON public.research_shadow_live_v1(candidate_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_shadow_live_signal_v1
ON public.research_shadow_live_v1(signal_id);

ALTER TABLE public.research_shadow_live_v1 ENABLE ROW LEVEL SECURITY;

-- Vista ultracompacta para Analytics/Bridge. PostgreSQL hace la extracción JSON;
-- Render recibe como máximo unas decenas de filas pequeñas.
CREATE OR REPLACE VIEW public.research_analytics_compact_v1
WITH (security_invoker = true) AS
SELECT
    p.candidate_key,
    p.source_engine,
    p.experiment,
    p.stage,
    p.reason,
    COALESCE(p.scope->>'market_family','--') AS market_family,
    COALESCE(p.scope->>'symbol','ALL') AS symbol,
    COALESCE(p.scope->>'timeframe','ALL') AS timeframe,
    COALESCE(p.scope->>'direction','ALL') AS direction,
    COALESCE(p.scope->>'regime','ALL') AS regime,
    COALESCE((p.metrics->'all'->>'resolved')::int,0) AS backtest_n,
    NULLIF(p.metrics->'all'->>'win_rate_pct','')::double precision AS backtest_wr,
    NULLIF(p.metrics->'all'->>'expectancy_r','')::double precision AS backtest_exp_r,
    NULLIF(p.metrics->'all'->>'profit_factor','')::double precision AS backtest_pf,
    COALESCE((p.metrics->'validation'->>'resolved')::int,0) AS oos_n,
    NULLIF(p.metrics->'validation'->>'win_rate_pct','')::double precision AS oos_wr,
    NULLIF(p.metrics->'validation'->>'expectancy_r','')::double precision AS oos_exp_r,
    NULLIF(p.metrics->'validation'->>'profit_factor','')::double precision AS oos_pf,
    NULLIF(p.metrics->'validation'->>'max_drawdown_r','')::double precision AS oos_max_dd_r,
    COALESCE((p.meta->>'recommended_shadow_target')::int,0) AS shadow_target,
    COALESCE((p.meta->>'recommended_canary_target')::int,0) AS canary_target,
    p.research_version,
    p.updated_at
FROM public.research_promotions_v1 p
WHERE p.authority='RESEARCH_ONLY'
  AND p.stage IN ('SHADOW_READY_FAST','SHADOW_READY','VALIDATED_SINGLE_ASSET','VALIDATION_REQUIRED','REJECTED_OOS');

-- Resultado live de cada candidato Shadow usando la última resolución disponible.
CREATE OR REPLACE VIEW public.research_shadow_live_metrics_v1
WITH (security_invoker = true) AS
WITH joined AS (
    SELECT
        s.*,
        r.status AS result_status,
        COALESCE(r.modeled_net_r, r.gross_r,
            CASE WHEN r.status IN ('tp_hit','win_tp','win_protected','tp') THEN NULLIF(s.risk_reward,0)
                 WHEN r.status IN ('sl_hit','loss_sl','sl') THEN -1.0
                 ELSE NULL END
        )::double precision AS result_r,
        COALESCE(r.net_pnl_pct, r.modeled_net_pnl_pct_margin, r.pnl_pct)::double precision AS result_pnl_pct,
        r.created_at AS result_created_at
    FROM public.research_shadow_live_v1 s
    LEFT JOIN LATERAL (
        SELECT sr.*
        FROM public.signal_results sr
        WHERE sr.signal_id=s.signal_id
        ORDER BY sr.created_at DESC
        LIMIT 1
    ) r ON true
), grouped AS (
    SELECT
        candidate_key,
        max(source_engine) AS source_engine,
        max(experiment) AS experiment,
        max(research_stage) AS research_stage,
        max(research_version) AS research_version,
        max(market_family) AS market_family,
        max(symbol) AS symbol,
        max(timeframe) AS timeframe,
        max(direction) AS direction,
        max(regime) AS regime,
        count(*)::int AS signals_n,
        count(result_r)::int AS resolved_n,
        count(*) FILTER (WHERE result_r>0)::int AS wins,
        count(*) FILTER (WHERE result_r<0)::int AS losses,
        CASE WHEN count(result_r)>0 THEN round((100.0*count(*) FILTER (WHERE result_r>0)/count(result_r))::numeric,2) END AS win_rate_pct,
        CASE WHEN count(result_r)>0 THEN round(avg(result_r)::numeric,5) END AS expectancy_r,
        CASE WHEN abs(sum(result_r) FILTER (WHERE result_r<0)) > 0
             THEN round((sum(result_r) FILTER (WHERE result_r>0) / abs(sum(result_r) FILTER (WHERE result_r<0)))::numeric,4)
             WHEN sum(result_r) FILTER (WHERE result_r>0) > 0 THEN 999.0 END AS profit_factor,
        round(COALESCE(sum(result_pnl_pct),0)::numeric,4) AS pnl_pct_sum,
        round(avg(execution_safety)::numeric,2) AS avg_safety,
        max(COALESCE(result_created_at, observed_at)) AS updated_at
    FROM joined
    GROUP BY candidate_key
)
SELECT * FROM grouped;

-- Cleanup sólo de tracking recreable antiguo. La evidencia Research durable NO se toca.
CREATE OR REPLACE FUNCTION public.cleanup_research_shadow_v1()
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=public AS $$
BEGIN
  DELETE FROM public.research_shadow_live_v1
  WHERE observed_at < now() - interval '365 days';
END; $$;
REVOKE ALL ON FUNCTION public.cleanup_research_shadow_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.cleanup_research_shadow_v1() TO service_role;

-- Sanidad opcional:
-- SELECT stage,count(*) FROM public.research_promotions_v1 GROUP BY stage ORDER BY stage;
-- SELECT * FROM public.research_analytics_compact_v1 LIMIT 20;
-- SELECT * FROM public.research_shadow_live_metrics_v1 LIMIT 20;
