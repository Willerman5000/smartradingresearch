# COMMIT I — Causal Profitability Coverage Accelerator

**Commit sugerido:**

`feat(research): add causal profitability coverage accelerator`

## Qué resuelve

Commit I cambia el cuello de botella de Research: ya no sólo busca asociaciones dentro de señales que el sistema ya produjo. Cada motor Research recibe celdas exclusivas de **mercado × temporalidad** y ejecuta replay causal con velas públicas de KuCoin.

La matriz objetivo queda en **18 celdas**:

- Futures agrupado: 5m, 15m, 30m, 1h, 2h, 4h.
- BTC/USDT Spot: 4h, 12h, 1D, 1W.
- PAXG/USDT Spot: 4h, 12h, 1D, 1W.
- PAXG/BTC Spot: 4h, 12h, 1D, 1W.

Cada celda busca de forma `coarse → fine` entre familias interpretables: tendencia, pullback, breakout/retest, mean reversion y sweep/reversal. Después refina Entry/SL/TP **sólo dentro de Research**. No cambia niveles del sistema productivo.

## Evidencia causal

Orden:

`Discovery 60% → Selection Holdout 20% → Final OOS 20% → Walk-forward → costes → Shadow`

El **Final OOS no participa en la selección** de la estrategia. Si una vela toca SL y TP, el replay cuenta SL primero (conservador).

Los costes son modelados con fees + slippage y, en Futures, stress de funding. Se reportan como modelados, no como ejecución real de cuenta.

## RAM: usar mucho sin provocar OOM

Los 4 motores analíticos usan su propia RAM de Render y no duplican celdas:

- `research-execution`: Futures 5m + 15m.
- `research-risk`: Futures 30m + 1h.
- `research-strategy`: Futures 2h + 4h + BTC/USDT Spot.
- `research-traders`: PAXG/USDT + PAXG/BTC Spot.
- `research-validation`: sólo agrega/valida; no descarga históricos.

Configuración objetivo:

- hard gate: **430 MB** en los motores de backtesting;
- target operativo: **390 MB**;
- caché histórico RAM: **210–270 MB** según motor;
- 1 worker / 1 thread por servicio;
- cargas de arranque escalonadas para no golpear KuCoin al mismo tiempo.

No usar 500–512 MB como hard gate: Gunicorn, requests, JSON, GC y el SO necesitan margen.

## Factory G también cambia

El límite deja de ser un `top-N` global puro. Ahora Factory reparte candidatos por `market_family × timeframe × symbol`, para que una zona fuerte como 30m SHORT no borre la investigación de 1h, 2h o Spot.

## Validation I

`CAUSAL_COVERAGE_STRATEGY` usa mínimos históricos diferentes por timeframe. No se exige el mismo N a 5m que a 1W. Futures agrega stress cross-asset antes de `SHADOW_READY`.

El camino sigue siendo:

`SIMULATED_PROFITABLE → OOS/WF → SHADOW_READY → Shadow live → Canary → Active`

No existe auto-promoción a producción.

## Exchange Flow

Se agrega un colector **CURRENT_CONTEXT_ONLY** de CEX. Es Research-only y fail-open. Nunca interpreta automáticamente “inflow = venta” o “outflow = compra”; no puede llegar a Shadow sin histórico causal confiable. Sirve para comenzar a registrar contexto para una futura integración formal con TraderMacro.

## Aplicación

Este paquete se aplica **sólo al repositorio `smartradingresearch`**, encima de Commit G.

1. Superponer el ready-tree sobre la raíz del repo Research.
2. Commit con el mensaje sugerido.
3. Deploy de los 5 servicios desde `render.yaml`.
4. No requiere SQL nuevo.

## Qué mirar después del deploy

En los exports/dashboard deben empezar a aparecer filas:

`CAUSAL_COVERAGE_STRATEGY`

con `coverage_status = SIMULATED_PROFITABLE` o `NO_EDGE_FOUND`, N, Final OOS, PF, estrategia y celda de cobertura.

Validation convertirá sólo las que superen OOS/walk-forward/costes/runtime/cross-asset a `SHADOW_READY` / `SHADOW_READY_FAST`.

**Importante:** el objetivo es llenar la matriz con estrategias rentables simuladas cuando los datos lo soporten. El motor no falsifica una estrategia positiva para llenar una casilla.
