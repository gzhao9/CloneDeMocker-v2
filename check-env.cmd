@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-env.ps1" %*
exit /b %errorlevel%
