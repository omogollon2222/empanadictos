# Renombrador de archivos – OneDrive

Programa para Windows (PowerShell, ya incluido en Windows 10/11, **no requiere instalación ni permisos de administrador**) que renombra archivos de una carpeta de OneDrive según la nomenclatura del cliente, **sin invalidar las firmas electrónicas de los PDF**.

## ¿Por qué no se dañan las firmas?
La firma digital de un PDF protege los **bytes del contenido** (`/ByteRange`), no el nombre del archivo. Renombrar solo cambia la entrada en el sistema de archivos. El programa:
- **Nunca abre los PDF para escribir**: solo los lee para detectar si están firmados.
- Calcula el **hash SHA-256 antes y después** de cada renombrado. Si fuera distinto (no debería ocurrir), deshace el cambio automáticamente.
- Deja una **bitácora CSV** con usuario, fecha, nombre anterior, nombre nuevo y hashes: sirve como evidencia de auditoría (ISO 9001 – control de información documentada).

## Archivos
| Archivo | Para qué |
|---|---|
| `Ejecutar.bat` | Menú. Doble clic para usar. |
| `Renombrar.ps1` | El programa. |
| `config.json` | Carpeta y regla de nombres del cliente. |
| `planes\` | Vistas previas generadas (CSV para Excel). |
| `bitacoras\` | Registro de cada ejecución (evidencia y para revertir). |

> Copie la carpeta del programa **fuera** de la carpeta de OneDrive que va a renombrar.

## Uso (3 pasos)
1. **Configurar** `config.json` (Bloc de notas): ruta de la carpeta y plantilla.
2. **Planificar** (opción 1): genera `planes\plan_FECHA.csv`. No cambia nada. Ábralo en Excel, revise la columna `NombreNuevo` (puede corregirla a mano) y `Aplicar` (SI/NO). Guarde como CSV.
3. **Aplicar** (opción 2): renombra lo marcado con `SI` y guarda la bitácora.

¿Algo salió mal? **Revertir** (opción 3) devuelve los nombres originales usando la última bitácora.

## Configuración (`config.json`)
| Clave | Descripción |
|---|---|
| `Carpeta` | Ruta local de OneDrive. Use `\\` en lugar de `\`. Ej.: `C:\\Users\\jperez\\OneDrive - SLB\\Cliente` |
| `IncluirSubcarpetas` | `true` / `false` |
| `Extensiones` | Ej.: `[".pdf"]`, `[".pdf", ".xlsx"]`. Vacío `[]` = todos. |
| `PatronExtraccion` | Expresión regular con grupos con nombre para sacar datos del nombre actual. Ej.: `(?<Tipo>[A-Za-z]+)[ _-]*(?<Numero>\\d+)` toma de `Factura 123.pdf` → Tipo=`Factura`, Numero=`123`. Vacío = no extrae. |
| `Plantilla` | Estructura del cliente con `{Campos}`. |
| `Valores` | Campos fijos (Cliente, Proyecto, Contrato…). |
| `FormatoFecha`, `FuenteFecha` | `yyyyMMdd`, `yyyy-MM-dd`…; `Modificacion` o `Creacion`. |
| `ConsecutivoInicio`, `ConsecutivoDigitos` | Para `{Consecutivo}` → `001`, `002`… |
| `Mayusculas`, `QuitarTildes`, `ReemplazarEspaciosCon` | Normalización del nombre. |
| `VerificarHash` | `true` recomendado (evidencia de integridad). |
| `Delimitador` | `;` para Excel en español; `,` si su Excel usa coma. |

**Campos disponibles en la plantilla:** `{Fecha}`, `{Anio}`, `{Mes}`, `{Dia}`, `{Consecutivo}`, `{NombreOriginal}`, `{Carpeta}`, los de `Valores` y los grupos de `PatronExtraccion`.

Ejemplo: `{Cliente}-{Proyecto}-{Tipo}-{Numero}-{Fecha}` → `CLIENTE-PRY001-FACTURA-123-20261031.pdf`

## Controles de seguridad
- Vista previa obligatoria antes de cambiar nada.
- Detecta duplicados, archivos que ya existen, rutas largas (>255) y caracteres no válidos en OneDrive/SharePoint (`\ / : * ? " < > | # %`).
- Nunca cambia la extensión.
- Archivos **"solo en línea"** de OneDrive (nube ☁): se renombran sin descargarlos; en ellos no se calcula hash ni se detecta firma (columna `SoloEnLinea`). Si quiere la evidencia completa, antes haga clic derecho → *Mantener siempre en este dispositivo*.
- El cambio de nombre se sincroniza como "renombrar" en OneDrive: se conserva el historial de versiones y los enlaces compartidos.

## Si la PC corporativa bloquea scripts
`Ejecutar.bat` ya usa `-ExecutionPolicy Bypass` solo para esta ejecución (no modifica la política del equipo). Si aún así está bloqueado por directiva de grupo (GPO/AppLocker), solicite a TI la autorización del script; el código es abierto y legible para revisión.
