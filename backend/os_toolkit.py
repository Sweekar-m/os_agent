import os
import sys
import shutil
import platform
import subprocess
import socket
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import psutil
import pyperclip

# Excluded system directories for fast scanning
EXCLUDE_DIRS = {
    "$recycle.bin", "system volume information", "windows", "program files",
    "program files (x86)", "appdata", "node_modules", ".git", "venv",
    ".cache", "__pycache__", "site-packages", "recovery"
}

def format_bytes(bytes_num: float) -> str:
    """Formats bytes into human readable size string"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"

class OSToolkit:
    """
    Comprehensive, zero-hallucination OS Toolkit for Windows Desktop Agent.
    Provides direct system introspection, process control, file operations,
    shell execution, and hardware telemetry.
    """

    @staticmethod
    def get_system_info() -> Dict[str, Any]:
        """Gathers real-time OS, hardware, memory, disk, and battery telemetry"""
        # CPU
        cpu_pct = psutil.cpu_percent(interval=0.1)
        cpu_count_logical = psutil.cpu_count(logical=True)
        cpu_count_physical = psutil.cpu_count(logical=False)
        cpu_freq = psutil.cpu_freq()
        cpu_freq_mhz = round(cpu_freq.current, 0) if cpu_freq else None

        # Virtual Memory
        mem = psutil.virtual_memory()
        mem_total_gb = round(mem.total / (1024 ** 3), 2)
        mem_used_gb = round(mem.used / (1024 ** 3), 2)
        mem_avail_gb = round(mem.available / (1024 ** 3), 2)
        mem_pct = mem.percent

        # Disks
        drives_info = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or part.fstype == "":
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                drives_info.append({
                    "device": part.device,
                    "mountpoint": part.mountpoint,
                    "fstype": part.fstype,
                    "total_gb": round(usage.total / (1024 ** 3), 1),
                    "used_gb": round(usage.used / (1024 ** 3), 1),
                    "free_gb": round(usage.free / (1024 ** 3), 1),
                    "percent_used": usage.percent
                })
            except Exception:
                pass

        # Battery
        battery = psutil.sensors_battery()
        battery_info = None
        if battery:
            battery_info = {
                "percent": battery.percent,
                "power_plugged": battery.power_plugged,
                "secs_left": battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else None
            }

        # Network IP
        hostname = socket.gethostname()
        local_ip = "127.0.0.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        # Boot time
        boot_ts = psutil.boot_time()
        uptime_sec = datetime.datetime.now().timestamp() - boot_ts
        uptime_str = str(datetime.timedelta(seconds=int(uptime_sec)))

        return {
            "os": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor()
            },
            "cpu": {
                "percent": cpu_pct,
                "logical_cores": cpu_count_logical,
                "physical_cores": cpu_count_physical,
                "freq_mhz": cpu_freq_mhz
            },
            "memory": {
                "total_gb": mem_total_gb,
                "used_gb": mem_used_gb,
                "available_gb": mem_avail_gb,
                "percent": mem_pct
            },
            "disks": drives_info,
            "battery": battery_info,
            "network": {
                "hostname": hostname,
                "local_ip": local_ip
            },
            "uptime": uptime_str
        }

    @staticmethod
    def get_running_processes(limit: int = 15, filter_name: Optional[str] = None, sort_by: str = "memory") -> List[Dict[str, Any]]:
        """Lists active running processes with real PID, memory, and status"""
        procs = []
        for p in psutil.process_iter(['pid', 'name', 'memory_info', 'status', 'create_time']):
            try:
                name = p.info.get('name') or ""
                if not name:
                    continue
                if filter_name and filter_name.lower() not in name.lower():
                    continue

                mem_bytes = p.info['memory_info'].rss if p.info.get('memory_info') else 0
                mem_mb = round(mem_bytes / (1024 * 1024), 1)

                procs.append({
                    "pid": p.info['pid'],
                    "name": name,
                    "memory_mb": mem_mb,
                    "status": p.info.get('status', 'running')
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        if sort_by == "memory":
            procs.sort(key=lambda x: x["memory_mb"], reverse=True)
        else:
            procs.sort(key=lambda x: x["name"].lower())

        return procs[:limit]

    @staticmethod
    def kill_process(pid: Optional[int] = None, name: Optional[str] = None) -> Dict[str, Any]:
        """Terminates a process by PID or image name"""
        killed = []
        errors = []

        if pid:
            try:
                p = psutil.Process(pid)
                p_name = p.name()
                p.terminate()
                killed.append(f"{p_name} (PID: {pid})")
            except Exception as e:
                errors.append(f"PID {pid}: {str(e)}")

        elif name:
            name_lower = name.lower()
            if not name_lower.endswith(".exe"):
                name_cand = f"{name_lower}.exe"
            else:
                name_cand = name_lower

            for p in psutil.process_iter(['pid', 'name']):
                try:
                    pname = p.info.get('name', '').lower()
                    if pname == name_cand or name_lower in pname:
                        p.terminate()
                        killed.append(f"{p.info['name']} (PID: {p.info['pid']})")
                except Exception as e:
                    errors.append(f"{p.info.get('name', '')}: {str(e)}")

        return {
            "success": len(killed) > 0,
            "killed_count": len(killed),
            "killed_processes": killed,
            "errors": errors
        }

    @staticmethod
    def read_file(file_path: str, max_lines: int = 250, offset: int = 0) -> Dict[str, Any]:
        """Safely reads content from any text/code file on disk"""
        p = Path(file_path).resolve()
        if not p.exists():
            return {"error": f"File does not exist: {file_path}", "exists": False}
        if p.is_dir():
            return {"error": f"Path is a directory, not a file: {file_path}", "is_dir": True}

        # Check file size (guard against reading 500MB binary files)
        stat = p.stat()
        size_bytes = stat.st_size
        if size_bytes > 15 * 1024 * 1024:
            return {
                "error": f"File is too large ({format_bytes(size_bytes)}) to display in chat. Maximum supported size is 15MB.",
                "size_bytes": size_bytes,
                "size_display": format_bytes(size_bytes)
            }

        # Attempt decoding with utf-8, fallback to latin-1
        lines = []
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except Exception as e:
            try:
                with open(p, "r", encoding="latin-1", errors="replace") as f:
                    all_lines = f.readlines()
            except Exception as ex2:
                return {"error": f"Could not read file: {ex2}"}

        total_lines = len(all_lines)
        selected_lines = all_lines[offset: offset + max_lines]
        content_preview = "".join(selected_lines)

        return {
            "file_name": p.name,
            "file_path": str(p),
            "size_bytes": size_bytes,
            "size_display": format_bytes(size_bytes),
            "total_lines": total_lines,
            "offset": offset,
            "returned_lines": len(selected_lines),
            "is_truncated": (offset + max_lines) < total_lines,
            "content": content_preview
        }

    @staticmethod
    def write_file(file_path: str, content: str, overwrite: bool = True) -> Dict[str, Any]:
        """Creates or updates a file on disk with full parent directory creation"""
        p = Path(file_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        if p.exists() and not overwrite:
            return {"error": f"File already exists and overwrite=False: {file_path}", "exists": True}

        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(content)
            return {
                "success": True,
                "file_name": p.name,
                "file_path": str(p),
                "bytes_written": len(content.encode("utf-8")),
                "lines_written": len(content.splitlines()),
                "message": f"Successfully written to {p.name}"
            }
        except Exception as e:
            return {"error": f"Failed to write file: {str(e)}"}

    @staticmethod
    def append_file(file_path: str, content: str) -> Dict[str, Any]:
        """Appends content to a file on disk"""
        p = Path(file_path).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            return {
                "success": True,
                "file_name": p.name,
                "file_path": str(p),
                "bytes_appended": len(content.encode("utf-8"))
            }
        except Exception as e:
            return {"error": f"Failed to append to file: {str(e)}"}

    @staticmethod
    def list_directory(dir_path: str = ".", limit: int = 40) -> Dict[str, Any]:
        """Lists files and folders inside a directory with sizes and timestamps"""
        p = Path(dir_path).resolve()
        if not p.exists():
            return {"error": f"Directory does not exist: {dir_path}", "exists": False}
        if not p.is_dir():
            return {"error": f"Path is not a directory: {dir_path}", "is_dir": False}

        items = []
        try:
            for entry in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    st = entry.stat()
                    is_dir = entry.is_dir()
                    mod_dt = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                    items.append({
                        "name": entry.name,
                        "path": str(entry),
                        "is_dir": is_dir,
                        "size_display": "<DIR>" if is_dir else format_bytes(st.st_size),
                        "modified": mod_dt
                    })
                except Exception:
                    continue
        except Exception as e:
            return {"error": f"Could not list directory: {str(e)}"}

        return {
            "directory": str(p),
            "total_items": len(items),
            "items": items[:limit],
            "is_truncated": len(items) > limit
        }

    @staticmethod
    def find_files(query: str, drive: Optional[str] = None, extension: Optional[str] = None, limit: int = 15) -> List[Dict[str, Any]]:
        """Fast file search across user folders and system drives"""
        found = []
        q_lower = query.lower().strip()
        ext_lower = f".{extension.lower().lstrip('.')}" if extension else None

        search_roots = []
        if drive:
            search_roots.append(Path(drive))
        else:
            # Prioritize standard user directories for sub-second responses
            search_roots = [
                Path.home() / "Documents",
                Path.home() / "Desktop",
                Path.home() / "Downloads",
                Path("d:/project_a"),
                Path("D:/"),
                Path("C:/Users")
            ]

        for sdir in search_roots:
            if not sdir.exists():
                continue
            try:
                for root, dirs, files in os.walk(sdir):
                    dirs[:] = [d for d in dirs if d.lower() not in EXCLUDE_DIRS and not d.startswith(".")]
                    for fname in files:
                        fname_lower = fname.lower()
                        if ext_lower and not fname_lower.endswith(ext_lower):
                            continue
                        if q_lower in fname_lower:
                            full_p = os.path.join(root, fname)
                            try:
                                st = os.stat(full_p)
                                found.append({
                                    "name": fname,
                                    "path": full_p,
                                    "size_bytes": st.st_size,
                                    "size_display": format_bytes(st.st_size),
                                    "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                                })
                            except Exception:
                                found.append({"name": fname, "path": full_p})

                            if len(found) >= limit:
                                return found
            except Exception:
                pass

        return found

    @staticmethod
    def run_command(command: str, shell_type: str = "powershell", timeout: int = 25) -> Dict[str, Any]:
        """Runs a command via PowerShell or CMD and returns stdout, stderr, and exit code"""
        if shell_type.lower() in ["powershell", "ps"]:
            args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command]
        else:
            args = ["cmd.exe", "/c", command]

        try:
            res = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace"
            )
            return {
                "command": command,
                "shell": shell_type,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "success": res.returncode == 0
            }
        except subprocess.TimeoutExpired:
            return {
                "command": command,
                "shell": shell_type,
                "exit_code": -1,
                "error": f"Command timed out after {timeout} seconds",
                "success": False
            }
        except Exception as e:
            return {
                "command": command,
                "shell": shell_type,
                "exit_code": -1,
                "error": str(e),
                "success": False
            }

    @staticmethod
    def get_clipboard() -> Dict[str, Any]:
        """Gets the current text content from the Windows clipboard"""
        try:
            text = pyperclip.paste()
            return {"clipboard_text": text, "length": len(text) if text else 0}
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def set_clipboard(text: str) -> Dict[str, Any]:
        """Copies text to the Windows clipboard"""
        try:
            pyperclip.copy(text)
            return {"success": True, "copied_length": len(text)}
        except Exception as e:
            return {"error": str(e)}

    @staticmethod
    def open_app_or_file(target: str) -> Dict[str, Any]:
        """Launches an application, opens a file in default app, or opens a URL"""
        app_map = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "chrome": "start chrome",
            "google chrome": "start chrome",
            "browser": "start chrome",
            "cmd": "start cmd.exe",
            "terminal": "start wt.exe",
            "powershell": "start powershell.exe",
            "explorer": "explorer.exe",
            "file explorer": "explorer.exe",
            "settings": "start ms-settings:",
            "paint": "mspaint.exe",
            "task manager": "taskmgr.exe",
            "taskmgr": "taskmgr.exe"
        }

        target_clean = target.strip()
        cmd = app_map.get(target_clean.lower())
        try:
            if cmd:
                subprocess.Popen(cmd, shell=True)
                return {"success": True, "target": target_clean, "status": "launched"}
            elif Path(target_clean).exists():
                os.startfile(target_clean)
                return {"success": True, "target": target_clean, "status": "opened"}
            elif target_clean.startswith("http://") or target_clean.startswith("https://"):
                os.startfile(target_clean)
                return {"success": True, "target": target_clean, "status": "opened_url"}
            else:
                which_exe = shutil.which(target_clean) or shutil.which(f"{target_clean}.exe")
                if which_exe:
                    subprocess.Popen([which_exe])
                    return {"success": True, "target": target_clean, "status": "launched", "path": which_exe}
                return {"success": False, "target": target_clean, "error": f"Application or file '{target_clean}' does not exist."}
        except Exception as e:
            return {"success": False, "target": target_clean, "error": str(e)}

os_toolkit = OSToolkit()
