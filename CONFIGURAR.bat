@echo off
:: Configurar codificación básica de consola
chcp 1252 >nul
title Configurar Organizador de Archivos Visual

echo ===================================================
echo   CONFIGURADOR DEL ORGANIZADOR VISUAL DE ARCHIVOS
echo ===================================================
echo.

:: 1. Verificar si Python está instalado y disponible en el PATH
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python no está instalado o no se encuentra en el PATH.
    echo Por favor, instala Python y marca la opción "Add Python to PATH" durante el instalador.
    echo.
    pause
    exit /b 1
)

:: 2. Instalar silenciosamente las librerías necesarias
echo [1/5] Instalando librerías de Python (watchdog, customtkinter, pystray, pillow) en segundo plano...
pip install watchdog customtkinter pystray pillow --quiet
if %errorlevel% neq 0 (
    echo [ERROR] No se pudieron instalar las librerías de Python. Revisa tu conexión a Internet.
    echo.
    pause
    exit /b 1
)
echo [OK] Librerías instaladas correctamente.
echo.

:: 3. Cerrar instancias previas del organizador visual o de la versión modular anterior
echo [2/5] Deteniendo posibles instancias anteriores en ejecución...
powershell -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and ($_.CommandLine -like '*app_visual.py*' -or $_.CommandLine -like '*organizador.py*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>&1
echo [OK] Instancias anteriores cerradas.
echo.

:: 4. Crear el acceso directo en la carpeta de Inicio de Windows (Startup)
echo [3/5] Creando acceso directo en la carpeta de Inicio de Windows (Startup)...
set "SHORTCUT_PATH=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\OrganizadorVisualArchivos.lnk"
set "TARGET_PATH=%~dp0lanzador.vbs"

:: Obtener el directorio de trabajo actual y quitarle la barra invertida del final
set "WORKING_DIR=%~dp0"
if "%WORKING_DIR:~-1%"=="\" set "WORKING_DIR=%WORKING_DIR:~0,-1%"

powershell -ExecutionPolicy Bypass -Command "$WshShell = New-Object -ComObject WScript.Shell; $Shortcut = $WshShell.CreateShortcut('%SHORTCUT_PATH%'); $Shortcut.TargetPath = '%TARGET_PATH%'; $Shortcut.WorkingDirectory = '%WORKING_DIR%'; $Shortcut.Arguments = '--background'; $Shortcut.IconLocation = (Join-Path '%WORKING_DIR%' 'logo.ico'); $Shortcut.Save()"
if %errorlevel% neq 0 (
    echo [WARNING] No se pudo registrar el inicio automático en Startup.
) else (
    echo [OK] Acceso directo registrado en Startup.
)
echo.

:: 5. Crear el acceso directo en el Escritorio (Desktop)
echo [4/5] Creando acceso directo en el Escritorio (Desktop)...
powershell -ExecutionPolicy Bypass -Command "$WshShell = New-Object -ComObject WScript.Shell; $Desktop = [Environment]::GetFolderPath('Desktop'); $Shortcut = $WshShell.CreateShortcut((Join-Path $Desktop 'Organizador de Archivos.lnk')); $Shortcut.TargetPath = '%TARGET_PATH%'; $Shortcut.WorkingDirectory = '%WORKING_DIR%'; $Shortcut.IconLocation = (Join-Path '%WORKING_DIR%' 'logo.ico'); $Shortcut.Save()"
if %errorlevel% neq 0 (
    echo [WARNING] No se pudo crear el acceso directo en el Escritorio.
) else (
    echo [OK] Acceso directo creado en el Escritorio.
)
echo.

:: 6. Iniciar la aplicación de forma visible por primera vez
echo [5/5] Lanzando la interfaz de la aplicación por primera vez...
start "" pythonw "%~dp0app_visual.py"
if %errorlevel% neq 0 (
    echo [ERROR] No se pudo iniciar la aplicación visual.
    echo.
    pause
    exit /b 1
)
echo [OK] Aplicación iniciada con éxito.
echo.
echo ===================================================
echo   PROCESO COMPLETADO
echo   Usa la interfaz gráfica recién abierta para configurar
echo   tus carpetas vigiladas y reglas de organización.
echo ===================================================
echo.
pause
exit /b 0
