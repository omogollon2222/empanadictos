@echo off
setlocal
cd /d "%~dp0"
title Renombrador OneDrive
:menu
cls
echo ============================================
echo   RENOMBRADOR DE ARCHIVOS - OneDrive
echo ============================================
echo   1. Planificar  (genera vista previa, no cambia nada)
echo   2. Aplicar     (renombra segun el ultimo plan)
echo   3. Revertir    (deshace el ultimo renombrado)
echo   4. Salir
echo.
set "op="
set /p op=Elija una opcion: 
if "%op%"=="1" set "acc=Planificar"
if "%op%"=="2" set "acc=Aplicar"
if "%op%"=="3" set "acc=Revertir"
if "%op%"=="4" exit /b
if not defined acc goto menu
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Renombrar.ps1" -Accion %acc%
set "acc="
echo.
pause
goto menu
