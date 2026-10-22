@echo off
rem Starts the audio-lab server and opens the web UI. Run from anywhere.
cd /d "%~dp0\..\.."
start "" http://localhost:5100/
python -m test_lab.audio_pipeline %*
pause
