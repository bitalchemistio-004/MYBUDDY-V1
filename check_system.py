"""
check_system.py - MBUDDY setup and environment validation.

Catches configuration errors before runtime. Merged from the former
setup_check.py (structured validator) and check_system.py (COM-port scan).
It only reports; it never installs anything.

Usage:
    python check_system.py
"""

import os
import sys
import socket
import importlib.util
from pathlib import Path
from typing import List

# ANSI color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
RESET = '\033[0m'
BOLD = '\033[1m'

class SetupValidator:
    """Validates MBUDDY setup and configuration."""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passed: List[str] = []

    def check_python_version(self) -> bool:
        """Verify Python version >= 3.8"""
        version = sys.version_info
        if version >= (3, 8):
            self.passed.append(f"Python {version.major}.{version.minor}.{version.micro}")
            return True
        else:
            self.errors.append(f"Python 3.8+ required (found {version.major}.{version.minor})")
            return False

    def check_virtual_env(self) -> bool:
        """Check if running in virtual environment"""
        in_venv = sys.prefix != sys.base_prefix or 'VIRTUAL_ENV' in os.environ
        venv_path = Path('.venv')

        if in_venv:
            self.passed.append("Virtual environment active")
            return True
        elif venv_path.exists():
            self.warnings.append("Virtual environment exists but not activated")
            self.warnings.append("  Run: .venv\\Scripts\\python.exe main.py")
            return False
        else:
            self.errors.append("No virtual environment found")
            self.errors.append("  Run: python -m venv .venv")
            return False

    def check_required_packages(self) -> bool:
        """Verify all required packages are installed"""
        required = [
            'aiohttp', 'edge_tts', 'groq', 'numpy',
            'faiss', 'dotenv', 'pyaudio'
        ]

        missing = []
        for package in required:
            # Handle package name mapping
            import_name = {
                'dotenv': 'dotenv',
                'edge_tts': 'edge_tts',
                'pyaudio': 'pyaudio'
            }.get(package, package)

            if importlib.util.find_spec(import_name) is None:
                missing.append(package)

        if not missing:
            self.passed.append(f"All {len(required)} packages installed")
            return True
        else:
            self.errors.append(f"Missing packages: {', '.join(missing)}")
            self.errors.append("  Run: pip install -r requirements.txt")
            return False

    def check_env_file(self) -> bool:
        """Verify .env file exists and has required keys"""
        env_path = Path('.env')

        if not env_path.exists():
            self.errors.append(".env file not found")
            self.errors.append("  Create .env with GROQ_API_KEY=your_key_here")
            return False

        # Read .env and check for GROQ_API_KEY
        env_content = env_path.read_text()
        if 'GROQ_API_KEY' not in env_content:
            self.errors.append("GROQ_API_KEY not found in .env")
            return False

        # Check if key has value
        for line in env_content.split('\n'):
            if line.startswith('GROQ_API_KEY'):
                if '=' in line:
                    key_value = line.split('=', 1)[1].strip()
                    if key_value and key_value != 'your_key_here':
                        self.passed.append("GROQ_API_KEY configured")
                        return True
                    else:
                        self.errors.append("GROQ_API_KEY is empty")
                        return False

        return False

    def check_port_available(self, port: int = 5000) -> bool:
        """Check if the server port is available"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('localhost', port))
            sock.close()

            if result != 0:
                self.passed.append(f"Port {port} available")
                return True
            else:
                self.warnings.append(f"Port {port} already in use")
                self.warnings.append("  Stop existing process or change PORT in .env")
                return False
        except Exception as e:
            self.warnings.append(f"Could not check port {port}: {e}")
            return False

    def check_required_directories(self) -> bool:
        """Verify required directories exist"""
        required_dirs = ['api', 'brain', 'core', 'memory', 'stt', 'tts', 'utils']
        missing_dirs = [d for d in required_dirs if not Path(d).exists()]

        if not missing_dirs:
            self.passed.append(f"All {len(required_dirs)} core directories found")
            return True
        else:
            self.errors.append(f"Missing directories: {', '.join(missing_dirs)}")
            return False

    def check_memory_files(self) -> bool:
        """Check if memory storage files are accessible"""
        memory_file = Path('mbuddy_memory.json')
        vector_file = Path('memory/vectors.npz')

        issues = []

        if not memory_file.exists():
            self.warnings.append("mbuddy_memory.json not found (will be created)")
        else:
            try:
                import json
                with open(memory_file, 'r', encoding='utf-8') as f:
                    json.load(f)
                self.passed.append("Memory JSON validated")
            except Exception as e:
                self.warnings.append(f"Memory JSON corrupted: {e}")

        if not vector_file.exists():
            self.warnings.append("vectors.npz not found (will be created)")
        else:
            try:
                import numpy as np
                np.load(vector_file)
                self.passed.append("Vector store validated")
            except Exception as e:
                self.warnings.append(f"Vector store corrupted: {e}")

        return True

    def check_audio_devices(self) -> bool:
        """Verify audio input/output devices are available"""
        try:
            import pyaudio
            p = pyaudio.PyAudio()

            input_count = p.get_device_count()
            has_input = False
            has_output = False

            for i in range(input_count):
                device_info = p.get_device_info_by_index(i)
                if device_info['maxInputChannels'] > 0:
                    has_input = True
                if device_info['maxOutputChannels'] > 0:
                    has_output = True

            p.terminate()

            if has_input and has_output:
                self.passed.append(f"Audio devices available ({input_count} total)")
                return True
            else:
                if not has_input:
                    self.warnings.append("No audio input device found")
                if not has_output:
                    self.warnings.append("No audio output device found")
                return False

        except Exception as e:
            self.warnings.append(f"Could not check audio devices: {e}")
            return False

    def check_network_connectivity(self) -> bool:
        """Verify network connectivity"""
        try:
            # Try to get local IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()

            self.passed.append(f"Network available (IP: {local_ip})")
            return True
        except Exception as e:
            self.errors.append("Network connectivity issue")
            self.errors.append(f"  {e}")
            return False

    def check_serial_ports(self) -> bool:
        """List USB serial ports (informational; a missing device is normal)"""
        try:
            import serial.tools.list_ports
        except ImportError:
            self.warnings.append("pyserial not installed; cannot list COM ports")
            return False
        ports = list(serial.tools.list_ports.comports())
        if ports:
            names = ", ".join(f"{p.device} ({p.description})" for p in ports)
            self.passed.append(f"Serial ports: {names}")
        else:
            self.passed.append("No USB serial ports connected")
        return True

    def run_all_checks(self):
        """Run all validation checks"""
        print(f"\n{BOLD}{'='*60}{RESET}")
        print(f"{BOLD}  MBUDDY Setup Validation{RESET}")
        print(f"{BOLD}{'='*60}{RESET}\n")

        checks = [
            ("Python Version", self.check_python_version),
            ("Virtual Environment", self.check_virtual_env),
            ("Required Packages", self.check_required_packages),
            ("Environment Variables", self.check_env_file),
            ("Server Port", self.check_port_available),
            ("Directory Structure", self.check_required_directories),
            ("Memory Files", self.check_memory_files),
            ("Audio Devices", self.check_audio_devices),
            ("Network", self.check_network_connectivity),
            ("Serial Ports", self.check_serial_ports),
        ]

        for name, check_func in checks:
            print(f"Checking {name}...", end=' ')
            try:
                result = check_func()
                if result:
                    print(f"{GREEN}✓{RESET}")
                else:
                    print(f"{YELLOW}⚠{RESET}")
            except Exception as e:
                print(f"{RED}✗{RESET}")
                self.errors.append(f"{name} check failed: {e}")

        return self.print_summary()

    def print_summary(self):
        """Print validation summary"""
        print(f"\n{BOLD}{'='*60}{RESET}")
        print(f"{BOLD}  Summary{RESET}")
        print(f"{BOLD}{'='*60}{RESET}\n")

        if self.passed:
            print(f"{GREEN}✓ Passed ({len(self.passed)}){RESET}")
            for item in self.passed:
                print(f"  • {item}")
            print()

        if self.warnings:
            print(f"{YELLOW}⚠ Warnings ({len(self.warnings)}){RESET}")
            for item in self.warnings:
                print(f"  • {item}")
            print()

        if self.errors:
            print(f"{RED}✗ Errors ({len(self.errors)}){RESET}")
            for item in self.errors:
                print(f"  • {item}")
            print()

        print(f"{BOLD}{'='*60}{RESET}\n")

        if self.errors:
            print(f"{RED}Setup validation failed. Fix errors above before running MBUDDY.{RESET}\n")
            return False
        elif self.warnings:
            print(f"{YELLOW}Setup validation passed with warnings.{RESET}")
            print("System may work but some features might be limited.\n")
            return True
        else:
            print(f"{GREEN}✓ All checks passed! MBUDDY is ready to run.{RESET}\n")
            print(f"Start the system with: {BOLD}.venv\\Scripts\\python.exe main.py{RESET}\n")
            return True


if __name__ == "__main__":
    # The check marks are not in cp1252, so a redirected or legacy console
    # would crash on the first result without this.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    validator = SetupValidator()
    success = validator.run_all_checks()
    sys.exit(0 if success else 1)
