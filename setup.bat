@echo off
rem MBUDDY one-command setup. Options: -Cpu  -SkipModel  -WithApp   (see setup.ps1)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 (
  echo.
  echo Setup stopped. Read the message above, fix it, and run setup again: finished steps are skipped.
  exit /b 1
)
