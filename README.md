# Research Federation V1.1

Cinco motores externos de investigación para SmartradingReview. Cada servicio usa su propia RAM, escribe evidencia compacta en Supabase y expone un dashboard ligero en `/`.

Motores: `execution`, `risk`, `strategy`, `traders`, `validation`.

## Seguridad
- Autoridad: `RESEARCH_ONLY`.
- Ningún motor modifica Entry/SL/TP/Safety/leverage ni publica señales.
- Validation puede marcar `SHADOW_READY` o `SHADOW_READY_FAST`, nunca `ACTIVE`.
- Un backtest no garantiza rentabilidad futura.

## Frontend
Cada motor muestra tablas y gráficos dibujados en el navegador. El backend sólo devuelve hasta 120 resultados agregados. El botón **Copiar informe para ChatGPT** genera Markdown compacto para pegar en futuros prompts.

Consulta `INSTRUCCIONES_RESEARCH_FEDERATION_V1_1.md`.


## V1.2
Separa temporalidad como dimensión fuerte. PAXG/USDT y PAXG/BTC se estudian como familias Spot independientes; Futures conserva aprendizaje crypto compartido y sólo abre chequeos por símbolo para detectar excepciones. Validation recomienda targets Shadow distintos por timeframe.
