@echo off
cd /d "%~dp0"
python "%~dp0run.py"
if errorlevel 1 pause
