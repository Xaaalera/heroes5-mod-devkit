@echo off
where uv >nul 2>nul
if errorlevel 1 (
    echo Install uv once: https://docs.astral.sh/uv/getting-started/installation/
    exit /b 2
)
uv run --project "%~dp0." xkit %*
