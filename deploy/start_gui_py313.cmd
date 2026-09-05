@echo off
setlocal
cd /d "%~dp0.."
set "AUTOFOCUS_YOLO_DEVICE=0"
"%~dp0..\runtime\venvs\autofocus\Scripts\python.exe" -m gui.main
if errorlevel 1 pause
endlocal
