@echo off
rem Przetwarza .\input -> .\output. Wymaga: pip install -r requirements.txt (Python 3.13+).
cd /d "%~dp0"
set PYTHON=python
if exist .venv\Scripts\python.exe set PYTHON=.venv\Scripts\python.exe
"%PYTHON%" -m doxforge run --input input --output output --budget-seconds 2640 %*
