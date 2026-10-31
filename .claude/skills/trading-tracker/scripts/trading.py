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
def fetch_yahoo(ticker, rango="2y"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?range={rango}&interval=1d&includeAdjustedClose=true")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
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
    results = []
    for t in tickers:
        try:
            if args.demo:
                rows = demo_series(t)
            elif args.csv_dir:
                rows = load_csv(os.path.join(args.csv_dir, f"{t}.csv"))
            else:
                rows = fetch_yahoo(t, cfg.get("historial", "2y"))
            if len(rows) < 60:
                raise ValueError(f"historial insuficiente ({len(rows)} días)")
            results.append(analyze(cfg, t, rows))
        except Exception as e:  # noqa: BLE001 — reportar y seguir con los demás
            results.append({"ticker": t, "nombre": cfg["watchlist"].get(t, t), "error": str(e)[:80]})
    md = render(results, cfg, args.demo)
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
    args = ap.parse_args()
    cfg = load_config()
    {"scan": cmd_scan, "calc": cmd_calc, "log": cmd_log, "diario": cmd_diario}[args.cmd](args, cfg)


if __name__ == "__main__":
    sys.exit(main())
