@echo off
REM Double click this to run toss on Windows.
cd /d "%~dp0"
python -m pip install -e . --quiet
python -m toss serve %*
pause
