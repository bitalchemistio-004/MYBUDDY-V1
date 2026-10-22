@echo off
echo ============================================
echo   MBUDDY Brain - local mode
echo ============================================
echo.
echo Your PC IPv4 addresses (set WS_HOST in MBUDDY.ino to one of these):
ipconfig | findstr /i "IPv4"
echo.
echo Brain URL:  http://127.0.0.1:5000/ws
echo ESP32:      USE_LOCAL_BRAIN true, WS_PORT 5000, same WiFi as this PC
echo Remote:     run_tunnel.bat instead, then USE_LOCAL_BRAIN false in .ino
echo.
.venv\Scripts\python.exe main.py
pause
