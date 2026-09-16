-- RC8 FINAL · Alpha Decay Shield + Shadow rolling health
-- Idempotente. No borra señales ni resultados.

CREATE OR REPLACE VIEW public.research_shadow_live_metrics_v1
WITH (security_invoker = true) AS
WITH joined AS (
    SELECT
        s.*,
        r.status AS result_status,
        COALESCE(
            r.modeled_net_r,
            r.gross_r,
            CASE
                WHEN r.status IN ('tp_hit','win_tp','win_protected','tp') THEN NULLIF(s.risk_reward,0)
                WHEN r.status IN ('sl_hit','loss_sl','sl') THEN -1.0
                ELSE NULL
            END
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
), resolved_ranked AS (
    SELECT
        j.*,
        row_number() OVER (
            PARTITION BY candidate_key
            ORDER BY COALESCE(result_created_at, observed_at) DESC, signal_id DESC
        ) AS rn
    FROM joined j
    WHERE result_r IS NOT NULL
), base AS (
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
        CASE WHEN count(result_r)>0
             THEN round((100.0*count(*) FILTER (WHERE result_r>0)/count(result_r))::numeric,2)
        END AS win_rate_pct,
        CASE WHEN count(result_r)>0 THEN round(avg(result_r)::numeric,5) END AS expectancy_r,
        CASE
            WHEN abs(sum(result_r) FILTER (WHERE result_r<0)) > 0
            THEN round((sum(result_r) FILTER (WHERE result_r>0) /
                        abs(sum(result_r) FILTER (WHERE result_r<0)))::numeric,4)
            ELSE NULL
        END AS profit_factor,
        (count(result_r)>0
         AND abs(COALESCE(sum(result_r) FILTER (WHERE result_r<0),0)) <= 1e-12
         AND COALESCE(sum(result_r) FILTER (WHERE result_r>0),0) > 0) AS profit_factor_degenerate,
        round(COALESCE(sum(result_pnl_pct),0)::numeric,4) AS pnl_pct_sum,
        round(avg(execution_safety)::numeric,2) AS avg_safety,
        max(COALESCE(result_created_at, observed_at)) AS updated_at
    FROM joined
    GROUP BY candidate_key
), rolling AS (
    SELECT
        candidate_key,
        count(*) FILTER (WHERE rn<=8)::int AS recent8_n,
        round(avg(result_r) FILTER (WHERE rn<=8)::numeric,5) AS recent8_expectancy_r,
        CASE
            WHEN abs(COALESCE(sum(result_r) FILTER (WHERE rn<=8 AND result_r<0),0)) > 1e-12
            THEN round((COALESCE(sum(result_r) FILTER (WHERE rn<=8 AND result_r>0),0) /
                        abs(sum(result_r) FILTER (WHERE rn<=8 AND result_r<0)))::numeric,4)
            ELSE NULL
        END AS recent8_profit_factor,
        CASE
            WHEN count(*) FILTER (WHERE rn<=8) >= 3
             AND COALESCE(stddev_samp(result_r) FILTER (WHERE rn<=8),0) > 1e-12
            THEN round((avg(result_r) FILTER (WHERE rn<=8) /
                        stddev_samp(result_r) FILTER (WHERE rn<=8))::numeric,4)
            ELSE NULL
        END AS recent8_trade_sharpe,
        count(*) FILTER (WHERE rn BETWEEN 9 AND 16)::int AS previous8_n,
        round(avg(result_r) FILTER (WHERE rn BETWEEN 9 AND 16)::numeric,5) AS previous8_expectancy_r,
        CASE
            WHEN count(*) FILTER (WHERE rn BETWEEN 9 AND 16) >= 3
             AND COALESCE(stddev_samp(result_r) FILTER (WHERE rn BETWEEN 9 AND 16),0) > 1e-12
            THEN round((avg(result_r) FILTER (WHERE rn BETWEEN 9 AND 16) /
                        stddev_samp(result_r) FILTER (WHERE rn BETWEEN 9 AND 16))::numeric,4)
            ELSE NULL
        END AS previous8_trade_sharpe
    FROM resolved_ranked
    GROUP BY candidate_key
), first_non_loss AS (
    SELECT candidate_key, min(rn) FILTER (WHERE result_r>=0) AS first_non_loss_rn
    FROM resolved_ranked
    GROUP BY candidate_key
), streaks AS (
    SELECT
        r.candidate_key,
        count(*)::int AS current_loss_streak
    FROM resolved_ranked r
    LEFT JOIN first_non_loss b USING(candidate_key)
    WHERE r.result_r<0
      AND r.rn < COALESCE(b.first_non_loss_rn, 2147483647)
    GROUP BY r.candidate_key
)
SELECT
    b.*,
    COALESCE(r.recent8_n,0) AS recent8_n,
    r.recent8_expectancy_r,
    r.recent8_profit_factor,
    r.recent8_trade_sharpe,
    COALESCE(r.previous8_n,0) AS previous8_n,
    r.previous8_expectancy_r,
    r.previous8_trade_sharpe,
    COALESCE(s.current_loss_streak,0) AS current_loss_streak
FROM base b
LEFT JOIN rolling r USING(candidate_key)
LEFT JOIN streaks s USING(candidate_key);

COMMENT ON VIEW public.research_shadow_live_metrics_v1 IS
'RC8: métricas Shadow por Champion + ventanas rolling de 8 trades y racha actual para detectar alpha decay sin mezclar celdas.';
