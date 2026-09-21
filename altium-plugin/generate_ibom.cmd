@echo off
setlocal EnableExtensions DisableDelayedExpansion

if not exist "%~dp0config.cmd" goto :not_installed
call "%~dp0config.cmd"

set "SOURCE=%~1"
if not defined SOURCE goto :no_source
if not exist "%SOURCE%" goto :missing_source

for %%I in ("%SOURCE%") do (
    set "SOURCE_DIR=%%~dpI"
    set "SOURCE_NAME=%%~nI"
)

set "OUTPUT_DIR=%SOURCE_DIR%ibom"
set "JSON_PATH=%OUTPUT_DIR%\%SOURCE_NAME%.ibom-input.json"
set "HTML_PATH=%OUTPUT_DIR%\%SOURCE_NAME%.ibom.html"
set "LOG_PATH=%OUTPUT_DIR%\%SOURCE_NAME%.ibom.log"

if not exist "%OUTPUT_DIR%" mkdir "%OUTPUT_DIR%"

"%ALTIUM_IBOM_PYTHON%" "%ALTIUM_IBOM_CONVERTER%" "%SOURCE%" -o "%JSON_PATH%" --html --html-dir "%OUTPUT_DIR%" --html-name "%SOURCE_NAME%.ibom" > "%LOG_PATH%" 2>&1
if errorlevel 1 goto :generation_failed

if /I "%~2"=="--no-open" exit /b 0
start "" "%HTML_PATH%"
exit /b 0

:not_installed
set "ERROR_MESSAGE=Altium iBOM is not installed correctly. Run install.ps1 again."
goto :show_error

:no_source
set "ERROR_MESSAGE=Altium did not provide a PCB project or PCB document."
goto :show_error

:missing_source
set "ERROR_MESSAGE=The selected Altium file does not exist: %SOURCE%"
goto :show_error

:generation_failed
set "ERROR_MESSAGE=iBOM generation failed. See: %LOG_PATH%"
goto :show_error

:show_error
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show($env:ERROR_MESSAGE, 'Altium iBOM', 'OK', 'Error') | Out-Null"
exit /b 1
