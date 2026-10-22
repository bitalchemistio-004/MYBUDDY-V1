# MBUDDY Setup Guide

**Version:** 4.1.0-ENHANCED  
**Last Updated:** August 13, 2026  
**Estimated Setup Time:** 5 minutes

---

## 📋 Prerequisites

### Required Software
- **Python 3.8+** (Python 3.10 recommended)
- **Git** (for cloning repository)
- **PlatformIO** or **Arduino IDE** (for ESP32 firmware)

### Hardware Requirements
- **ESP32 Development Board** (ESP32-WROOM-32 or ESP32-DevKitC)
- **128×64 OLED Display** (SH1106 or SSD1306, I2C)
- **I2S MEMS Microphone** (INMP441 or compatible)
- **I2S DAC + Amplifier** (MAX98357A or PCM5102)
- **4-8Ω Speaker**
- **5V USB Power Supply** (2A recommended)
- **USB Cable** (for programming ESP32)

### API Keys
- **Groq API Key** (free tier available at console.groq.com)

---

## ⚡ Quick Start (5 Minutes)

### Step 1: Clone Repository
```bash
git clone <repository-url>
cd mbuddy
```

### Step 2: Create Virtual Environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Linux/Mac
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment
Create a `.env` file in the project root:
```env
GROQ_API_KEY=your_groq_api_key_here
PORT=5000
```

Get your Groq API key from: https://console.groq.com

### Step 5: Validate Setup
```bash
python setup_check.py
```

This script validates:
- ✅ Python version
- ✅ Virtual environment
- ✅ All required packages
- ✅ Environment variables
- ✅ Port availability
- ✅ Directory structure
- ✅ Audio devices
- ✅ Network connectivity

**Fix any errors before proceeding.**

### Step 6: Start Server
```bash
# Windows
.venv\Scripts\python.exe main.py

# Linux/Mac
python main.py
```

You should see:
```
------------------------------------------
🚀 MBUDDY SYSTEM VERSION: 4.1.0-OVERHAUL
------------------------------------------
🔍 mDNS Discovery: 192.168.1.100:5000
📡 WebSocket URL: ws://192.168.1.100:5000/ws
======== Running on http://0.0.0.0:5000 ========
```

**Copy the IP address shown** - you'll need it for the ESP32 firmware.

### Step 7: Upload ESP32 Firmware

#### Using PlatformIO (Recommended)
1. Open `firmware/mbuddy` in VS Code with PlatformIO
2. Edit `firmware/mbuddy/src/MBUDDY.ino`:
   - Update WiFi credentials:
     ```cpp
     const char* ssid = "YourWiFiSSID";
     const char* password = "YourWiFiPassword";
     ```
   - **Note:** The server IP is now auto-discovered via mDNS - no manual IP configuration needed!

3. Connect ESP32 via USB
4. Click "PlatformIO: Upload" (or press Ctrl+Alt+U)
5. Open Serial Monitor (115200 baud) to verify connection

#### Using Arduino IDE
1. Install ESP32 board support (if not already installed)
2. Install required libraries:
   - U8g2 (OLED display)
   - ArduinoWebsockets
   - ESP32-audioI2S
3. Open `firmware/mbuddy/src/MBUDDY.ino`
4. Update WiFi credentials (see above)
5. Select board: "ESP32 Dev Module"
6. Upload sketch

### Step 8: Verify Connection
Watch the ESP32 serial monitor. You should see:
```
[WiFi] Connected to YourWiFiSSID
[mDNS] Discovering MBUDDY server...
[mDNS] Found server at 192.168.1.100:5000
[WS] Connecting to ws://192.168.1.100:5000/ws
[OK] Connected: 192.168.1.100
```

### Step 9: Open Dashboard
Open your browser to: `http://<server-ip>:5000/dashboard`

Example: `http://192.168.1.100:5000/dashboard`

You should see:
- Live OLED preview
- Device connection status (green dot)
- Emotion/energy displays
- Real-time status updates

### Step 10: Test Voice Interaction
Say the wake word: **"Hey MBUDDY"** or **"MBUDDY"**

The system should:
1. Respond with a greeting
2. Show "listening" animation on OLED
3. Display emotion changes in dashboard

**Setup complete! 🎉**

---

## 🔧 Hardware Wiring

### ESP32 Pin Connections

#### OLED Display (I2C)
```
OLED SDA  →  GPIO 21
OLED SCL  →  GPIO 22
OLED VCC  →  3.3V
OLED GND  →  GND
```

#### I2S Microphone (INMP441)
```
MIC SCK   →  GPIO 26
MIC WS    →  GPIO 25
MIC SD    →  GPIO 33
MIC VCC   →  3.3V
MIC GND   →  GND
MIC L/R   →  GND (left channel)
```

#### I2S DAC/Amp (MAX98357A)
```
AMP BCLK  →  GPIO 14
AMP LRC   →  GPIO 15
AMP DIN   →  GPIO 27
AMP VCC   →  5V (or 3.3V depending on module)
AMP GND   →  GND
```

#### Speaker
```
Speaker + →  AMP OUT+
Speaker - →  AMP OUT-
```

### Power Considerations
- **Total current draw:** ~500-800mA during active use
- **Recommended:** 5V 2A USB power supply
- **I2C speed:** 100kHz (configured in firmware to prevent brownout)

**⚠️ Warning:** Do not use 400kHz I2C speed - this causes brownout on some ESP32 boards.

---

## 📱 Testing Features

### 1. Voice Commands
Try these phrases:
- "Hey MBUDDY" (wake)
- "What's the time?"
- "Tell me a joke"
- "How are you feeling?"
- "Good night" (sleep)

### 2. Dashboard Controls
Navigate to: `http://<server-ip>:5000/dashboard`

Test:
- **Actions tab:** Click animation cards (joystick, dragon_fire, etc.)
- **SFX buttons:** Play sound effects
- **Speech tab:** Type text and click "SPEAK"
- **State/Energy:** Override emotional state

### 3. Real-Time Updates
Watch the dashboard while interacting:
- Emotion changes update instantly (<50ms)
- Energy bar drains during conversations
- Action animations appear in OLED preview
- Behavior traces log in real-time

### 4. Hardware Integration
If you have USB serial access:
- Click "Hardware Link" → "SCAN USB"
- Select your ESP32 COM port
- Click "CONNECT"
- Send commands directly via serial bridge

---

## 🐛 Troubleshooting

### Server Won't Start

**Error:** `Port 5000 already in use`
- **Fix:** Change `PORT` in `.env` to another port (e.g., 5001)

**Error:** `ModuleNotFoundError: No module named 'edge_tts'`
- **Fix:** You're running system Python, not venv Python
- Use: `.venv\Scripts\python.exe main.py` (Windows)
- Or: `python main.py` from activated venv

**Error:** `GROQ_API_KEY is missing`
- **Fix:** Create `.env` file with valid API key

### ESP32 Connection Issues

**ESP32 shows:** `[mDNS] Server not found`
- **Fix 1:** Check server is running (`python main.py`)
- **Fix 2:** Ensure ESP32 and PC are on same network
- **Fix 3:** Some routers block mDNS - check router settings

**ESP32 shows:** `[WS] Connection failed`
- **Fix 1:** Verify server IP is correct (check server startup logs)
- **Fix 2:** Firewall may be blocking port 5000 - add exception
- **Fix 3:** Restart both server and ESP32

**ESP32 keeps rebooting (brownout)**
- **Fix 1:** Use 5V 2A power supply (not USB hub)
- **Fix 2:** Verify I2C speed is 100kHz (not 400kHz)
- **Fix 3:** Check wiring for shorts

### Audio Issues

**No audio input detected**
- **Fix 1:** Check microphone wiring (especially GND)
- **Fix 2:** Verify I2S pins match firmware configuration
- **Fix 3:** Test with `python setup_check.py` audio validation

**No audio output**
- **Fix 1:** Check speaker wiring
- **Fix 2:** Verify amplifier has power
- **Fix 3:** Test with dashboard "Speech" tab

**Echo/feedback loop**
- **Fix:** Increase distance between microphone and speaker
- **Note:** Barge-in thresholds prevent most echo issues

### Dashboard Issues

**Status shows "checking" forever**
- **Fix 1:** Check browser console for errors
- **Fix 2:** Verify server URL is correct
- **Fix 3:** Try hard refresh (Ctrl+F5)

**Real-time updates not working**
- **Fix:** Dashboard may have fallen back to polling
- Check: Open DevTools → Network → WS tab
- Should show WebSocket connection to `/status/stream`

---

## 🔒 Security Notes

### Network Security
- **Local only:** Server binds to all interfaces (0.0.0.0) but should only be accessed on local network
- **No authentication:** Dashboard has no login - use firewall rules for protection
- **HTTPS:** Not configured by default - add reverse proxy (nginx/Caddy) if exposing publicly

### API Key Security
- **.env file:** Never commit to git (already in .gitignore)
- **Read-only scope:** Groq API key should be read-only if possible
- **Rate limits:** Groq free tier has rate limits - monitor usage

### Privacy
- **Local processing:** All audio processing happens locally
- **Cloud LLM:** Only text transcripts sent to Groq API
- **No recording:** Audio is not saved to disk
- **Memory storage:** Conversations stored in `mbuddy_memory.json` and SQLite - delete to reset

---

## 📊 Performance Tuning

### Reduce Latency
1. **Use wired Ethernet** instead of WiFi (if possible)
2. **Increase Python process priority** (Windows: Task Manager → Details → Set Priority)
3. **Disable antivirus real-time scanning** for project folder

### Reduce Memory Usage
1. **Limit conversation log:** Edit `memory/long_term.py` - reduce max entries from 200 to 100
2. **Disable vector store:** Comment out FAISS imports (not recommended)
3. **Prune old threads:** Use dashboard or delete `memory/conversation_threads.db`

### Improve Audio Quality
1. **Adjust microphone gain:** Edit `MBUDDY.ino` - increase `AUDIO_CHUNK_SIZE`
2. **Reduce noise:** Add foam windscreen to microphone
3. **Better speaker:** Upgrade to 8Ω 3W speaker for clearer voice

---

## 🔄 Updating

### Update Python Dependencies
```bash
.venv\Scripts\activate
pip install --upgrade -r requirements.txt
```

### Update ESP32 Firmware
1. Pull latest code: `git pull`
2. Re-upload firmware via PlatformIO
3. Monitor serial output for any errors

### Reset Memory
To start fresh:
```bash
rm mbuddy_memory.json
rm memory/vectors.npz
rm memory/conversation_threads.db
rm memory/tasks.db
```

**Warning:** This deletes all conversation history and tasks.

---

## 📚 Next Steps

### Explore Features
- Read `IMPLEMENTATION_SUMMARY.md` for technical details
- Open `MBUDDY_GUIDE.pptx` for visual overview
- Check `IMPROVEMENT_PLAN.md` for future roadmap

### Customize
- Add custom animations in `firmware/mbuddy/lib/esp32-eyes-main/`
- Modify personality in `brain/personality.py`
- Adjust emotion thresholds in `brain/emotion_authority.py`

### Contribute
- Report issues on GitHub
- Submit pull requests
- Share your setup photos/videos

---

## ⚙️ Advanced Configuration

### Environment Variables (.env)
```env
# Required
GROQ_API_KEY=your_key_here

# Optional
PORT=5000                          # Server port
GROQ_MODEL=llama-3.3-70b-versatile # LLM model
AWAKE_IDLE_TIMEOUT=180             # Auto-sleep after N seconds
INNER_THOUGHT_VOLUME=0.35          # Inner monologue volume
```

### Config Overrides (core/config.py)
Edit these constants for fine-tuning:
```python
LLM_TIMEOUT_SECONDS = 25.0        # LLM response timeout
MIN_SPEECH_DURATION = 0.52        # Minimum speech length
BARGE_IN_MIN_RMS = 0.040          # Interrupt threshold
SAMPLE_RATE = 16000               # Audio sample rate
```

---

## 💡 Tips & Best Practices

### Daily Use
1. **Keep server running:** Use `systemd` (Linux) or Task Scheduler (Windows) for auto-start
2. **Monitor dashboard:** Check for errors in behavior traces
3. **Regular restarts:** Restart server weekly to clear memory leaks

### Development
1. **Use virtual environment:** Always activate `.venv` before running
2. **Check logs:** Monitor console output for warnings
3. **Test changes:** Use dashboard for quick testing before voice tests

### Backup
1. **Export memory:** Dashboard → Logs → EXPORT
2. **Backup database:** Copy `memory/*.db` files
3. **Version control:** Commit `.env.example` (not `.env`)

---

## 🆘 Getting Help

### Resources
- **Documentation:** Check other `.md` files in project root
- **Code comments:** Most modules have detailed docstrings
- **Dashboard:** Built-in help tooltips

### Community
- **GitHub Issues:** Report bugs and request features
- **Discord:** [Community server link]
- **Email:** [Support email]

### Debug Mode
Enable verbose logging:
```python
# In main.py
import logging
logging.basicConfig(level=logging.DEBUG)
```

---

**Setup complete! Enjoy your AI companion.** 🤖✨
