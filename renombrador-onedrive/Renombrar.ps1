#Requires -Version 5.1
<#
.SYNOPSIS
    Renombrador de archivos para carpetas de OneDrive segun la nomenclatura del cliente.

.DESCRIPTION
    Funciona en 3 pasos, sin instalar nada (PowerShell viene con Windows):

      1. Planificar : revisa la carpeta y genera un PLAN en CSV (Excel) con el nombre
                      actual y el nombre propuesto. NO cambia nada.
      2. Aplicar    : lee el plan (que usted puede revisar/corregir en Excel) y renombra.
                      Guarda una bitacora con el hash SHA-256 antes y despues.
      3. Revertir   : usando la bitacora, devuelve los archivos a su nombre original.

    FIRMAS ELECTRONICAS: renombrar solo cambia la entrada del archivo en el sistema de
    archivos; el contenido (los bytes del PDF) no se toca, por lo que la firma sigue
    valida. El programa lo DEMUESTRA comparando el hash SHA-256 antes y despues de
    cada cambio de nombre. Nunca abre los PDF para escribir en ellos.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Renombrar.ps1 -Accion Planificar
    powershell -ExecutionPolicy Bypass -File .\Renombrar.ps1 -Accion Aplicar
    powershell -ExecutionPolicy Bypass -File .\Renombrar.ps1 -Accion Revertir
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Planificar', 'Aplicar', 'Revertir')]
    [string]$Accion,

    [string]$Config = (Join-Path $PSScriptRoot 'config.json'),

    # Ruta de un plan especifico (por defecto, el mas reciente de la carpeta 'planes')
    [string]$Plan,

    # Ruta de una bitacora especifica (por defecto, la mas reciente de 'bitacoras')
    [string]$Bitacora,

    # No pedir confirmacion (para uso automatizado)
    [switch]$SinConfirmar
)

$ErrorActionPreference = 'Stop'
$DirPlanes    = Join-Path $PSScriptRoot 'planes'
$DirBitacoras = Join-Path $PSScriptRoot 'bitacoras'
foreach ($d in @($DirPlanes, $DirBitacoras)) {
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

function Leer-Config {
    param([string]$Ruta)
    if (-not (Test-Path -LiteralPath $Ruta)) { throw "No se encontro el archivo de configuracion: $Ruta" }
    $cfg = Get-Content -LiteralPath $Ruta -Raw -Encoding UTF8 | ConvertFrom-Json

    # Valores por defecto
    $def = @{
        IncluirSubcarpetas    = $false
        Extensiones           = @('.pdf')
        PatronExtraccion      = ''
        Plantilla             = '{NombreOriginal}'
        Valores               = [pscustomobject]@{}
        FormatoFecha          = 'yyyyMMdd'
        FuenteFecha           = 'Modificacion'
        ConsecutivoInicio     = 1
        ConsecutivoDigitos    = 3
        Mayusculas            = $false
        QuitarTildes          = $true
        ReemplazarEspaciosCon = '_'
        VerificarHash         = $true
        Delimitador           = ';'
        LongitudMaximaRuta    = 255
    }
    foreach ($k in $def.Keys) {
        if (-not ($cfg.PSObject.Properties.Name -contains $k)) {
            $cfg | Add-Member -NotePropertyName $k -NotePropertyValue $def[$k]
        }
    }
    if (-not $cfg.Carpeta) { throw "En config.json falta 'Carpeta'." }
    if (-not (Test-Path -LiteralPath $cfg.Carpeta)) { throw "La carpeta no existe: $($cfg.Carpeta)" }
    return $cfg
}

# Excel puede guardar el CSV como UTF-8 (con BOM) o como ANSI. Detectamos cual.
function Importar-CsvSeguro {
    param([string]$Ruta, [string]$Delimitador)
    $bytes = [System.IO.File]::ReadAllBytes($Ruta)
    $esUtf8Bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    if ($esUtf8Bom) { $texto = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3) }
    else            { $texto = [System.Text.Encoding]::Default.GetString($bytes) }
    return $texto | ConvertFrom-Csv -Delimiter $Delimitador
}

function Exportar-Csv {
    param($Filas, [string]$Ruta, [string]$Delimitador)
    # UTF-8 con BOM: Excel lo abre con tildes y enies correctas
    $Filas | Export-Csv -LiteralPath $Ruta -Delimiter $Delimitador -NoTypeInformation -Encoding UTF8
}

# Archivo "solo en linea" de OneDrive (no descargado). Leerlo forzaria la descarga.
function Es-SoloEnLinea {
    param([System.IO.FileInfo]$Archivo)
    $attr = [int64]$Archivo.Attributes
    return (($attr -band 0x400000) -ne 0) -or (($attr -band 0x1000) -ne 0)   # RECALL_ON_DATA_ACCESS / OFFLINE
}

# Detecta si el PDF contiene una firma digital (solo LECTURA del archivo)
function Tiene-Firma {
    param([string]$Ruta)
    try {
        $fs = [System.IO.File]::Open($Ruta, 'Open', 'Read', 'ReadWrite')
        try {
            $buf = New-Object byte[] $fs.Length
            [void]$fs.Read($buf, 0, $buf.Length)
        } finally { $fs.Dispose() }
        $txt = [System.Text.Encoding]::GetEncoding(28591).GetString($buf)
        return ($txt -match '/ByteRange' -and ($txt -match '/Type\s*/Sig' -or $txt -match '/FT\s*/Sig'))
    } catch { return $null }
}

function Obtener-Hash {
    param([string]$Ruta)
    return (Get-FileHash -LiteralPath $Ruta -Algorithm SHA256).Hash
}

function Quitar-Tildes {
    param([string]$s)
    $n = $s.Normalize([System.Text.NormalizationForm]::FormD)
    $sb = New-Object System.Text.StringBuilder
    foreach ($c in $n.ToCharArray()) {
        if ([System.Globalization.CharUnicodeInfo]::GetUnicodeCategory($c) -ne 'NonSpacingMark') { [void]$sb.Append($c) }
    }
    return $sb.ToString().Normalize([System.Text.NormalizationForm]::FormC)
}

# Limpia el nombre para que sea valido en Windows, OneDrive y SharePoint
function Limpiar-Nombre {
    param([string]$Base, $Cfg)
    $s = $Base
    if ($Cfg.QuitarTildes) { $s = Quitar-Tildes $s }
    $s = $s -replace '[\\/:*?"<>|#%]', '-'
    $s = $s -replace '[\x00-\x1F]', ''
    if ($null -ne $Cfg.ReemplazarEspaciosCon) { $s = $s -replace '\s+', [string]$Cfg.ReemplazarEspaciosCon }
    $s = $s -replace '([-_])[-_]+', '$1'        # separadores repetidos
    $s = $s.Trim(' ', '.', '-', '_')
    if ($Cfg.Mayusculas) { $s = $s.ToUpperInvariant() }
    if ($s -match '^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])$') { $s = "_$s" }
    return $s
}

function Construir-Nombre {
    param([System.IO.FileInfo]$Archivo, [int]$Consecutivo, $Cfg, [ref]$Observacion)

    $base = [System.IO.Path]::GetFileNameWithoutExtension($Archivo.Name)
    $tokens = @{}

    # Valores fijos del config (Cliente, Proyecto, etc.)
    if ($Cfg.Valores) {
        foreach ($p in $Cfg.Valores.PSObject.Properties) { $tokens[$p.Name] = [string]$p.Value }
    }

    $fecha = if ($Cfg.FuenteFecha -eq 'Creacion') { $Archivo.CreationTime } else { $Archivo.LastWriteTime }
    $tokens['Fecha']          = $fecha.ToString($Cfg.FormatoFecha)
    $tokens['Anio']           = $fecha.ToString('yyyy')
    $tokens['Mes']            = $fecha.ToString('MM')
    $tokens['Dia']            = $fecha.ToString('dd')
    $tokens['Consecutivo']    = $Consecutivo.ToString().PadLeft([int]$Cfg.ConsecutivoDigitos, '0')
    $tokens['NombreOriginal'] = $base
    $tokens['Carpeta']        = $Archivo.Directory.Name

    # Datos extraidos del nombre actual con grupos con nombre: (?<Tipo>...)
    if ($Cfg.PatronExtraccion) {
        $m = [regex]::Match($base, $Cfg.PatronExtraccion, 'IgnoreCase')
        if (-not $m.Success) {
            $Observacion.Value = 'El nombre no coincide con PatronExtraccion; escriba el nombre nuevo a mano.'
            return $null
        }
        $rx = New-Object regex ($Cfg.PatronExtraccion, 'IgnoreCase')
        foreach ($g in $rx.GetGroupNames()) {
            if ($g -notmatch '^\d+$') { $tokens[$g] = $m.Groups[$g].Value }
        }
    }

    # Reemplazo de {Token} en la plantilla
    $faltantes = @()
    $nuevo = $Cfg.Plantilla
    foreach ($mm in [regex]::Matches($Cfg.Plantilla, '\{(\w+)\}')) {
        $k = $mm.Groups[1].Value
        if ($tokens.ContainsKey($k) -and $tokens[$k] -ne '') {
            $nuevo = $nuevo.Replace($mm.Value, $tokens[$k])
        } else {
            $faltantes += $k
        }
    }
    if ($faltantes.Count -gt 0) {
        $Observacion.Value = "Faltan datos para: $($faltantes -join ', ')"
        return $null
    }

    $limpio = Limpiar-Nombre $nuevo $Cfg
    if (-not $limpio) { $Observacion.Value = 'El nombre resultante quedo vacio.'; return $null }
    return $limpio + $Archivo.Extension.ToLowerInvariant()
}

# Mover con soporte para cambios solo de mayusculas/minusculas
function Mover-Archivo {
    param([string]$Origen, [string]$Destino)
    if ($Origen -ieq $Destino) {
        $tmp = "$Origen.renombrando_$([guid]::NewGuid().ToString('N').Substring(0,8))"
        [System.IO.File]::Move($Origen, $tmp)
        [System.IO.File]::Move($tmp, $Destino)
    } else {
        [System.IO.File]::Move($Origen, $Destino)
    }
}

function Ultimo-Archivo {
    param([string]$Dir, [string]$Patron)
    $f = Get-ChildItem -LiteralPath $Dir -Filter $Patron -File -ErrorAction SilentlyContinue |
         Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { return $f.FullName } else { return $null }
}

function Confirmar {
    param([string]$Mensaje)
    if ($SinConfirmar) { return $true }
    $r = Read-Host "$Mensaje (S/N)"
    return ($r -match '^(s|si|y|yes)$')
}

# ---------------------------------------------------------------------------
# 1. PLANIFICAR
# ---------------------------------------------------------------------------
function Invocar-Planificar {
    $cfg = Leer-Config $Config

    $params = @{ LiteralPath = $cfg.Carpeta; File = $true }
    if ($cfg.IncluirSubcarpetas) { $params['Recurse'] = $true }
    $exts = @($cfg.Extensiones | ForEach-Object { $_.ToLowerInvariant() })
    $archivos = Get-ChildItem @params |
                Where-Object { $exts.Count -eq 0 -or $exts -contains $_.Extension.ToLowerInvariant() } |
                Sort-Object DirectoryName, Name

    if (-not $archivos) { Write-Host "No se encontraron archivos en $($cfg.Carpeta)" -ForegroundColor Yellow; return }

    $destinos = @{}   # para detectar duplicados dentro del plan
    $filas = New-Object System.Collections.Generic.List[object]
    $consec = [int]$cfg.ConsecutivoInicio

    foreach ($a in $archivos) {
        $obs = ''
        $enLinea = Es-SoloEnLinea $a
        $firmado = 'NO VERIFICADO'
        if ($a.Extension -ieq '.pdf' -and -not $enLinea) {
            $t = Tiene-Firma $a.FullName
            if ($t -eq $true) { $firmado = 'SI' } elseif ($t -eq $false) { $firmado = 'NO' }
        }

        $nuevo = Construir-Nombre -Archivo $a -Consecutivo $consec -Cfg $cfg -Observacion ([ref]$obs)
        $estado = 'OK'
        $aplicar = 'SI'

        if (-not $nuevo) {
            $estado = 'REVISAR'; $aplicar = 'NO'; $nuevo = ''
        } else {
            $consec++
            $destinoFull = Join-Path $a.DirectoryName $nuevo
            $clave = $destinoFull.ToLowerInvariant()
            if ($nuevo -ceq $a.Name) {
                $estado = 'SIN CAMBIO'; $aplicar = 'NO'; $obs = 'Ya tiene el nombre correcto.'
            } elseif ($destinos.ContainsKey($clave)) {
                $estado = 'CONFLICTO'; $aplicar = 'NO'; $obs = "Mismo nombre nuevo que: $($destinos[$clave])"
            } elseif ((Test-Path -LiteralPath $destinoFull) -and ($destinoFull -ine $a.FullName)) {
                $estado = 'CONFLICTO'; $aplicar = 'NO'; $obs = 'Ya existe un archivo con ese nombre en la carpeta.'
            } elseif ($destinoFull.Length -gt [int]$cfg.LongitudMaximaRuta) {
                $estado = 'REVISAR'; $aplicar = 'NO'; $obs = "Ruta muy larga ($($destinoFull.Length) caracteres)."
            }
            $destinos[$clave] = $a.Name
        }

        $filas.Add([pscustomobject]@{
            Aplicar        = $aplicar
            Estado         = $estado
            NombreActual   = $a.Name
            NombreNuevo    = $nuevo
            FirmaDigital   = $firmado
            SoloEnLinea    = $(if ($enLinea) { 'SI' } else { 'NO' })
            Carpeta        = $a.DirectoryName
            Observacion    = $obs
        })
    }

    $ruta = Join-Path $DirPlanes ("plan_{0}.csv" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    Exportar-Csv $filas $ruta $cfg.Delimitador

    $res = $filas | Group-Object Estado | ForEach-Object { "{0}: {1}" -f $_.Name, $_.Count }
    Write-Host ""
    Write-Host "Plan generado: $ruta" -ForegroundColor Green
    Write-Host ("Archivos: {0}  |  {1}" -f $filas.Count, ($res -join '  |  '))
    Write-Host ("PDF con firma digital detectada: {0}" -f @($filas | Where-Object FirmaDigital -eq 'SI').Count)
    Write-Host ""
    Write-Host "Revise el plan en Excel. Puede corregir la columna 'NombreNuevo' y poner"
    Write-Host "'SI' o 'NO' en 'Aplicar'. Guarde como CSV y luego ejecute la opcion Aplicar."
    if (-not $SinConfirmar -and (Confirmar 'Abrir el plan ahora en Excel?')) { Invoke-Item -LiteralPath $ruta }
}

# ---------------------------------------------------------------------------
# 2. APLICAR
# ---------------------------------------------------------------------------
function Invocar-Aplicar {
    $cfg = Leer-Config $Config
    if (-not $Plan) { $Plan = Ultimo-Archivo $DirPlanes 'plan_*.csv' }
    if (-not $Plan -or -not (Test-Path -LiteralPath $Plan)) { throw 'No hay plan. Ejecute primero Planificar.' }

    $filas = @(Importar-CsvSeguro $Plan $cfg.Delimitador | Where-Object {
        $_.Aplicar -match '^\s*(SI|S)\s*$' -and $_.NombreNuevo -and $_.NombreNuevo.Trim() -ne $_.NombreActual
    })
    if ($filas.Count -eq 0) { Write-Host 'El plan no tiene filas marcadas para aplicar.' -ForegroundColor Yellow; return }

    Write-Host "Plan: $Plan"
    Write-Host "Se renombraran $($filas.Count) archivo(s)."
    if (-not (Confirmar 'Desea continuar?')) { Write-Host 'Cancelado.'; return }

    $rutaBit = Join-Path $DirBitacoras ("bitacora_{0}.csv" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
    $log = New-Object System.Collections.Generic.List[object]
    $ok = 0; $err = 0

    foreach ($f in $filas) {
        $nuevoNombre = Limpiar-Nombre ([System.IO.Path]::GetFileNameWithoutExtension($f.NombreNuevo.Trim())) $cfg
        $nuevoNombre += [System.IO.Path]::GetExtension($f.NombreNuevo.Trim())
        $origen  = Join-Path $f.Carpeta $f.NombreActual
        $destino = Join-Path $f.Carpeta $nuevoNombre
        $hA = ''; $hD = ''; $resultado = ''; $detalle = ''

        try {
            if (-not (Test-Path -LiteralPath $origen)) { throw 'El archivo original ya no existe.' }
            if ((Test-Path -LiteralPath $destino) -and ($destino -ine $origen)) { throw 'Ya existe un archivo con el nombre nuevo.' }
            if ([System.IO.Path]::GetExtension($origen) -ine [System.IO.Path]::GetExtension($destino)) {
                throw 'La extension cambiaria; se omite por seguridad.'
            }

            $info = Get-Item -LiteralPath $origen
            $verificar = $cfg.VerificarHash -and -not (Es-SoloEnLinea $info)
            if ($verificar) { $hA = Obtener-Hash $origen }

            Mover-Archivo $origen $destino

            if ($verificar) {
                $hD = Obtener-Hash $destino
                if ($hA -ne $hD) {
                    # No deberia ocurrir nunca: deshacer de inmediato
                    Mover-Archivo $destino $origen
                    throw 'El contenido cambio (hash distinto). Se revirtio el nombre.'
                }
                $detalle = 'Contenido identico (SHA-256). Firma intacta.'
            } else {
                $detalle = 'Archivo solo en linea o verificacion desactivada: no se calculo hash.'
            }
            $resultado = 'OK'; $ok++
            Write-Host "  OK   $($f.NombreActual)  ->  $nuevoNombre" -ForegroundColor Green
        } catch {
            $resultado = 'ERROR'; $detalle = $_.Exception.Message; $err++
            Write-Host "  ERR  $($f.NombreActual): $detalle" -ForegroundColor Red
        }

        $log.Add([pscustomobject]@{
            FechaHora      = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
            Usuario        = $env:USERNAME
            Carpeta        = $f.Carpeta
            NombreOriginal = $f.NombreActual
            NombreNuevo    = $nuevoNombre
            HashAntes      = $hA
            HashDespues    = $hD
            Resultado      = $resultado
            Detalle        = $detalle
        })
        # Guardar en cada paso: si algo se interrumpe, la bitacora queda completa
        Exportar-Csv $log $rutaBit $cfg.Delimitador
    }

    Write-Host ""
    Write-Host "Terminado. Correctos: $ok  |  Errores: $err" -ForegroundColor Cyan
    Write-Host "Bitacora (evidencia y para revertir): $rutaBit"
}

# ---------------------------------------------------------------------------
# 3. REVERTIR
# ---------------------------------------------------------------------------
function Invocar-Revertir {
    $cfg = Leer-Config $Config
    if (-not $Bitacora) { $Bitacora = Ultimo-Archivo $DirBitacoras 'bitacora_*.csv' }
    if (-not $Bitacora -or -not (Test-Path -LiteralPath $Bitacora)) { throw 'No hay bitacora para revertir.' }

    $filas = @(Importar-CsvSeguro $Bitacora $cfg.Delimitador | Where-Object Resultado -eq 'OK')
    [array]::Reverse($filas)
    if ($filas.Count -eq 0) { Write-Host 'La bitacora no tiene cambios para revertir.'; return }

    Write-Host "Bitacora: $Bitacora"
    Write-Host "Se devolveran $($filas.Count) archivo(s) a su nombre original."
    if (-not (Confirmar 'Desea continuar?')) { Write-Host 'Cancelado.'; return }

    $ok = 0; $err = 0
    foreach ($f in $filas) {
        $actual   = Join-Path $f.Carpeta $f.NombreNuevo
        $original = Join-Path $f.Carpeta $f.NombreOriginal
        try {
            if (-not (Test-Path -LiteralPath $actual)) { throw 'No se encuentra el archivo renombrado.' }
            if ((Test-Path -LiteralPath $original) -and ($original -ine $actual)) { throw 'Ya existe un archivo con el nombre original.' }
            Mover-Archivo $actual $original
            $ok++; Write-Host "  OK   $($f.NombreNuevo)  ->  $($f.NombreOriginal)" -ForegroundColor Green
        } catch {
            $err++; Write-Host "  ERR  $($f.NombreNuevo): $($_.Exception.Message)" -ForegroundColor Red
        }
    }
    Write-Host "Revertidos: $ok  |  Errores: $err" -ForegroundColor Cyan
}

# ---------------------------------------------------------------------------
try {
    switch ($Accion) {
        'Planificar' { Invocar-Planificar }
        'Aplicar'    { Invocar-Aplicar }
        'Revertir'   { Invocar-Revertir }
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
