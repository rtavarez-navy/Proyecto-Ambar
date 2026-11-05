@echo off
cd /d "%~dp0"
"Servidor Corte SK2 2027.exe" --config "%~dp0server.json" --connect-google
pause
