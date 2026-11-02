@echo off
rem Runs qtask from a clone on Windows. Put this folder on PATH. cmd expands %NAME% inside
rem arguments and cuts them at a line break: pass such text with --body-file, or install
rem qtask with pipx or uv, which needs no launcher.
where python >nul 2>nul
if not errorlevel 1 goto python
where py >nul 2>nul
if not errorlevel 1 goto py
echo qtask: Python 3.11 or newer was not found on PATH 1>&2
exit /b 1
:python
python -I -X utf8 "%~dp0..\qtask.py" %*
exit /b %ERRORLEVEL%
:py
py -3 -I -X utf8 "%~dp0..\qtask.py" %*
exit /b %ERRORLEVEL%
