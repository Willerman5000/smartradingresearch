# Research Federation V1.1 — instalación

## Qué vas a tener
Cinco servicios Render independientes: Execution, Risk/Wick, Strategy/Ablation, Trader Intelligence y Validation. Cada uno tiene su propio frontend en `/` y su propia RAM. El sistema central no ejecuta sus backtests.

## 1. Supabase central
Ejecuta `schema_research_federation_v1_1.sql` en SQL Editor del mismo Supabase de SmartradingReview. No borra tablas de trading.

## 2. GitHub de investigación
Crea un repositorio nuevo (ej. `smartradingresearch`) y sube el contenido del ZIP unificado.

## 3. Segunda cuenta de Render
Opción recomendada: New > Blueprint, selecciona ese repo y usa `render.yaml`. Se crearán 5 servicios.

En los cinco configura:
- `CENTRAL_SUPABASE_URL` = URL del Supabase central.
- `CENTRAL_SUPABASE_SERVICE_KEY` = service-role/secret backend. Nunca la publiques en frontend.
- `RESEARCH_RUN_TOKEN` = una contraseña larga aleatoria.

Si luego deseas BD privada por motor, añade `RESEARCH_SUPABASE_URL` y `RESEARCH_SUPABASE_SERVICE_KEY`; si se omiten, las tablas privadas conviven en la central.

## 4. Verificación
Abre cada URL:
- `/health` debe decir `ok:true`, `authority:RESEARCH_ONLY`.
- `/` debe mostrar el dashboard.
- El botón `Copiar informe para ChatGPT` copia un resumen Markdown de resultados/OOS para que lo pegues en futuros chats.

## 5. Orden de trabajo
Execution, Risk, Strategy y Traders descubren evidencia. Validation corre después y decide: OBSERVE, VALIDATION_REQUIRED, REJECTED_OOS, VALIDATED_SINGLE_ASSET, SHADOW_READY o SHADOW_READY_FAST.

`SHADOW_READY_FAST` sólo aparece con evidencia mucho más exigente y recomienda ~10 resultados Shadow antes de Canary. No garantiza rentabilidad futura y no toca producción.

## 6. Sistema central
Después aplica `central_research_bridge_ready_tree.zip` sobre el sistema central actual (Commit 15 + Hotfix 15.3). No añade workers ni backtesting. Sólo agrega `/research-federation` y dos APIs de lectura compacta.

## 7. RAM
Los gráficos se dibujan con Canvas en el navegador, no en Render. El API del dashboard limita resultados y no carga velas/DataFrames. Mantén 1 worker por motor.

## 8. Para prompts futuros
En cualquier motor pulsa **Copiar informe para ChatGPT** y pega el texto. En el sistema central puedes hacer lo mismo desde `/research-federation`.
