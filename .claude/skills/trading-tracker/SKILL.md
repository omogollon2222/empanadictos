---
name: trading-tracker
description: Seguimiento de trading de acciones tecnológicas (Apple, Tesla, Carrier, Meta, NVIDIA por defecto). Revisa cómo se mueve el mercado, calcula indicadores técnicos (SMA, RSI, MACD, ATR), da señal COMPRAR / ESPERAR / VENDER con sus razones, arma un plan por operación (acciones, stop, objetivo, ganancia y pérdida neta después de comisiones), hace backtest de la estrategia y lleva un diario de compras/ventas con P&L realizado. Usar cuando el usuario diga "cómo está el mercado", "revisa mis acciones", "¿compro Apple?", "¿cuánto gano si vendo a X?", "registra mi compra", "cómo voy en mis operaciones", "agrega/quita una acción de la watchlist".
---

# Seguimiento de Trading

Herramienta: `scripts/trading.py` (solo Python estándar, sin dependencias).
Configuración: `config.json` (watchlist, capital, % de riesgo, comisiones, múltiplos de ATR).
Datos generados: `data/` (reportes y diario; está en .gitignore).

Ejecuta los comandos desde la carpeta de la skill:
`cd .claude/skills/trading-tracker`

## Flujos

### 1. "¿Cómo está el mercado?" / revisar la watchlist
```
python3 scripts/trading.py scan
python3 scripts/trading.py scan --tickers AAPL NVDA   # solo algunas
```
- Datos diarios de Yahoo Finance (2 años). Si falla la red, prueba `--csv-dir <carpeta>` con archivos `TICKER.csv` (Date,Open,High,Low,Close,Volume) o `--demo` para probar (avisa siempre que son datos simulados).
- Presenta al usuario: la tabla resumen, el plan por operación y las razones. Luego en 3–5 líneas: qué acción tiene mejor configuración hoy, cuál evitar, y si la **ganancia neta esperada justifica el riesgo** (R:R ≥ 1.5 y backtest con promedio por operación positivo).
- Si el backtest de una acción da promedio negativo, dilo claro aunque la señal sea COMPRAR.

### 2. "¿Cuánto gano si compro a X y vendo a Y?"
```
python3 scripts/trading.py calc --compra 180 --venta 195 --acciones 20
```
Muestra ganancia bruta, neta (descontando comisiones de compra y venta) y precio de equilibrio.

### 3. Registrar operaciones reales y ver resultados
```
python3 scripts/trading.py log compra AAPL 10 182.50 --nota "rebote en SMA50"
python3 scripts/trading.py log venta AAPL 10 195.00
python3 scripts/trading.py diario
```
El diario calcula costo promedio (incluye comisión), P&L neto por operación, tasa de acierto y posiciones abiertas.
Confirma con el usuario ticker, cantidad y precio antes de registrar.

### 4. Cambiar la watchlist o parámetros
Edita `config.json`: `watchlist` (ticker → nombre), `capital_usd`, `riesgo_por_operacion_pct`, `comision_por_orden_usd`, `comision_pct`, `stop_atr`, `objetivo_atr`, `dias_max_operacion`.

## Cómo se calcula la señal (puntaje −5 a +5)
+1/−1 por cada uno: precio vs SMA50 · SMA50 vs SMA200 · histograma MACD (signo y aceleración) · RSI <30 (+1) / >70 (−1) · precio vs SMA20.
**≥ 3 COMPRAR · ≤ −3 VENDER/EVITAR · resto ESPERAR.**

Plan: entrada = último cierre, stop = entrada − 2×ATR, objetivo = entrada + 3×ATR. Tamaño = (capital × % riesgo) ÷ (entrada − stop), limitado al capital.
Backtest: entra al día siguiente de cada señal ≥ 3, sale en stop, objetivo o tras N días; reporta % neto por operación.

## Reglas
- Nunca presentes esto como asesoría financiera ni garantices ganancias. Cierra cada análisis con el aviso.
- No ejecutes órdenes en ningún bróker; esta skill solo analiza y registra.
- Si el usuario pregunta por noticias o resultados trimestrales, complementa con búsqueda web y separa claramente lo técnico de lo fundamental.
- Nota: CARR es Carrier Global (climatización/HVAC, no tecnológica). Si el usuario quería otra empresa, cámbiala en `config.json`.
