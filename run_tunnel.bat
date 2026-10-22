@echo off
echo MBUDDY tunnel — exposes http://127.0.0.1:5000 to the internet for ESP32 (WSS).
echo.
echo 1. Start the brain first:  run.bat
echo 2. Then run this script and copy the https://....trycloudflare.com host
echo 3. In MBUDDY.ino: USE_LOCAL_BRAIN false, WS_HOST = that host (no https://)
echo.
where cloudflared >nul 2>&1
if errorlevel 1 (
  echo ERROR: cloudflared not found. Install from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
  pause
  exit /b 1
)
cloudflared tunnel --url http://127.0.0.1:5000
pause
