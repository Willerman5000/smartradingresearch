# Arquitectura simple

- **Execution Lab:** busca mejores momentos de entrada.
- **Risk & Wick Lab:** estudia stops, barridos y falsos stops.
- **Strategy Lab:** descubre qué estrategias/indicadores ayudan o perjudican.
- **Trader Lab:** descubre en qué contexto acierta cada especialista.
- **Validation Lab:** es el juez estadístico. Puede recomendar Shadow corto o normal, pero nunca activa señales por sí solo.

Cada uno vive en otro servicio Render, con su propia RAM. Todos mandan sólo resultados compactos al Supabase central. SmartradingReview sigue dedicado a operar y mostrar señales.

El frontend de cada laboratorio es sólo una ventana de observación: las tablas y gráficos se construyen en tu navegador.
