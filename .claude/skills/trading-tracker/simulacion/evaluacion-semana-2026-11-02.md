# Evaluación semana 2–6 nov 2026 (datos reales, dinero simulado)

| | Método técnico | Método Cardona | Comprar y mantener las 5 |
|---|---:|---:|---:|
| Inicio | $5.000,00 | $5.000,00 | $5.000,00 |
| Final (cierre 6 nov) | $5.000,00 | $4.974,58 | ≈ $4.770,50 |
| Resultado | $0,00 (0,00%) | −$25,42 (−0,51%) | −$229,50 (−4,59%) |
| Operaciones cerradas | 0 | 2 · 50% aciertos | — |

Operaciones Cardona: compra META 2 @ 621,03 y NVDA 6 @ 206,69 (3 nov, rebote en piso fuerte); venta NVDA @ 200,13 (−41,36, ruptura del piso) y META @ 630,00 (+15,94, PUT bajo promedio 40) el 4 nov.

Escenarios: salir solo por stop habría dado −89,48 en lugar de −25,42 (NVDA abrió el 5 nov en 196,07, bajo el stop). Entrar en AAPL el 4 nov (señal técnica COMPRAR rechazada por R:R 1,36) habría perdido −16,28.

Lecciones / ajustes propuestos:
1. Mantener rr_minimo = 1,4: el único filtro que rechazó una entrada evitó una pérdida.
2. Mantener la salida por señal PUT en Cardona: ahorró ~$64 frente a esperar el stop.
3. Cardona: no tomar "rebote en piso fuerte" si el piso se rompió en las últimas 3 sesiones (el piso de META bajó de 609 a 606,41 tras romperse y generó una entrada en falso).
4. stop_atr / objetivo_atr: sin operaciones técnicas, no hay evidencia para cambiarlos. Los objetivos de Cardona (+17–23%) son demasiado lejanos para un horizonte de una semana.
5. Muestra muy pequeña (2 operaciones): ninguna conclusión es estadísticamente sólida.
