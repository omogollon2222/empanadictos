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
- Presenta al usuario: la tabla resumen, el plan por operación y las razones. Luego en 3–5 líneas: qué acción tiene mejor configuración hoy, cuál evitar, y si la **ganancia neta esperada justifica el riesgo** (R:R ≥ rr_minimo (1.4) y backtest con promedio por operación positivo).
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

## Simulación (paper trading con dinero virtual)
Estado guardado en `simulacion/estado.json` (versionado en git: haz commit y push después de cada cambio, porque el contenedor de la nube es temporal).
```
python3 scripts/trading.py sim iniciar --saldo 5000 --fin 2026-11-06
python3 scripts/trading.py sim comprar NVDA 5 --stop 180 --objetivo 205 --motivo "señal COMPRAR puntaje 4"
python3 scripts/trading.py sim vender NVDA 5 --motivo "tocó objetivo"
python3 scripts/trading.py sim estado            # valoriza con precios actuales
python3 scripts/trading.py sim estado --precios "AAPL=270,NVDA=190"   # precios manuales
```
### Datos reales (obligatorio)
El entorno en la nube no tiene acceso directo a Yahoo. El workflow `.github/workflows/datos-mercado.yml` descarga los precios reales en `datos/TICKER.csv`. La skill usa Yahoo en vivo si hay red y, si no, esos CSV.
Para refrescarlos: escribe la fecha y hora actual en `datos/solicitud.txt`, haz commit y push a `feature/trading-tracker-skill`, espera hasta que aparezca un commit nuevo "Datos de mercado: ..." (≈1 min) y haz `git pull`. Verifica en `datos/AAPL.csv` que el último día sea la sesión de hoy antes de operar.

Rutina diaria DESPUÉS del cierre (16:23 hora de Ecuador/Nueva York), simulando órdenes al precio de cierre del día:
1. Refrescar datos (ver arriba) y `sim estado`: vender lo que tocó stop u objetivo.
2. `scan`: para cada señal COMPRAR con R:R ≥ rr_minimo (1.4) y backtest con promedio positivo, comprar con el tamaño del plan, ajustado al efectivo disponible. Registrar siempre stop, objetivo y motivo.
3. Vender las posiciones cuya señal pasó a VENDER/EVITAR.
4. Commit y push de `simulacion/estado.json`.
5. Informar al usuario: qué se hizo y por qué, el valor de la cartera y la comparación con la referencia.
Nunca operes con precios inventados ni con datos simulados. Si no hay datos del día, dilo y no operes.

## Tablero (dashboard)
Tablero: https://claude.ai/artifact/GhBwmGxn53Cm6EwCJut3vs (tipo Dashboard; datasets en su base: senales, precios, cartera, posiciones, operaciones).
Para actualizarlo después de cada turno de la simulación:
1. `python3 scripts/trading.py tablero` genera `data/tablero/{senales,precios,cartera,posiciones,operaciones}.json`.
2. Sube cada archivo como asset del artifact (Artifact publish con `url` del tablero, `asset: true`, un `file_path` por llamada).
3. Con ArtifactData haz `update` de `datasets/<id>` con el nuevo `source.url`, `source.name` y `updated: {at, by}` (pasa `if_version` tras leer el documento).
4. Borra los assets anteriores que ya no se usen.
La página no cambia; solo cambian los datos.
