-- RESEARCH FEDERATION V1
-- Ejecutar en el Supabase CENTRAL del sistema principal.
-- No modifica ni elimina signals, signal_results, saved_signals ni tablas de trading.

CREATE TABLE IF NOT EXISTS public.research_findings_v1 (
    feature_key text PRIMARY KEY,
    engine text NOT NULL,
    experiment text NOT NULL,
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    stage text NOT NULL DEFAULT 'INSUFFICIENT',
    authority text NOT NULL DEFAULT 'RESEARCH_ONLY',
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    research_version text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_research_findings_engine_updated_v1
ON public.research_findings_v1(engine, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_findings_stage_updated_v1
ON public.research_findings_v1(stage, updated_at DESC);

CREATE TABLE IF NOT EXISTS public.research_promotions_v1 (
    candidate_key text PRIMARY KEY,
    source_engine text NOT NULL,
    experiment text NOT NULL,
    stage text NOT NULL,
    authority text NOT NULL DEFAULT 'RESEARCH_ONLY',
    reason text,
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
    meta jsonb NOT NULL DEFAULT '{}'::jsonb,
    research_version text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_research_promotions_stage_updated_v1
ON public.research_promotions_v1(stage, updated_at DESC);

-- Estas dos tablas pueden estar en una BD privada distinta por motor.
-- Si al principio no tienes otra Supabase, pueden convivir aquí sin afectar producción.
CREATE TABLE IF NOT EXISTS public.research_engine_state_v1 (
    engine text PRIMARY KEY,
    research_version text NOT NULL,
    status text NOT NULL DEFAULT 'IDLE',
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    rss_mb double precision,
    meta jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS public.research_runs_v1 (
    run_id uuid PRIMARY KEY,
    engine text NOT NULL,
    status text NOT NULL,
    research_version text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    meta jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_research_runs_engine_updated_v1
ON public.research_runs_v1(engine, updated_at DESC);

-- Vista pequeña para el futuro Research Bridge del sistema central.
CREATE OR REPLACE VIEW public.research_actionable_candidates_v1
WITH (security_invoker = true)
AS
SELECT candidate_key, source_engine, experiment, stage, reason, scope, metrics, meta, research_version, updated_at
FROM public.research_promotions_v1
WHERE stage IN ('SHADOW_READY','VALIDATED_SINGLE_ASSET','REJECTED_OOS')
  AND authority = 'RESEARCH_ONLY';

-- Conserva lo esencial; borra sólo logs viejos recreables.
CREATE OR REPLACE FUNCTION public.cleanup_research_federation_v1()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  DELETE FROM public.research_runs_v1
  WHERE updated_at < now() - interval '60 days';

  -- Findings viejos que NO son candidatos y no fueron actualizados en 180 días.
  DELETE FROM public.research_findings_v1 f
  WHERE f.updated_at < now() - interval '180 days'
    AND NOT EXISTS (
      SELECT 1 FROM public.research_promotions_v1 p
      WHERE p.candidate_key = f.feature_key
    );
END;
$$;

-- Sanidad:
-- SELECT engine, count(*) FROM public.research_findings_v1 GROUP BY engine;
-- SELECT stage, count(*) FROM public.research_promotions_v1 GROUP BY stage;

-- Las tablas de investigación son backend-only. El service_role de los motores
-- puede operar; clientes anon/authenticated no reciben políticas de acceso.
ALTER TABLE public.research_findings_v1 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_promotions_v1 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_engine_state_v1 ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.research_runs_v1 ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON FUNCTION public.cleanup_research_federation_v1() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.cleanup_research_federation_v1() TO service_role;
