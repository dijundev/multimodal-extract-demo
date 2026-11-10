@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python extract_demo.py %*
) else (
    py -3 extract_demo.py %*
)

echo.
pause
