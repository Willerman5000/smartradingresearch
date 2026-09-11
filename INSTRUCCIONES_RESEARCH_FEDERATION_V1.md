# RESEARCH FEDERATION V1 — instrucciones simples

## Qué estás creando

Piensa en cinco ayudantes separados. Cada uno tiene su propia computadora/RAM en Render y estudia una pregunta distinta. El sistema principal sigue operando y sólo recibe una libreta con resultados compactos.

1. `research-execution`: estudia dónde conviene entrar.
2. `research-risk`: estudia dónde conviene invalidar/poner SL y barridos.
3. `research-strategy`: estudia qué estrategias e indicadores realmente aportan.
4. `research-traders`: estudia a qué trader escuchar según contexto.
5. `research-validation`: revisa el trabajo de los cuatro anteriores y decide si algo merece pasar a Shadow.

**Ninguno puede cambiar producción.** Si todos se caen, SmartradingReview continúa igual.

## PASO 1 — Supabase central

Abre SQL Editor del Supabase principal y ejecuta completo:

`schema_research_federation_v1.sql`

Sólo crea tablas `research_*_v1` y una función de limpieza. No borra señales ni aprendizaje.

## PASO 2 — GitHub de investigación

Crea un repositorio nuevo, por ejemplo:

`smartradingresearch`

Sube TODO el contenido de este ZIP a la raíz del repo y realiza un único commit.

## PASO 3 — Segunda cuenta Render

En Render usa **New → Blueprint** y selecciona ese repositorio.

`render.yaml` propone cinco servicios separados. Cada servicio tendrá su propia RAM.

## PASO 4 — Variables secretas

En CADA servicio coloca:

- `CENTRAL_SUPABASE_URL` = la URL del Supabase de SmartradingReview.
- `CENTRAL_SUPABASE_SERVICE_KEY` = service role del backend. Nunca lo pongas en frontend.
- `RESEARCH_RUN_TOKEN` = inventa una contraseña larga; usa la misma en los cinco.

No necesitas `RESEARCH_SUPABASE_URL` por ahora. Si más adelante creas bases físicas privadas, esos campos permiten conectarlas sin reescribir el sistema.

## PASO 5 — Comprobar

Abre en cada servicio:

`/health`

Debe indicar `ok: true`, el nombre de su engine y `authority: RESEARCH_ONLY`.

Después puedes lanzar manualmente un backtest/research batch con POST a `/run`, o configurar GitHub Actions.

## PASO 6 — Automatización opcional

El repo incluye `.github/workflows/research_tick.yml`.

En GitHub → Settings → Secrets and variables → Actions crea:

- `RESEARCH_RUN_TOKEN`
- `RESEARCH_EXECUTION_URL`
- `RESEARCH_RISK_URL`
- `RESEARCH_STRATEGY_URL`
- `RESEARCH_TRADERS_URL`
- `RESEARCH_VALIDATION_URL`

El workflow despierta/lanza los motores y deja Validation al final.

## Qué NO hacer todavía

No conectes estos resultados directamente a Entry/SL/TP/Safety. Primero deja que produzcan evidencia. El futuro Research Bridge del sistema principal sólo leerá candidatos `SHADOW_READY`; será fail-open y podrá desactivarse con una variable.
