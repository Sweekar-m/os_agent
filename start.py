import os
import sys
import time
import socket
import subprocess
import webbrowser
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
VENV_PYTHON = ROOT_DIR / "backend" / "venv" / "Scripts" / "python.exe"

def get_python_exe():
    if VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    if Path("C:/Python314/python.exe").exists():
        return "C:/Python314/python.exe"
    return "py"

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

def kill_port(port: int):
    try:
        cmd = f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
        subprocess.run(["powershell", "-Command", cmd], capture_output=True, timeout=5)
        time.sleep(1)
    except Exception:
        pass

def main():
    print("=" * 60)
    print(" 🚀 STARTING OS AGENT DESKTOP & MOBILE REMOTE")
    print("=" * 60)

    kill_port(8000)

    python_exe = get_python_exe()
    backend_dir = ROOT_DIR / "backend"
    local_ip = get_local_ip()
    port = 8000
    desktop_url = f"http://localhost:{port}"
    remote_url = f"http://{local_ip}:{port}/remote"

    print(f"\n[1/3] Launching Python FastAPI Backend...")
    print(f"      Executable: {python_exe}")
    print(f"      Working Dir: {backend_dir}")

    # Launch FastAPI backend with reload enabled
    backend_proc = subprocess.Popen(
        [python_exe, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(port), "--reload"],
        cwd=str(backend_dir),
        shell=False
    )

    # Wait for backend to come alive
    print("[2/3] Waiting for backend to initialize...", end="", flush=True)
    import urllib.request
    connected = False
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{desktop_url}/api/status", timeout=1) as resp:
                if resp.status == 200:
                    connected = True
                    break
        except Exception:
            pass
        time.sleep(0.5)
        print(".", end="", flush=True)
    print(" Done!")

    print("\n" + "=" * 60)
    print(f" ✨ OS Agent Server is Live!")
    print(f" 🖥️  Desktop App UI:  {desktop_url}")
    print(f" 📱 Phone Remote URL: {remote_url}")
    print(f" 📲  Scan the QR code in the desktop app with your phone!")
    print("=" * 60 + "\n")

    # Step 3: Launch Frontend
    print("[3/3] Launching Desktop Application Window...")
    
    tauri_launched = False
    try:
        res = subprocess.run(["npx", "@tauri-apps/cli", "--version"], cwd=str(ROOT_DIR), capture_output=True, text=True, timeout=5)
        if "tauri-cli" in res.stdout:
            print("      Starting Tauri Desktop Window (tauri dev)...")
            tauri_proc = subprocess.Popen(["npx", "@tauri-apps/cli", "dev"], cwd=str(ROOT_DIR), shell=True)
            tauri_launched = True
    except Exception as e:
        print(f"      Tauri launch note: {e}")

    if not tauri_launched:
        print("      Opening Desktop Application in App Mode...")
        try:
            subprocess.Popen(["cmd", "/c", "start", "msedge", f"--app={desktop_url}"], shell=False)
        except Exception:
            webbrowser.open(desktop_url)

    print("\n[ℹ️] Running! Press Ctrl+C in this terminal to shut down.")
    try:
        backend_proc.wait()
    except KeyboardInterrupt:
        print("\n[Shutting down OS Agent...]")
        backend_proc.terminate()

if __name__ == "__main__":
    main()
