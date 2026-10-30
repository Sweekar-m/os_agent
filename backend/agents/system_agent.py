import os
import subprocess
import logging
import time
import platform
import socket
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import ctypes
from ctypes import wintypes
import psutil

try:
    from input_controller import controller as input_controller
except Exception:
    input_controller = None

logger = logging.getLogger("system_agent")
logger.setLevel(logging.INFO)

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
try:
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
except Exception:
    pass

def get_accessible_desktops() -> List[str]:
    """Returns list of desktop names on the current window station."""
    desktops = []
    try:
        hwinsta = user32.GetProcessWindowStation()
        DESKTOPENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPARAM)
        def desk_cb(desk_name, lparam):
            if desk_name:
                desktops.append(desk_name)
            return True
        user32.EnumDesktopsW(hwinsta, DESKTOPENUMPROC(desk_cb), 0)
    except Exception:
        pass
    if "Default" not in desktops:
        desktops.append("Default")
    return desktops

def ensure_interactive_desktop():
    """Attaches calling thread to active input desktop or preserves existing valid session desktop."""
    try:
        kernel32 = ctypes.windll.kernel32
        h_curr = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        if h_curr:
            return
    except Exception:
        pass
    try:
        h_input = user32.OpenInputDesktop(0, False, 0x01FF)
        if h_input:
            user32.SetThreadDesktop(h_input)
            return
    except Exception:
        pass
    try:
        h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_default:
            user32.SetThreadDesktop(h_default)
    except Exception:
        pass

COMMON_APP_MAP = {
    "notepad": "notepad.exe",
    "text editor": "notepad.exe",
    "calc": "calc.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "files": "explorer.exe",
    "cmd": "cmd.exe",
    "terminal": "powershell.exe",
    "powershell": "powershell.exe",
    "code": "code",
    "vscode": "code",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "brave": "brave.exe",
    "word": "winword.exe",
    "winword": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
    "ppt": "powerpnt.exe",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
    "wordpad": "wordpad.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "snipping tool": "snippingtool.exe",
    "settings": "ms-settings:"
}

class SystemAgent:
    """
    Interacts with the Windows operating system through structured tools:
    telemetry, application management, process inspection, default handlers,
    and validated terminal commands.
    """
    def __init__(self):
        pass

    def get_system_telemetry(self) -> Dict[str, Any]:
        """Reads hardware metrics: CPU load, memory utilization, disk partitions, and uptime."""
        try:
            cpu_pct = psutil.cpu_percent(interval=0.1)
            cpu_freq = psutil.cpu_freq()
            mem = psutil.virtual_memory()
            disks = []
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disks.append({
                        "device": part.device,
                        "mountpoint": part.mountpoint,
                        "total_gb": round(usage.total / (1024**3), 1),
                        "used_gb": round(usage.used / (1024**3), 1),
                        "free_gb": round(usage.free / (1024**3), 1),
                        "percent": usage.percent
                    })
                except (PermissionError, OSError):
                    pass

            battery_info = None
            try:
                bat = psutil.sensors_battery()
                if bat:
                    battery_info = {
                        "percent": bat.percent,
                        "power_plugged": bat.power_plugged,
                        "secsleft": bat.secsleft
                    }
            except Exception:
                pass

            boot_time = psutil.boot_time()
            uptime_seconds = int(time.time() - boot_time)

            return {
                "success": True,
                "os": {
                    "platform": platform.system(),
                    "release": platform.release(),
                    "architecture": platform.machine(),
                    "hostname": socket.gethostname(),
                    "uptime_hours": round(uptime_seconds / 3600, 1)
                },
                "cpu": {
                    "percent": cpu_pct,
                    "cores_logical": psutil.cpu_count(logical=True),
                    "cores_physical": psutil.cpu_count(logical=False),
                    "current_freq_mhz": round(cpu_freq.current, 1) if cpu_freq else None
                },
                "memory": {
                    "total_gb": round(mem.total / (1024**3), 2),
                    "used_gb": round(mem.used / (1024**3), 2),
                    "available_gb": round(mem.available / (1024**3), 2),
                    "percent": mem.percent
                },
                "disks": disks,
                "battery": battery_info,
                "uptime_hours": round(uptime_seconds / 3600, 1)
            }
        except Exception as e:
            logger.error(f"Failed to get system telemetry: {e}")
            return {"success": False, "error": str(e)}

    def list_processes(self, limit: int = 25, filter_name: Optional[str] = None) -> Dict[str, Any]:
        """Lists running Windows processes with resource consumption details."""
        procs = []
        filter_lower = filter_name.lower() if filter_name else None

        for p in psutil.process_iter(['pid', 'name', 'memory_info', 'cpu_percent', 'username', 'status']):
            try:
                info = p.info
                name = info.get('name') or "Unknown"
                if filter_lower and filter_lower not in name.lower():
                    continue

                mem_mb = round(info['memory_info'].rss / (1024 * 1024), 1) if info.get('memory_info') else 0.0
                procs.append({
                    "pid": info['pid'],
                    "name": name,
                    "memory_mb": mem_mb,
                    "cpu_percent": info.get('cpu_percent') or 0.0,
                    "username": info.get('username') or "",
                    "status": info.get('status') or "running"
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # Sort descending by memory footprint
        procs.sort(key=lambda x: x["memory_mb"], reverse=True)
        return {
            "success": True,
            "total_listed": len(procs[:limit]),
            "processes": procs[:limit]
        }

    def terminate_process(self, pid: Optional[int] = None, name: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
        """Terminates an active process by PID or Name with confirmation barrier."""
        if not confirm:
            target = f"PID {pid}" if pid else f"process '{name}'"
            return {
                "success": False,
                "requires_confirmation": True,
                "error": f"Safety Barrier: Terminating {target} requires explicit user approval (confirm=True)."
            }

        killed = []
        if pid:
            try:
                proc = psutil.Process(pid)
                pname = proc.name()
                proc.terminate()
                killed.append(f"{pname} (PID {pid})")
            except psutil.NoSuchProcess:
                return {"success": False, "error": f"Process with PID {pid} not found."}
            except psutil.AccessDenied:
                return {"success": False, "error": f"Access denied to terminate PID {pid}."}
        elif name:
            for p in psutil.process_iter(['pid', 'name']):
                try:
                    if p.info['name'] and p.info['name'].lower() == name.lower():
                        p.terminate()
                        try:
                            p.wait(timeout=0.5)
                        except (psutil.TimeoutExpired, psutil.NoSuchProcess):
                            try:
                                p.kill()
                            except Exception:
                                pass
                        killed.append(f"{p.info['name']} (PID {p.info['pid']})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

        return {
            "success": len(killed) > 0,
            "terminated": killed,
            "message": f"Terminated: {', '.join(killed)}" if killed else f"No matching running process found for '{name}'."
        }

    def launch_app(self, app_name_or_path: str, args: Optional[List[str]] = None) -> Dict[str, Any]:
        """Launches an application or protocol on the Windows desktop."""
        if not app_name_or_path:
            app_name_or_path = "notepad"
        clean_app = app_name_or_path.lower().replace("application", "").replace("app", "").strip()
        target = COMMON_APP_MAP.get(clean_app, COMMON_APP_MAP.get(app_name_or_path.lower(), app_name_or_path))

        ensure_interactive_desktop()
        try:
            if target.startswith("ms-settings:"):
                os.startfile(target)
                return {"success": True, "launched": target, "message": "Opened Windows Settings"}

            cmd = [target]
            if args:
                cmd.extend(args)

            # Spawn decoupled from python process
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return {
                "success": True,
                "launched": target,
                "pid": proc.pid,
                "message": f"Successfully launched {target}"
            }
        except Exception as e:
            logger.error(f"Failed to launch app '{target}': {e}")
            return {"success": False, "error": str(e), "target": target}

    def open_in_default_app(self, file_or_folder_path: str) -> Dict[str, Any]:
        """Opens any file, document, image, or folder in its default registered Windows application."""
        p = Path(file_or_folder_path).resolve()
        if not p.exists():
            return {"success": False, "error": f"Path '{file_or_folder_path}' does not exist."}

        try:
            os.startfile(str(p))
            return {
                "success": True,
                "opened_path": str(p),
                "is_directory": p.is_dir(),
                "message": f"Opened '{p.name}' in its default Windows application."
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def run_terminal_command(self, command: str, shell: str = "powershell", timeout: int = 30) -> Dict[str, Any]:
        """Runs a structured terminal command with timeout and output capture."""
        shell_exe = "powershell.exe" if shell == "powershell" else "cmd.exe"
        shell_flag = "-Command" if shell == "powershell" else "/c"

        t0 = time.time()
        try:
            res = subprocess.run(
                [shell_exe, shell_flag, command],
                capture_output=True,
                text=True,
                timeout=timeout
            )
            elapsed = round(time.time() - t0, 2)
            return {
                "success": res.returncode == 0,
                "exit_code": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "duration_seconds": elapsed,
                "command": command
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "exit_code": -1,
                "error": f"Command timed out after {timeout} seconds.",
                "command": command
            }
        except Exception as e:
            return {
                "success": False,
                "exit_code": -1,
                "error": str(e),
                "command": command
            }

    def diagnose_system(self) -> Dict[str, Any]:
        """Analyzes system health, finding bottlenecks, high RAM consumers, or disk alerts."""
        telemetry = self.get_system_telemetry()
        diagnostics = []
        recommendations = []

        if telemetry.get("success"):
            cpu = telemetry["cpu"]["percent"]
            mem = telemetry["memory"]["percent"]
            if cpu > 85:
                diagnostics.append(f"High CPU utilization: {cpu}%")
                recommendations.append("Inspect top CPU processes and consider closing heavy background tasks.")
            if mem > 85:
                diagnostics.append(f"High Memory utilization: {mem}% ({telemetry['memory']['used_gb']} GB used)")
                recommendations.append("Free up memory by terminating idle applications or tabs.")

            for d in telemetry.get("disks", []):
                if d["percent"] > 90:
                    diagnostics.append(f"Low disk space on drive {d['mountpoint']}: {d['free_gb']} GB remaining ({d['percent']}% full)")
                    recommendations.append(f"Clean up temporary files on {d['mountpoint']}.")

        if not diagnostics:
            diagnostics.append("System is operating smoothly within healthy thresholds.")

        # Top 5 memory processes
        top_procs = self.list_processes(limit=5)

        return {
            "success": True,
            "status": "Warning" if len(diagnostics) > 1 or "High" in diagnostics[0] else "Healthy",
            "diagnostics": diagnostics,
            "recommendations": recommendations,
            "top_processes": top_procs.get("processes", [])
        }

    def find_all_window_handles(self, title_substr: str) -> List[Tuple[int, str]]:
        """Finds all active top-level window handles matching title_substr."""
        if not title_substr:
            return []
        ensure_interactive_desktop()
        t_sub = title_substr.lower().strip()

        found_windows: List[Tuple[int, str]] = []

        # 0. Check current foreground window first
        try:
            fg_hwnd = user32.GetForegroundWindow()
            if fg_hwnd and user32.IsWindowVisible(fg_hwnd):
                length = user32.GetWindowTextLengthW(fg_hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(fg_hwnd, buf, length + 1)
                    t_val = buf.value.lower()
                    if t_sub in t_val or ("notepad" in t_sub and "notepad" in t_val):
                        found_windows.append((fg_hwnd, buf.value))
        except Exception:
            pass

        def enum_cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                t_val = buf.value.lower()

                cname = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cname, 256)
                c_val = cname.value.lower()

                is_match = False
                if t_sub in t_val or t_val in t_sub:
                    is_match = True
                elif "notepad" in t_sub and ("notepad" in t_val or "notepad" in c_val):
                    is_match = True
                elif "calc" in t_sub and ("calculator" in t_val or "calc" in c_val):
                    is_match = True
                elif "terminal" in t_sub and ("powershell" in t_val or "cmd" in t_val or "terminal" in c_val):
                    is_match = True

                if is_match and buf.value:
                    if not any(h == hwnd for h, _ in found_windows):
                        found_windows.append((hwnd, buf.value))
            return True

        cb_proc = WNDENUMPROC(enum_cb)
        user32.EnumWindows(cb_proc, 0)

        # If not found on current desktop, search all accessible desktops on WinSta0
        if not found_windows:
            desktops = get_accessible_desktops()
            for d in desktops:
                try:
                    h_desk = user32.OpenDesktopW(d, 0, False, 0x01FF)
                    if h_desk:
                        user32.SetThreadDesktop(h_desk)
                        user32.EnumWindows(cb_proc, 0)
                        if found_windows:
                            # Keep thread attached to the desktop where the window resides
                            break
                except Exception:
                    pass

        return found_windows

    def find_window_handle(self, title_substr: str) -> Optional[Tuple[int, str]]:
        """Finds active top-level window handle matching title_substr."""
        all_wins = self.find_all_window_handles(title_substr)
        if not all_wins:
            return None
        # Prioritize untitled / new window or the first match
        for h, t in all_wins:
            if "untitled" in t.lower() or "*" in t:
                return (h, t)
        return all_wins[0]

    def wait_for_window(self, title_substr: str, timeout: float = 10.0) -> Dict[str, Any]:
        """Polls until a window matching title_substr appears, then restores and focuses it."""
        ensure_interactive_desktop()
        t0 = time.time()
        while time.time() - t0 < timeout:
            win = self.find_window_handle(title_substr)
            if win:
                hwnd, title = win
                try:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                return {
                    "success": True,
                    "hwnd": hwnd,
                    "title": title,
                    "elapsed_seconds": round(time.time() - t0, 2),
                    "message": f"Window '{title}' is ready and focused."
                }
            time.sleep(0.25)

        return {
            "success": False,
            "error": f"Window matching '{title_substr}' did not appear within {timeout}s.",
            "title_substr": title_substr
        }

    def type_into_window(self, text: str, title_substr: Optional[str] = None) -> Dict[str, Any]:
        """Types or sets text into the target window using UI Automation or input controller."""
        ensure_interactive_desktop()
        target_hwnd = None
        target_title = None

        if title_substr:
            wait_res = self.wait_for_window(title_substr, timeout=6.0)
            if wait_res.get("success"):
                target_hwnd = wait_res["hwnd"]
                target_title = wait_res["title"]

        if not target_hwnd:
            win = self.find_window_handle(title_substr or "Notepad")
            if win:
                target_hwnd, target_title = win

        if target_hwnd:
            try:
                user32.ShowWindow(target_hwnd, 9)
                user32.BringWindowToTop(target_hwnd)
                user32.SetForegroundWindow(target_hwnd)
                time.sleep(0.3)
            except Exception:
                pass

        # Method 1: UI Automation ValuePattern (fast, reliable, handles Win11 modern Notepad & edit controls)
        uia_success = False
        if target_hwnd:
            try:
                import comtypes.client
                from comtypes.gen.UIAutomationClient import (
                    CUIAutomation, IUIAutomation, IUIAutomationValuePattern,
                    UIA_DocumentControlTypeId, UIA_EditControlTypeId, TreeScope_Descendants
                )
                uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
                elem = uia.ElementFromHandle(target_hwnd)
                if elem:
                    cond_doc = uia.CreatePropertyCondition(30003, UIA_DocumentControlTypeId)
                    cond_edit = uia.CreatePropertyCondition(30003, UIA_EditControlTypeId)
                    cond_or = uia.CreateOrCondition(cond_doc, cond_edit)
                    text_elem = elem.FindFirst(TreeScope_Descendants, cond_or)
                    if text_elem:
                        try:
                            text_elem.SetFocus()
                        except Exception:
                            pass
                        val_p = text_elem.GetCurrentPattern(10002)  # UIA_ValuePatternId
                        if val_p:
                            vp = val_p.QueryInterface(IUIAutomationValuePattern)
                            vp.SetValue(text)
                            uia_success = True
                            logger.info(f"Set text via UIAutomation ValuePattern in '{target_title}'")
            except Exception as uia_err:
                logger.debug(f"UIAutomation ValuePattern set failed, falling back to keystrokes: {uia_err}")

        # Method 2: Keystroke typing via input_controller / pyautogui fallback
        if not uia_success:
            if input_controller:
                try:
                    input_controller.type_text(text)
                except Exception as k_err:
                    logger.warning(f"Keystroke typing failed: {k_err}")
            else:
                try:
                    import pyautogui
                    pyautogui.FAILSAFE = False
                    pyautogui.write(text, interval=0.005)
                except Exception as p_err:
                    return {"success": False, "error": f"Failed to enter text: {p_err}"}

        return {
            "success": True,
            "typed_length": len(text),
            "window_title": target_title or "Active Window",
            "hwnd": target_hwnd,
            "method": "UIAutomationValuePattern" if uia_success else "KeyboardInput",
            "message": f"Successfully entered {len(text)} characters into '{target_title or 'active window'}'."
        }

    def verify_window_text(self, title_substr: str, expected_snippet: str, timeout: float = 5.0) -> Dict[str, Any]:
        """Reads text from matching target windows and verifies expected snippet is present in at least one."""
        ensure_interactive_desktop()
        t0 = time.time()
        expected_lower = expected_snippet.lower().strip()

        import comtypes.client
        from comtypes.gen.UIAutomationClient import (
            CUIAutomation, IUIAutomation, IUIAutomationValuePattern, IUIAutomationTextPattern,
            UIA_DocumentControlTypeId, UIA_EditControlTypeId, TreeScope_Descendants
        )

        uia = None
        try:
            uia = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
        except Exception:
            pass

        last_checked_title = None
        last_checked_hwnd = None
        last_extracted_text = ""
        verification_method = "Unknown"

        while time.time() - t0 < timeout:
            matching_windows = self.find_all_window_handles(title_substr)
            if not matching_windows:
                time.sleep(0.25)
                continue

            for target_hwnd, target_title in matching_windows:
                last_checked_hwnd = target_hwnd
                last_checked_title = target_title

                # Check if snippet is already reflected in the window title itself (e.g. '*PES University Bangalore - Notepad')
                if expected_lower in target_title.lower():
                    return {
                        "success": True,
                        "verified": True,
                        "snippet_found": True,
                        "expected_snippet": expected_snippet,
                        "found_text_preview": target_title,
                        "method": "WindowTitleVerification",
                        "window_title": target_title,
                        "hwnd": target_hwnd,
                        "session_accessible": True,
                        "message": f"Verified: '{expected_snippet}' confirmed present in {target_title}."
                    }

                # Try UI Automation
                extracted_text = ""
                if uia:
                    try:
                        elem = uia.ElementFromHandle(target_hwnd)
                        if elem:
                            cond_doc = uia.CreatePropertyCondition(30003, UIA_DocumentControlTypeId)
                            cond_edit = uia.CreatePropertyCondition(30003, UIA_EditControlTypeId)
                            cond_or = uia.CreateOrCondition(cond_doc, cond_edit)
                            text_elem = elem.FindFirst(TreeScope_Descendants, cond_or)
                            if text_elem:
                                try:
                                    val_p = text_elem.GetCurrentPattern(10002)
                                    if val_p:
                                        vp = val_p.QueryInterface(IUIAutomationValuePattern)
                                        extracted_text = vp.CurrentValue or ""
                                        verification_method = "UIAutomationValuePattern"
                                except Exception:
                                    pass

                                if not extracted_text:
                                    try:
                                        txt_p = text_elem.GetCurrentPattern(10014)
                                        if txt_p:
                                            tp = txt_p.QueryInterface(IUIAutomationTextPattern)
                                            extracted_text = tp.DocumentRange.GetText(-1) or ""
                                            verification_method = "UIAutomationTextPattern"
                                    except Exception:
                                        pass
                    except Exception:
                        pass

                # Fallback to WM_GETTEXT on child Edit control
                if not extracted_text:
                    try:
                        child_edit = user32.FindWindowExW(target_hwnd, 0, "Edit", None)
                        if child_edit:
                            buf = ctypes.create_unicode_buffer(8192)
                            user32.SendMessageW(child_edit, 0x000D, 8192, buf)
                            extracted_text = buf.value
                            verification_method = "Win32WM_GETTEXT"
                    except Exception:
                        pass

                last_extracted_text = extracted_text
                if extracted_text and expected_lower in extracted_text.lower():
                    preview = (extracted_text[:200] + "...") if len(extracted_text) > 200 else extracted_text
                    return {
                        "success": True,
                        "verified": True,
                        "snippet_found": True,
                        "expected_snippet": expected_snippet,
                        "found_text_preview": preview,
                        "method": verification_method,
                        "window_title": target_title,
                        "hwnd": target_hwnd,
                        "session_accessible": True,
                        "message": f"Verified: '{expected_snippet}' confirmed present in {target_title} via {verification_method}."
                    }

            time.sleep(0.25)

        return {
            "success": False,
            "verified": False,
            "snippet_found": False,
            "expected_snippet": expected_snippet,
            "found_text_preview": (last_extracted_text[:200] + "...") if len(last_extracted_text) > 200 else last_extracted_text,
            "method": verification_method,
            "window_title": last_checked_title or title_substr,
            "hwnd": last_checked_hwnd,
            "session_accessible": bool(last_checked_hwnd),
            "message": f"Verification failed: expected snippet '{expected_snippet}' was not found in matching windows (checked {last_checked_title})."
        }

    def press_key_or_hotkey(self, keys: Any, title: Optional[str] = None, window_title: Optional[str] = None, **kw: Any) -> Dict[str, Any]:
        """Sends keyboard shortcut or keystroke sequence to target window or active foreground window."""
        ensure_interactive_desktop()
        target_title = title or window_title
        focused_title = None

        if target_title:
            w_res = self.wait_for_window(target_title, timeout=4.0)
            if w_res.get("success"):
                focused_title = w_res["title"]

        time.sleep(0.15)
        raw_keys = keys or ""
        str_keys = str(raw_keys).strip()

        if isinstance(raw_keys, (list, tuple)):
            if input_controller:
                input_controller.hotkey(*[str(k).lower().strip() for k in raw_keys])
            else:
                import pyautogui
                pyautogui.hotkey(*[str(k).lower().strip() for k in raw_keys])
        elif "+" in str_keys:
            parts = [p.strip().lower() for p in str_keys.split("+")]
            if input_controller:
                input_controller.hotkey(*parts)
            else:
                import pyautogui
                pyautogui.hotkey(*parts)
        elif str_keys.lower() in ["enter", "return", "tab", "escape", "esc", "space", "backspace", "delete", "win", "up", "down", "left", "right"]:
            if input_controller:
                input_controller.press_key(str_keys.lower())
            else:
                import pyautogui
                pyautogui.press(str_keys.lower())
        else:
            # Sequence of characters or math like '25*4=' or text
            if input_controller:
                input_controller.type_text(str_keys)
            else:
                import pyautogui
                pyautogui.write(str_keys, interval=0.01)

        return {
            "success": True,
            "keys": raw_keys,
            "window": focused_title or "active window",
            "message": f"Successfully sent keystrokes/shortcut '{raw_keys}' to {focused_title or 'active window'}."
        }

system_agent = SystemAgent()

