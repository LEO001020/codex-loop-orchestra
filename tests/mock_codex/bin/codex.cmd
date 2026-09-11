@echo off
setlocal
set "BASH_EXE=C:\Program Files\Git\bin\bash.exe"
if not exist "%BASH_EXE%" set "BASH_EXE=bash.exe"
"%BASH_EXE%" "%~dp0..\mock_codex_exec.sh" %*
exit /b %ERRORLEVEL%
