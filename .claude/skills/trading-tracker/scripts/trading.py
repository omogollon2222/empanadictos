#!/usr/bin/env python3
"""Seguimiento de trading: escaneo de mercado, plan de operación, backtest y diario.

Solo usa la biblioteca estándar de Python (no requiere pip install).

Uso:
  python3 trading.py scan                      # escanea la watchlist de config.json
  python3 trading.py scan --tickers AAPL NVDA  # escanea tickers específicos
  python3 trading.py scan --demo               # datos simulados (sin internet)
  python3 trading.py calc --compra 180 --venta 195 --acciones 20
  python3 trading.py log compra AAPL 10 182.5 [--nota "..."]
  python3 trading.py log venta  AAPL 10 195.0
  python3 trading.py diario                    # P&L realizado y posiciones abiertas
  python3 trading.py sim iniciar --saldo 5000 --fin 2026-11-06
  python3 trading.py sim comprar NVDA 5 [--precio 190 --stop 180 --objetivo 205]
  python3 trading.py sim vender NVDA 5
  python3 trading.py sim estado [--precios "AAPL=270,NVDA=190"]

AVISO: herramienta educativa/analítica. No es asesoría financiera.
"""
import argparse
import csv
import json
import math
import os
import random
import sys
import urllib.request
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE, "config.json")
JOURNAL_PATH = os.path.join(BASE, "data", "diario_operaciones.csv")
REPORT_DIR = os.path.join(BASE, "data", "reportes")


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------- datos
DATOS_DIR = os.path.join(BASE, "datos")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8")


def fetch_yahoo(ticker, rango="2y"):
    last = None
    for host in ("query1", "query2"):
        try:
            data = json.loads(_get(f"https://{host}.finance.yahoo.com/v8/finance/chart/{ticker}"
                                   f"?range={rango}&interval=1d"))
            break
        except Exception as e:  # noqa: BLE001
            last = e
    else:
        raise last
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(res["timestamp"]):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        rows.append({"date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                     "open": o, "high": h, "low": l, "close": c, "volume": v or 0})
    return rows


def load_csv(path):
    """CSV con columnas Date,Open,High,Low,Close,Volume (formato Yahoo/Investing)."""
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r = {k.strip().lower(): v for k, v in r.items()}
            try:
                rows.append({"date": r["date"], "open": float(r["open"]), "high": float(r["high"]),
                             "low": float(r["low"]), "close": float(r["close"]),
                             "volume": float(r.get("volume") or 0)})
            except (ValueError, KeyError):
                continue
    return rows


def load_real(ticker, rango="2y"):
    """Datos reales: intenta Yahoo en vivo; si la red está bloqueada usa datos/TICKER.csv
    (descargado por GitHub Actions). Devuelve (filas, fuente)."""
    try:
        return fetch_yahoo(ticker, rango), "Yahoo Finance (en vivo)"
    except Exception as e:  # noqa: BLE001
        path = os.path.join(DATOS_DIR, f"{ticker}.csv")
        if os.path.exists(path):
            return load_csv(path), "Yahoo Finance vía GitHub Actions (datos/)"
        raise RuntimeError(f"sin red ({str(e)[:40]}) y sin datos/{ticker}.csv") from e


def cmd_descargar(args, cfg):
    os.makedirs(DATOS_DIR, exist_ok=True)
    ok = 0
    for t in [t.upper() for t in (args.tickers or cfg["watchlist"].keys())]:
        try:
            rows = fetch_yahoo(t, cfg.get("historial", "2y"))
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t}: {e}")
            continue
        with open(os.path.join(DATOS_DIR, f"{t}.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
            for r in rows:
                w.writerow([r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]])
        ok += 1
        print(f"{t}: {len(rows)} días, último {rows[-1]['date']} cierre {rows[-1]['close']:.2f}")
    with open(os.path.join(DATOS_DIR, "actualizado.txt"), "w", encoding="utf-8") as f:
        f.write(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + "\n")
    if ok == 0:
        sys.exit(1)


def demo_series(ticker, n=500):
    rnd = random.Random(ticker)
    price, rows = rnd.uniform(80, 400), []
    drift = rnd.uniform(-0.0005, 0.0015)
    for i in range(n):
        ch = rnd.gauss(drift, 0.02)
        o = price
        c = max(1, price * (1 + ch))
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.008)))
        l = min(o, c) * (1 - abs(rnd.gauss(0, 0.008)))
        rows.append({"date": f"d{i:04d}", "open": o, "high": h, "low": l, "close": c,
                     "volume": rnd.randint(10**6, 10**8)})
        price = c
    return rows


# ------------------------------------------------------------ indicadores
def sma(vals, n):
    out, s = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(vals, n):
    out, k, prev = [None] * len(vals), 2 / (n + 1), None
    for i, v in enumerate(vals):
        prev = v if prev is None else v * k + prev * (1 - k)
        out[i] = prev if i >= n - 1 else None
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, n + 1)]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, n + 1)]
    ag, al = sum(gains) / n, sum(losses) / n
    for i in range(n, len(closes)):
        if i > n:
            d = closes[i] - closes[i - 1]
            ag = (ag * (n - 1) + max(d, 0)) / n
            al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(rows, n=14):
    trs = []
    for i, r in enumerate(rows):
        pc = rows[i - 1]["close"] if i else r["close"]
        trs.append(max(r["high"] - r["low"], abs(r["high"] - pc), abs(r["low"] - pc)))
    out, prev = [None] * len(rows), None
    for i, t in enumerate(trs):
        if i == n - 1:
            prev = sum(trs[:n]) / n
        elif i >= n:
            prev = (prev * (n - 1) + t) / n
        out[i] = prev
    return out


def indicators(rows):
    c = [r["close"] for r in rows]
    e12, e26 = ema(c, 12), ema(c, 26)
    macd = [a - b if a is not None and b is not None else None for a, b in zip(e12, e26)]
    valid = [m for m in macd if m is not None]
    sig_v = ema(valid, 9)
    signal = [None] * (len(macd) - len(valid)) + sig_v
    return {"close": c, "sma20": sma(c, 20), "sma50": sma(c, 50), "sma200": sma(c, 200),
            "rsi": rsi(c), "macd": macd, "macd_signal": signal, "atr": atr(rows)}


def score_at(ind, i):
    """Puntaje de -5 a +5 combinando tendencia, momentum y sobrecompra/sobreventa."""
    g = lambda k: ind[k][i]
    if any(g(k) is None for k in ("sma50", "rsi", "macd", "macd_signal", "atr")):
        return None, []
    c, s, why = g("close"), 0, []
    if c > g("sma50"):
        s += 1; why.append("precio > SMA50")
    else:
        s -= 1; why.append("precio < SMA50")
    if g("sma200") is not None:
        if g("sma50") > g("sma200"):
            s += 1; why.append("SMA50 > SMA200 (tendencia alcista)")
        else:
            s -= 1; why.append("SMA50 < SMA200 (tendencia bajista)")
    hist = g("macd") - g("macd_signal")
    prev_hist = (ind["macd"][i - 1] - ind["macd_signal"][i - 1]) if ind["macd_signal"][i - 1] is not None else hist
    if hist > 0 and hist >= prev_hist:
        s += 1; why.append("MACD alcista y acelerando")
    elif hist < 0 and hist <= prev_hist:
        s -= 1; why.append("MACD bajista y acelerando")
    r = g("rsi")
    if r < 30:
        s += 1; why.append(f"RSI {r:.0f} sobrevendido")
    elif r > 70:
        s -= 1; why.append(f"RSI {r:.0f} sobrecomprado")
    if g("sma20") is not None:
        if c > g("sma20"):
            s += 1; why.append("precio > SMA20 (corto plazo fuerte)")
        else:
            s -= 1; why.append("precio < SMA20 (corto plazo débil)")
    return s, why


def verdict(s):
    if s is None:
        return "SIN DATOS"
    if s >= 3:
        return "COMPRAR"
    if s <= -3:
        return "VENDER / EVITAR"
    return "ESPERAR"


# ---------------------------------------------------------------- costos
def fees(cfg, notional):
    return cfg["comision_por_orden_usd"] + notional * cfg["comision_pct"] / 100


def trade_plan(cfg, price, a):
    stop = price - cfg["stop_atr"] * a
    target = price + cfg["objetivo_atr"] * a
    risk_ps = price - stop
    shares = math.floor(cfg["capital_usd"] * cfg["riesgo_por_operacion_pct"] / 100 / risk_ps) if risk_ps > 0 else 0
    shares = min(shares, math.floor(cfg["capital_usd"] / price))
    cost = fees(cfg, shares * price)
    gain = shares * (target - price) - cost - fees(cfg, shares * target)
    loss = shares * (stop - price) - cost - fees(cfg, shares * stop)
    breakeven = price + (2 * cfg["comision_por_orden_usd"] / shares if shares else 0) + price * 2 * cfg["comision_pct"] / 100
    return {"entrada": price, "stop": stop, "objetivo": target, "acciones": shares,
            "inversion": shares * price, "ganancia_neta_objetivo": gain,
            "perdida_neta_stop": loss, "precio_equilibrio": breakeven,
            "ratio_rr": (gain / -loss) if loss < 0 else None}


# --------------------------------------------------------------- backtest
def backtest(cfg, rows, ind):
    """Simula: compra cuando el puntaje >= 3; sale en stop, objetivo o tras N días."""
    trades, i, n = [], 0, len(rows)
    while i < n - 1:
        s, _ = score_at(ind, i)
        if s is None or s < 3:
            i += 1
            continue
        entry, a = rows[i + 1]["open"], ind["atr"][i]
        stop, tgt = entry - cfg["stop_atr"] * a, entry + cfg["objetivo_atr"] * a
        exit_p, j = None, i + 1
        while j < n and j <= i + cfg["dias_max_operacion"]:
            r = rows[j]
            if r["low"] <= stop:
                exit_p = min(stop, r["open"]); break
            if r["high"] >= tgt:
                exit_p = max(tgt, r["open"]) if r["open"] > tgt else tgt; break
            j += 1
        if exit_p is None:
            j = min(j, n - 1); exit_p = rows[j]["close"]
        sh = max(1, math.floor(cfg["capital_usd"] / entry))
        net = sh * (exit_p - entry) - fees(cfg, sh * entry) - fees(cfg, sh * exit_p)
        trades.append(net / (sh * entry) * 100)
        i = j + 1
    if not trades:
        return {"operaciones": 0}
    wins = [t for t in trades if t > 0]
    return {"operaciones": len(trades), "tasa_acierto": len(wins) / len(trades) * 100,
            "promedio_pct": sum(trades) / len(trades), "mejor_pct": max(trades),
            "peor_pct": min(trades), "total_pct": sum(trades)}


# ------------------------------------------------------------------ scan
def analyze(cfg, ticker, rows):
    ind = indicators(rows)
    i = len(rows) - 1
    s, why = score_at(ind, i)
    last, prev = rows[i]["close"], rows[i - 1]["close"]
    ago = lambda d: (last / rows[max(0, i - d)]["close"] - 1) * 100
    out = {"ticker": ticker, "nombre": cfg["watchlist"].get(ticker, ticker), "fecha": rows[i]["date"],
           "precio": last, "var_dia_pct": (last / prev - 1) * 100, "var_1m_pct": ago(21),
           "var_3m_pct": ago(63), "max_52s": max(r["high"] for r in rows[-252:]),
           "min_52s": min(r["low"] for r in rows[-252:]), "rsi": ind["rsi"][i],
           "sma50": ind["sma50"][i], "sma200": ind["sma200"][i], "atr": ind["atr"][i],
           "puntaje": s, "senal": verdict(s), "razones": why,
           "backtest": backtest(cfg, rows, ind)}
    if ind["atr"][i]:
        out["plan"] = trade_plan(cfg, last, ind["atr"][i])
    return out


def fmt(v, d=2):
    return "—" if v is None else f"{v:,.{d}f}"


def render(results, cfg, demo):
    L = [f"# Seguimiento de Trading — {datetime.now():%Y-%m-%d %H:%M}",
         "> ⚠️ DATOS SIMULADOS (modo demo)" if demo else "",
         f"Capital: ${cfg['capital_usd']:,.0f} · Riesgo/operación: {cfg['riesgo_por_operacion_pct']}% · "
         f"Comisión: ${cfg['comision_por_orden_usd']}/orden + {cfg['comision_pct']}%", "",
         "## Resumen", "",
         "| Ticker | Empresa | Precio | Día % | 1M % | 3M % | RSI | Puntaje | Señal |",
         "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in results:
        if "error" in r:
            L.append(f"| {r['ticker']} | {r['nombre']} | — | — | — | — | — | — | ERROR: {r['error']} |")
            continue
        L.append(f"| {r['ticker']} | {r['nombre']} | {fmt(r['precio'])} | {fmt(r['var_dia_pct'])} | "
                 f"{fmt(r['var_1m_pct'])} | {fmt(r['var_3m_pct'])} | {fmt(r['rsi'], 0)} | "
                 f"{r['puntaje'] if r['puntaje'] is not None else '—'} | **{r['senal']}** |")
    L += ["", "## Plan por operación (si compras hoy al precio de cierre)", "",
          "| Ticker | Acciones | Inversión | Stop | Objetivo | Ganancia neta | Pérdida neta | R:R | Backtest: aciertos / prom. por op. |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in results:
        p, b = r.get("plan"), r.get("backtest", {})
        if not p:
            continue
        bt = (f"{b['operaciones']} ops · {fmt(b['tasa_acierto'], 0)}% · {fmt(b['promedio_pct'])}%"
              if b.get("operaciones") else "sin señales")
        L.append(f"| {r['ticker']} | {p['acciones']} | ${fmt(p['inversion'], 0)} | {fmt(p['stop'])} | "
                 f"{fmt(p['objetivo'])} | ${fmt(p['ganancia_neta_objetivo'])} | ${fmt(p['perdida_neta_stop'])} | "
                 f"{fmt(p['ratio_rr'])} | {bt} |")
    L += ["", "## Razones de cada señal", ""]
    for r in results:
        if "razones" in r:
            L.append(f"- **{r['ticker']}** ({r['senal']}): " + "; ".join(r["razones"]))
    L += ["", "_Herramienta analítica, no asesoría financiera. Resultados pasados no garantizan resultados futuros._"]
    return "\n".join(x for x in L if x is not None)


def cmd_scan(args, cfg):
    tickers = [t.upper() for t in (args.tickers or cfg["watchlist"].keys())]
    results, fuentes = [], set()
    for t in tickers:
        try:
            if args.demo:
                rows = demo_series(t)
            elif args.csv_dir:
                rows = load_csv(os.path.join(args.csv_dir, f"{t}.csv"))
            else:
                rows, fuente = load_real(t, cfg.get("historial", "2y"))
                fuentes.add(fuente)
            if len(rows) < 60:
                raise ValueError(f"historial insuficiente ({len(rows)} días)")
            results.append(analyze(cfg, t, rows))
        except Exception as e:  # noqa: BLE001 — reportar y seguir con los demás
            results.append({"ticker": t, "nombre": cfg["watchlist"].get(t, t), "error": str(e)[:80]})
    md = render(results, cfg, args.demo)
    if fuentes:
        md = md.replace("\n", f"\nFuente: {', '.join(sorted(fuentes))} · último dato: "
                        f"{max((r.get('fecha') or '') for r in results)}\n", 1)
    os.makedirs(REPORT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    with open(os.path.join(REPORT_DIR, f"scan_{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(REPORT_DIR, f"scan_{stamp}.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str) if args.json else md)


# ------------------------------------------------------------------ calc
def cmd_calc(args, cfg):
    sh, b, s = args.acciones, args.compra, args.venta
    fb, fs = fees(cfg, sh * b), fees(cfg, sh * s)
    gross = sh * (s - b)
    net = gross - fb - fs
    print(f"Compra: {sh} x ${b:,.2f} = ${sh*b:,.2f} (comisión ${fb:,.2f})")
    print(f"Venta : {sh} x ${s:,.2f} = ${sh*s:,.2f} (comisión ${fs:,.2f})")
    print(f"Ganancia bruta: ${gross:,.2f}")
    print(f"Ganancia NETA : ${net:,.2f}  ({net/(sh*b)*100:+.2f}% sobre lo invertido)")
    be = b + (2 * cfg['comision_por_orden_usd']) / sh
    print(f"Precio de equilibrio (cubre comisiones): ${be:,.2f}")


# ----------------------------------------------------------------- diario
FIELDS = ["fecha", "tipo", "ticker", "acciones", "precio", "comision", "nota"]


def cmd_log(args, cfg):
    os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
    new = not os.path.exists(JOURNAL_PATH)
    with open(JOURNAL_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow({"fecha": args.fecha or datetime.now().strftime("%Y-%m-%d %H:%M"), "tipo": args.tipo,
                    "ticker": args.ticker.upper(), "acciones": args.acciones, "precio": args.precio,
                    "comision": round(fees(cfg, args.acciones * args.precio), 4), "nota": args.nota or ""})
    print(f"Registrado: {args.tipo} {args.acciones} {args.ticker.upper()} @ ${args.precio:,.2f}")
    cmd_diario(args, cfg)


def cmd_diario(args, cfg):
    if not os.path.exists(JOURNAL_PATH):
        print("Diario vacío. Usa: trading.py log compra AAPL 10 182.5")
        return
    pos, realized, closed = {}, 0.0, []
    with open(JOURNAL_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            t, q, p, c = r["ticker"], float(r["acciones"]), float(r["precio"]), float(r["comision"])
            sh, cost = pos.get(t, (0.0, 0.0))
            if r["tipo"] == "compra":
                pos[t] = (sh + q, cost + q * p + c)
            else:
                avg = cost / sh if sh else 0
                pnl = q * (p - avg) - c
                realized += pnl
                closed.append((r["fecha"], t, q, avg, p, pnl))
                pos[t] = (sh - q, cost - q * avg)
    print("\n## Operaciones cerradas")
    print("| Fecha | Ticker | Acciones | Costo prom. | Venta | P&L neto |\n|---|---|---:|---:|---:|---:|")
    for fe, t, q, a, p, pnl in closed:
        print(f"| {fe} | {t} | {q:g} | {a:,.2f} | {p:,.2f} | {pnl:+,.2f} |")
    wins = sum(1 for c in closed if c[5] > 0)
    if closed:
        print(f"\nP&L realizado: **${realized:+,.2f}** · {len(closed)} ops · aciertos {wins/len(closed)*100:.0f}% · "
              f"promedio por operación ${realized/len(closed):+,.2f}")
    print("\n## Posiciones abiertas")
    for t, (sh, cost) in pos.items():
        if sh > 0:
            print(f"- {t}: {sh:g} acciones, costo promedio ${cost/sh:,.2f} (incluye comisión)")



# -------------------------------------------------------------- simulador
SIM_PATH = os.path.join(BASE, "simulacion", "estado.json")


def sim_load():
    if not os.path.exists(SIM_PATH):
        sys.exit("No hay simulación activa. Usa: trading.py sim iniciar --saldo 5000")
    with open(SIM_PATH, encoding="utf-8") as f:
        return json.load(f)


def sim_save(st):
    os.makedirs(os.path.dirname(SIM_PATH), exist_ok=True)
    with open(SIM_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def parse_precios(txt):
    out = {}
    for part in (txt or "").split(","):
        if "=" in part:
            k, v = part.split("=")
            out[k.strip().upper()] = float(v)
    return out


def get_prices(tickers, manual, cfg):
    prices, errs = {}, []
    for t in tickers:
        if t in manual:
            prices[t] = manual[t]
            continue
        try:
            rows, _ = load_real(t, "5d")
            prices[t] = rows[-1]["close"]
            prices.setdefault("_fecha", rows[-1]["date"])
        except Exception as e:  # noqa: BLE001
            errs.append(f"{t}: {str(e)[:60]}")
    return prices, errs


def now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def sim_iniciar(args, cfg):
    if os.path.exists(SIM_PATH) and not args.reiniciar:
        sys.exit("Ya hay una simulación. Usa --reiniciar para empezar de cero.")
    st = {"inicio": now_s(), "fin": args.fin, "saldo_inicial": args.saldo, "efectivo": args.saldo,
          "posiciones": {}, "operaciones": [], "referencia_inicio": {}, "historial_valor": []}
    sim_save(st)
    print(f"Simulación iniciada con ${args.saldo:,.2f} de efectivo virtual. Fin: {args.fin or 'sin fecha'}")


def sim_operar(args, cfg, tipo):
    st, t = sim_load(), args.ticker.upper()
    manual = {t: args.precio} if args.precio else {}
    prices, errs = get_prices([t], manual, cfg)
    fecha_px = prices.pop("_fecha", None)
    if t not in prices:
        sys.exit(f"No pude obtener el precio de {t} ({'; '.join(errs)}). Pásalo con --precio.")
    px, q = prices[t], args.acciones
    fee = fees(cfg, q * px)
    pos = st["posiciones"].get(t, {"acciones": 0, "costo_total": 0.0})
    if tipo == "compra":
        total = q * px + fee
        if total > st["efectivo"] + 1e-9:
            sys.exit(f"Saldo insuficiente: necesitas ${total:,.2f} y tienes ${st['efectivo']:,.2f}")
        st["efectivo"] -= total
        pos["acciones"] += q
        pos["costo_total"] += total
        if args.stop: pos["stop"] = args.stop
        if args.objetivo: pos["objetivo"] = args.objetivo
        st["posiciones"][t] = pos
        pnl = None
    else:
        if q > pos["acciones"] + 1e-9:
            sys.exit(f"Solo tienes {pos['acciones']:g} acciones de {t}")
        avg = pos["costo_total"] / pos["acciones"]
        pnl = q * (px - avg) - fee
        st["efectivo"] += q * px - fee
        pos["acciones"] -= q
        pos["costo_total"] -= q * avg
        if pos["acciones"] <= 1e-9:
            st["posiciones"].pop(t)
        else:
            st["posiciones"][t] = pos
    st["operaciones"].append({"fecha": now_s(), "fecha_precio": fecha_px or "manual", "tipo": tipo, "ticker": t, "acciones": q, "precio": px,
                              "comision": round(fee, 2), "pnl": None if pnl is None else round(pnl, 2),
                              "motivo": args.motivo or ""})
    sim_save(st)
    msg = f"{tipo.upper()} {q:g} {t} @ ${px:,.2f} (comisión ${fee:,.2f})"
    if pnl is not None:
        msg += f" → P&L neto ${pnl:+,.2f}"
    print(msg + f". Efectivo: ${st['efectivo']:,.2f}")


def sim_estado(args, cfg):
    st = sim_load()
    manual = parse_precios(args.precios)
    tick = list(cfg["watchlist"].keys())
    for t in st["posiciones"]:
        if t not in tick:
            tick.append(t)
    prices, errs = get_prices(tick, manual, cfg)
    fecha_px = prices.pop("_fecha", None)
    if not st["referencia_inicio"] and len(prices) == len(cfg["watchlist"]):
        st["referencia_inicio"] = {t: prices[t] for t in cfg["watchlist"]}
    L = [f"# Simulación — estado al {now_s()}" + (f" (precios de cierre del {fecha_px})" if fecha_px else ""), ""]
    valor_pos, faltan = 0.0, []
    L += ["| Ticker | Acciones | Costo prom. | Precio | Valor | P&L no realizado | Stop | Objetivo | Alerta |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for t, p in st["posiciones"].items():
        avg = p["costo_total"] / p["acciones"]
        px = prices.get(t)
        if px is None:
            faltan.append(t)
            L.append(f"| {t} | {p['acciones']:g} | {avg:,.2f} | — | — | — | {fmt(p.get('stop'))} | {fmt(p.get('objetivo'))} | sin precio |")
            continue
        val = p["acciones"] * px
        valor_pos += val
        alerta = ""
        if p.get("stop") and px <= p["stop"]:
            alerta = "⚠️ tocó STOP → vender"
        elif p.get("objetivo") and px >= p["objetivo"]:
            alerta = "🎯 llegó al OBJETIVO → tomar ganancia"
        L.append(f"| {t} | {p['acciones']:g} | {avg:,.2f} | {px:,.2f} | {val:,.2f} | {val - p['costo_total']:+,.2f} | "
                 f"{fmt(p.get('stop'))} | {fmt(p.get('objetivo'))} | {alerta} |")
    if not st["posiciones"]:
        L.append("| — | sin posiciones | | | | | | | |")
    realizado = sum(o["pnl"] or 0 for o in st["operaciones"])
    cerradas = [o for o in st["operaciones"] if o["pnl"] is not None]
    L += [""]
    if faltan:
        L.append(f"⚠️ Sin precio para {', '.join(faltan)}: el valor total está incompleto.")
    else:
        total = st["efectivo"] + valor_pos
        res = total - st["saldo_inicial"]
        L += [f"- Efectivo: **${st['efectivo']:,.2f}** · En acciones: **${valor_pos:,.2f}**",
              f"- Valor total: **${total:,.2f}** → resultado **${res:+,.2f} ({res / st['saldo_inicial'] * 100:+.2f}%)**",
              f"- P&L realizado: ${realizado:+,.2f} en {len(cerradas)} ventas"
              + (f" · aciertos {sum(1 for o in cerradas if o['pnl'] > 0) / len(cerradas) * 100:.0f}%" if cerradas else "")]
        ref = st["referencia_inicio"]
        if ref and all(t in prices for t in ref):
            bench = sum(prices[t] / ref[t] - 1 for t in ref) / len(ref) * 100
            L.append(f"- Referencia (comprar y mantener las 5 por igual desde el inicio): {bench:+.2f}% → "
                     + ("EMPATADO con la referencia" if abs(res / st["saldo_inicial"] * 100 - bench) < 0.005
                        else "vas GANANDO a la referencia" if res / st["saldo_inicial"] * 100 > bench
                        else "vas PERDIENDO contra la referencia"))
        st["historial_valor"].append({"fecha": now_s(), "valor": round(total, 2)})
    if errs:
        L.append(f"- Errores de precio: {'; '.join(errs)}")
    sim_save(st)
    print("\n".join(L))


def sim_cmd(args, cfg):
    if args.accion == "iniciar":
        sim_iniciar(args, cfg)
    elif args.accion in ("comprar", "vender"):
        sim_operar(args, cfg, "compra" if args.accion == "comprar" else "venta")
    else:
        sim_estado(args, cfg)


# -------------------------------------------------------------- tablero
def decision(cfg, r):
    p, b = r.get("plan") or {}, r.get("backtest") or {}
    c1 = r.get("senal") == "COMPRAR"
    c2 = (p.get("ratio_rr") or 0) >= cfg.get("rr_minimo", 1.4)
    c3 = (b.get("promedio_pct") or -1) > 0 and b.get("operaciones", 0) > 0
    return c1, c2, c3


def cmd_tablero(args, cfg):
    """Exporta JSON para el tablero: senales, precios, cartera, posiciones, operaciones."""
    out = args.out
    os.makedirs(out, exist_ok=True)
    rmin = cfg.get("rr_minimo", 1.4)
    senales, precios, last = [], [], {}
    for t, nombre in cfg["watchlist"].items():
        rows, _ = load_real(t, cfg.get("historial", "2y"))
        r = analyze(cfg, t, rows)
        ind = indicators(rows)
        for i in range(max(0, len(rows) - 120), len(rows)):
            precios.append({"ticker": t, "fecha": rows[i]["date"], "cierre": round(rows[i]["close"], 2),
                            "sma50": None if ind["sma50"][i] is None else round(ind["sma50"][i], 2)})
        c1, c2, c3 = decision(cfg, r)
        p, b = r.get("plan") or {}, r.get("backtest") or {}
        last[t] = r["precio"]
        senales.append({
            "ticker": t, "nombre": nombre, "fecha": r["fecha"], "precio": round(r["precio"], 2),
            "var_dia_pct": round(r["var_dia_pct"], 2), "var_1m_pct": round(r["var_1m_pct"], 2),
            "var_3m_pct": round(r["var_3m_pct"], 2), "rsi": round(r["rsi"], 1), "puntaje": r["puntaje"],
            "senal": r["senal"], "entrar": "ENTRAR" if (c1 and c2 and c3) else "NO ENTRAR",
            "ok_senal": c1, "ok_rr": c2, "ok_backtest": c3, "rr_minimo": rmin,
            "acciones": p.get("acciones"), "inversion": round(p.get("inversion", 0), 2),
            "stop": round(p.get("stop", 0), 2), "objetivo": round(p.get("objetivo", 0), 2),
            "ganancia_neta": round(p.get("ganancia_neta_objetivo", 0), 2),
            "perdida_neta": round(p.get("perdida_neta_stop", 0), 2), "rr": round(p.get("ratio_rr") or 0, 2),
            "bt_ops": b.get("operaciones", 0), "bt_acierto_pct": round(b.get("tasa_acierto", 0), 1),
            "bt_prom_pct": round(b.get("promedio_pct", 0), 2), "razones": "; ".join(r["razones"])})
    st = json.load(open(SIM_PATH, encoding="utf-8")) if os.path.exists(SIM_PATH) else None
    cartera, posiciones, ops = [], [], []
    if st:
        val = 0.0
        for t, p in st["posiciones"].items():
            px = last.get(t) or 0
            avg = p["costo_total"] / p["acciones"]
            val += p["acciones"] * px
            posiciones.append({"ticker": t, "acciones": p["acciones"], "costo_prom": round(avg, 2), "precio": round(px, 2),
                               "valor": round(p["acciones"] * px, 2), "pnl": round(p["acciones"] * px - p["costo_total"], 2),
                               "stop": p.get("stop"), "objetivo": p.get("objetivo")})
        total = st["efectivo"] + val
        ref = st.get("referencia_inicio") or {}
        bench = (sum(last[t] / ref[t] - 1 for t in ref if t in last) / len(ref) * 100) if ref else 0
        cerradas = [o for o in st["operaciones"] if o.get("pnl") is not None]
        cartera.append({"saldo_inicial": st["saldo_inicial"], "efectivo": round(st["efectivo"], 2),
                        "valor_acciones": round(val, 2), "valor_total": round(total, 2),
                        "resultado_usd": round(total - st["saldo_inicial"], 2),
                        "resultado_pct": round((total / st["saldo_inicial"] - 1) * 100, 2),
                        "referencia_pct": round(bench, 2),
                        "vs_referencia_pct": round((total / st["saldo_inicial"] - 1) * 100 - bench, 2),
                        "operaciones": len(st["operaciones"]), "ventas": len(cerradas),
                        "aciertos_pct": round(sum(1 for o in cerradas if o["pnl"] > 0) / len(cerradas) * 100, 1) if cerradas else None,
                        "fecha_precios": senales[0]["fecha"], "inicio": st["inicio"], "fin": st.get("fin")})
        for k, o in enumerate(st["operaciones"]):
            ops.append({"n": k + 1, "fecha": o.get("fecha_precio") or o["fecha"], "tipo": o["tipo"], "ticker": o["ticker"],
                        "acciones": o["acciones"], "precio": o["precio"], "comision": o["comision"],
                        "pnl": o.get("pnl"), "motivo": o.get("motivo", "")})
    for name, data in (("senales", senales), ("precios", precios), ("cartera", cartera),
                       ("posiciones", posiciones), ("operaciones", ops)):
        with open(os.path.join(out, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"{name}.json: {len(data)} filas")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("scan")
    s.add_argument("--tickers", nargs="+")
    s.add_argument("--demo", action="store_true")
    s.add_argument("--csv-dir")
    s.add_argument("--json", action="store_true")
    c = sp.add_parser("calc")
    c.add_argument("--compra", type=float, required=True)
    c.add_argument("--venta", type=float, required=True)
    c.add_argument("--acciones", type=float, required=True)
    lg = sp.add_parser("log")
    lg.add_argument("tipo", choices=["compra", "venta"])
    lg.add_argument("ticker")
    lg.add_argument("acciones", type=float)
    lg.add_argument("precio", type=float)
    lg.add_argument("--nota")
    lg.add_argument("--fecha")
    sp.add_parser("diario")
    tb = sp.add_parser("tablero", help="exporta JSON para el tablero")
    tb.add_argument("--out", default=os.path.join(BASE, "data", "tablero"))
    dl = sp.add_parser("descargar", help="guarda datos reales en datos/ (lo usa GitHub Actions)")
    dl.add_argument("--tickers", nargs="+")
    sm = sp.add_parser("sim", help="cartera simulada (paper trading)")
    ss = sm.add_subparsers(dest="accion", required=True)
    a = ss.add_parser("iniciar")
    a.add_argument("--saldo", type=float, default=5000)
    a.add_argument("--fin")
    a.add_argument("--reiniciar", action="store_true")
    for nombre in ("comprar", "vender"):
        b = ss.add_parser(nombre)
        b.add_argument("ticker")
        b.add_argument("acciones", type=float)
        b.add_argument("--precio", type=float)
        b.add_argument("--stop", type=float)
        b.add_argument("--objetivo", type=float)
        b.add_argument("--motivo")
    e = ss.add_parser("estado")
    e.add_argument("--precios", help='manual: "AAPL=270.5,NVDA=190"')
    args = ap.parse_args()
    cfg = load_config()
    {"scan": cmd_scan, "calc": cmd_calc, "log": cmd_log, "diario": cmd_diario, "sim": sim_cmd, "descargar": cmd_descargar, "tablero": cmd_tablero}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
