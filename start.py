import os
import sys
import time
import socket
import signal
import atexit
import json
import subprocess
import urllib.request
import urllib.error
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
LOGS_DIR = BACKEND_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
BACKEND_LOG_PATH = LOGS_DIR / "backend.log"

VENV_PYTHON = BACKEND_DIR / "venv" / "Scripts" / "python.exe"

# Add backend directory to sys.path to read unified configuration
sys.path.insert(0, str(BACKEND_DIR))
try:
    from config import settings
    CONFIG_HOST = settings.HOST
    CONFIG_PORT = settings.PORT
except Exception:
    CONFIG_HOST = "0.0.0.0"
    CONFIG_PORT = 8000

child_processes = []

def cleanup_child_processes():
    for proc in child_processes:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception:
            pass

atexit.register(cleanup_child_processes)

def get_python_exe() -> str:
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    if Path("C:/Python314/python.exe").exists():
        return "C:/Python314/python.exe"
    return sys.executable or "py"

def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip

def is_backend_healthy(port: int, timeout: float = 1.0) -> bool:
    """Checks if OS Agent backend is responding and ready on the given port."""
    for host in ["127.0.0.1", "localhost"]:
        url = f"http://{host}:{port}/api/health/ready"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OS-Agent-Startup"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("status") == "ready" or data.get("ready") is True:
                        return True
        except Exception:
            # Fallback check on /health or /api/status
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                pass
    return False

def clean_unresponsive_port(port: int):
    """Safely terminates an unresponsive process occupying the port."""
    try:
        cmd = f"(Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue).OwningProcess"
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=5)
        pids = set(int(p.strip()) for p in res.stdout.split() if p.strip().isdigit())
        for pid in pids:
            if pid > 0:
                print(f"[!] Reclaiming port {port} from unresponsive process PID {pid}...")
                subprocess.run(["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue"], timeout=5)
        if pids:
            time.sleep(1.0)
    except Exception as e:
        print(f"[!] Port check note: {e}")

def log_event(message: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] [Startup] {message}\n"
    print(message)
    try:
        with open(BACKEND_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(formatted)
    except Exception:
        pass

def print_log_tail(lines: int = 25):
    if BACKEND_LOG_PATH.exists():
        try:
            content = BACKEND_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            tail = content[-lines:]
            print("\n" + "=" * 60)
            print(" 📋 RECENT BACKEND LOGS:")
            print("=" * 60)
            for line in tail:
                print(f"  {line}")
            print("=" * 60 + "\n")
        except Exception:
            pass

def main():
    print("=" * 60)
    print(" 🚀 STARTING OS AGENT DESKTOP & MOBILE REMOTE")
    print("=" * 60)

    port = CONFIG_PORT
    python_exe = get_python_exe()
    local_ip = get_local_ip()
    desktop_url = f"http://127.0.0.1:{port}"
    remote_url = f"http://{local_ip}:{port}/remote"

    log_event(f"Checking existing backend service on port {port}...")

    # Step 1: Check if already running and healthy
    if is_backend_healthy(port):
        log_event(f"✅ Found existing healthy OS Agent backend on port {port}. Reusing active instance.")
        backend_proc = None
    else:
        # Check if port has an unresponsive process
        clean_unresponsive_port(port)

        log_event(f"[1/3] Launching Python FastAPI Backend...")
        log_event(f"      Executable: {python_exe}")
        log_event(f"      Working Dir: {BACKEND_DIR}")
        log_event(f"      Log File: {BACKEND_LOG_PATH}")

        # Open persistent log file
        log_file = open(BACKEND_LOG_PATH, "a", encoding="utf-8", buffering=1)

        # Launch FastAPI backend with auto-reload
        backend_proc = subprocess.Popen(
            [python_exe, "-m", "uvicorn", "main:app", "--host", CONFIG_HOST, "--port", str(port), "--reload"],
            cwd=str(BACKEND_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=False
        )
        child_processes.append(backend_proc)

        # Step 2: Wait for backend readiness using /api/health/ready
        print("[2/3] Waiting for backend readiness", end="", flush=True)
        ready = False
        for attempt in range(1, 31):
            if backend_proc.poll() is not None:
                log_event(f"\n❌ Backend process crashed immediately with exit code {backend_proc.returncode}!")
                print_log_tail()
                sys.exit(1)

            if is_backend_healthy(port, timeout=1.0):
                ready = True
                break
            time.sleep(0.5)
            print(".", end="", flush=True)

        if not ready:
            log_event(f"\n❌ Backend readiness timeout! Server did not report ready within 15 seconds.")
            print_log_tail()
            if backend_proc and backend_proc.poll() is None:
                backend_proc.terminate()
            sys.exit(1)

        print(" Ready!")

    print("\n" + "=" * 60)
    print(f" ✨ OS Agent Server is Live & Ready!")
    print(f" 🖥️  Desktop App UI:  {desktop_url}")
    print(f" 📱 Phone Remote URL: {remote_url}")
    print(f" 📲  Scan QR code in desktop settings to pair mobile device.")
    print("=" * 60 + "\n")

    # Step 3: Launch Frontend
    print("[3/3] Launching Desktop Application Window...")
    tauri_launched = False
    try:
        res = subprocess.run(["npx", "@tauri-apps/cli", "--version"], cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=5)
        if "tauri-cli" in res.stdout:
            print("      Starting Tauri Desktop Window (tauri dev)...")
            tauri_proc = subprocess.Popen(["npx", "@tauri-apps/cli", "dev"], cwd=str(ROOT_DIR), shell=True)
            child_processes.append(tauri_proc)
            tauri_launched = True
    except Exception as e:
        print(f"      Tauri launch note: {e}")

    if not tauri_launched:
        print("      Opening Desktop Application in App Mode...")
        try:
            edge_proc = subprocess.Popen(["cmd", "/c", "start", "msedge", f"--app={desktop_url}"], shell=False)
        except Exception:
            webbrowser.open(desktop_url)

    print("\n[ℹ️] Running! Press Ctrl+C in this terminal to shut down cleanly.")

    # Supervision loop with bounded recovery
    recovery_attempts = 0
    max_recoveries = 2

    try:
        while True:
            if backend_proc is not None and backend_proc.poll() is not None:
                # Backend process died
                exit_code = backend_proc.returncode
                log_event(f"⚠️ Backend process exited unexpectedly with code {exit_code}.")
                print_log_tail(15)

                if recovery_attempts < max_recoveries:
                    recovery_attempts += 1
                    log_event(f"🔄 Attempting automatic backend recovery ({recovery_attempts}/{max_recoveries})...")
                    clean_unresponsive_port(port)
                    log_file = open(BACKEND_LOG_PATH, "a", encoding="utf-8", buffering=1)
                    backend_proc = subprocess.Popen(
                        [python_exe, "-m", "uvicorn", "main:app", "--host", CONFIG_HOST, "--port", str(port), "--reload"],
                        cwd=str(BACKEND_DIR),
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        shell=False
                    )
                    child_processes.append(backend_proc)

                    # Wait for readiness
                    recovered = False
                    for _ in range(20):
                        if is_backend_healthy(port):
                            recovered = True
                            break
                        time.sleep(0.5)

                    if recovered:
                        log_event("✅ Backend successfully recovered and resumed service!")
                    else:
                        log_event("❌ Backend recovery failed readiness check.")
                        break
                else:
                    log_event("❌ Maximum recovery attempts exceeded. Terminating supervisor.")
                    break

            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n[Shutting down OS Agent services cleanly...]")
    finally:
        cleanup_child_processes()
        print("[Shutdown complete]")

if __name__ == "__main__":
    main()
