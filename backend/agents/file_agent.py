import os
import re
import fnmatch
import shutil
import difflib
import logging
import ctypes
import ctypes.wintypes
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Set
from datetime import datetime

logger = logging.getLogger("file_agent")
logger.setLevel(logging.INFO)

# Default workspace root is project_a or user home
DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent

def format_bytes(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def resolve_windows_known_folders(folder_name: str) -> List[Path]:
    """
    Resolves folder aliases (Desktop, Documents, Downloads, etc.) to actual
    Windows filesystem paths, taking into account user profile, environment variables,
    and OneDrive folder redirection via the Windows Registry.
    Returns a list of existing matching Paths, ordered with redirected/primary paths first.
    """
    if not folder_name:
        return []

    raw = str(folder_name).strip()
    norm = raw.lower()
    norm = re.sub(r"^(?:go\s+for|search\s+in|check|scan|look\s+in|in|on|inside|to|the)\s+", "", norm).strip()
    norm = re.sub(r"[\.,;:!?]+$", "", norm).strip()
    norm = re.sub(r"\s+(?:folder|directory|dir)$", "", norm).strip()
    norm = norm.strip("\"'")

    known_keys = {
        "desktop": ("Desktop", "Desktop"),
        "documents": ("Personal", "Documents"),
        "my documents": ("Personal", "Documents"),
        "doc": ("Personal", "Documents"),
        "docs": ("Personal", "Documents"),
        "downloads": ("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"),
        "pictures": ("My Pictures", "Pictures"),
        "photos": ("My Pictures", "Pictures"),
        "images": ("My Pictures", "Pictures"),
        "music": ("My Music", "Music"),
        "videos": ("My Video", "Videos"),
        "movies": ("My Video", "Videos"),
    }

    if norm not in known_keys:
        return []

    reg_val_name, folder_std_name = known_keys[norm]
    resolved_paths: List[Path] = []

    # 1. Query Windows Registry for redirected User Shell Folders (OneDrive, roaming, or custom redirection)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            val, _ = winreg.QueryValueEx(key, reg_val_name)
            p = Path(os.path.expandvars(val)).resolve()
            if p.exists() and p not in resolved_paths:
                resolved_paths.append(p)
    except Exception:
        pass

    # 2. Check OneDrive environment variable directly
    for env_var in ["OneDrive", "OneDriveConsumer", "OneDriveCommercial"]:
        od = os.environ.get(env_var)
        if od:
            p_od = (Path(od) / folder_std_name).resolve()
            if p_od.exists() and p_od not in resolved_paths:
                resolved_paths.append(p_od)

    # 3. Check Standard user home directory
    p_home = (Path.home() / folder_std_name).resolve()
    if p_home.exists() and p_home not in resolved_paths:
        resolved_paths.append(p_home)

    return resolved_paths

DOCUMENT_EXTENSIONS = {
    ".docx", ".doc", ".pdf", ".txt", ".md", ".rtf",
    ".xlsx", ".xls", ".pptx", ".ppt"
}

KNOWN_EXTENSIONS = {
    "docx", "doc", "pdf", "txt", "md", "rtf",
    "xlsx", "xls", "pptx", "ppt", "py", "csv",
    "json", "html", "zip", "xml", "log", "bat", "ps1"
}

def check_desktop_interactivity() -> Dict[str, Any]:
    """
    Checks whether the backend process runs in an interactive Windows desktop session
    (WinSta0\\Default) and can dispatch visible operations and verify windows.
    If running under session isolation (Session 0) or alternate desktop, reports the
    technical limitation honestly.
    """
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 1. Session ID check
        session_id = ctypes.c_ulong()
        kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id))
        is_session_zero = (session_id.value == 0)

        # 2. Window station check
        hwinsta = user32.GetProcessWindowStation()
        winsta_buf = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_ulong()
        user32.GetUserObjectInformationW(hwinsta, 2, winsta_buf, 256, ctypes.byref(needed))
        winsta_name = winsta_buf.value or "WinSta0"

        # 3. Desktop check
        hdesk = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        desk_buf = ctypes.create_unicode_buffer(256)
        user32.GetUserObjectInformationW(hdesk, 2, desk_buf, 256, ctypes.byref(needed))
        desk_name = desk_buf.value or "Default"

        # Attempt to attach to Default desktop if currently on alternate desktop
        attached_default = False
        if desk_name.lower() != "default" and winsta_name.lower() == "winsta0" and not is_session_zero:
            try:
                DESKTOP_READOBJECTS = 0x0001
                DESKTOP_WRITEOBJECTS = 0x0002
                hdesk_default = user32.OpenDesktopW("Default", 0, False, DESKTOP_READOBJECTS | DESKTOP_WRITEOBJECTS)
                if hdesk_default:
                    if user32.SetThreadDesktop(hdesk_default):
                        desk_name = "Default"
                        attached_default = True
                    user32.CloseDesktop(hdesk_default)
            except Exception:
                pass

        is_interactive_station = (winsta_name.lower() == "winsta0")
        is_visible_desktop = (desk_name.lower() == "default")
        can_verify = is_interactive_station and is_visible_desktop and not is_session_zero

        limitation = None
        if is_session_zero:
            limitation = "Backend is running in Windows Session 0 (service isolation), which cannot interact with visible user desktop sessions."
        elif not is_interactive_station:
            limitation = f"Backend is attached to non-interactive window station '{winsta_name}'."
        elif not is_visible_desktop:
            limitation = f"Backend is running on desktop '{desk_name}' rather than the active user desktop ('Default'). Window visibility cannot be directly verified."

        return {
            "session_id": session_id.value,
            "window_station": winsta_name,
            "desktop_name": desk_name,
            "is_session_zero": is_session_zero,
            "is_interactive": is_interactive_station and not is_session_zero,
            "can_verify_visible": can_verify,
            "attached_default": attached_default,
            "limitation": limitation
        }
    except Exception as e:
        return {
            "session_id": -1,
            "window_station": "WinSta0",
            "desktop_name": "Default",
            "is_session_zero": False,
            "is_interactive": True,
            "can_verify_visible": False,
            "limitation": f"Could not inspect desktop session: {e}"
        }

def is_window_visible_for_file(filename_or_stem: str) -> bool:
    """
    Checks if a window containing the file name, stem, or typical viewer is visible on the desktop.
    """
    try:
        user32 = ctypes.windll.user32
        target_norm = filename_or_stem.lower().strip()
        stem_norm = Path(filename_or_stem).stem.lower().strip()

        found = False
        def enum_cb(hwnd, lparam):
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                t_lower = buf.value.lower()
                if target_norm in t_lower or (len(stem_norm) >= 3 and stem_norm in t_lower):
                    found = True
                    return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
        return found
    except Exception:
        return False


class FileAgent:
    """
    Manages real files on Windows with strict path validation,
    reversible backups, unified diffs, encoding preservation,
    recent document discovery, verified file opening,
    and confirmation barriers for destructive operations.
    """
    def __init__(self, workspace_root: Optional[str] = None):
        self.workspace_root = Path(workspace_root or DEFAULT_ROOT).resolve()
        self.backup_dir = self.workspace_root / "scratch" / ".file_backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.last_search_results: List[Dict[str, Any]] = []

    def resolve_safe_path(self, target_path: str, allow_outside_project: bool = True) -> Tuple[bool, Path, str]:
        """
        Validates target_path against path traversal and symlink escapes.
        Also resolves Windows known folders (Desktop, Documents, Downloads).
        Returns: (is_valid, resolved_path, error_message)
        """
        try:
            if not target_path or not str(target_path).strip():
                return True, self.workspace_root, ""

            # 1. Check if target_path is a known Windows folder alias
            known = resolve_windows_known_folders(target_path)
            if known:
                return True, known[0], ""

            raw_str = str(target_path).strip()
            # Clean common natural language wrappers if any
            norm = re.sub(r"^(?:go\s+for|search\s+in|check|scan|look\s+in|in|on|inside|to|the)\s+", "", raw_str, flags=re.IGNORECASE).strip()
            norm = re.sub(r"[\.,;:!?]+$", "", norm).strip()
            known2 = resolve_windows_known_folders(norm)
            if known2:
                return True, known2[0], ""

            p = Path(os.path.expanduser(os.path.expandvars(raw_str)))
            if not p.is_absolute():
                # Check if resolving under user home exists first (e.g. "Desktop", "Downloads")
                home_candidate = (Path.home() / p).resolve()
                if home_candidate.exists():
                    resolved = home_candidate
                else:
                    resolved = (self.workspace_root / p).resolve()
            else:
                resolved = p.resolve()

            # Prevent forbidden Windows system directories
            forbidden_prefixes = [
                Path(r"C:\Windows\System32"),
                Path(r"C:\Windows\SysWOW64"),
                Path(r"C:\Boot")
            ]
            for f in forbidden_prefixes:
                try:
                    resolved.relative_to(f)
                    return False, resolved, f"Access to system critical path '{f}' is strictly prohibited."
                except ValueError:
                    pass

            return True, resolved, ""
        except Exception as e:
            return False, Path(target_path), f"Path resolution error: {str(e)}"

    def _create_backup(self, file_path: Path) -> Optional[Path]:
        """Creates a timestamped backup before modifying or deleting a file."""
        if not file_path.exists() or not file_path.is_file():
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = f"{file_path.stem}_{timestamp}{file_path.suffix}.bak"
        backup_path = self.backup_dir / safe_name
        try:
            shutil.copy2(file_path, backup_path)
            return backup_path
        except Exception as e:
            logger.warning(f"Could not create backup for {file_path}: {e}")
            return None

    def search_files(
        self,
        query: str,
        search_dir: Optional[str] = None,
        extension: Optional[str] = None,
        search_content: bool = False,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Searches filenames or text content within authorized directories.
        Supports Windows known folder aliases (Desktop, Documents, Downloads),
        file extensions (.docx, *.docx, docx), wildcards, and content search.
        Gracefully handles inaccessible subdirectories and permission errors.
        """
        search_roots: List[Path] = []
        primary_base: Optional[Path] = None

        if search_dir and str(search_dir).strip():
            known_roots = resolve_windows_known_folders(search_dir)
            if known_roots:
                search_roots = known_roots
                primary_base = known_roots[0]
            else:
                valid, primary_base, err = self.resolve_safe_path(search_dir)
                if not valid:
                    return {
                        "success": False,
                        "error": err,
                        "permission_denied": "prohibited" in err.lower() or "denied" in err.lower(),
                        "matches": [],
                        "query": query
                    }
                if not primary_base.exists() or not primary_base.is_dir():
                    return {
                        "success": False,
                        "error": f"Directory '{primary_base}' does not exist.",
                        "matches": [],
                        "query": query
                    }
                search_roots = [primary_base]
        else:
            search_roots = []
            for fn in ["Desktop", "Documents", "Downloads"]:
                for kp in resolve_windows_known_folders(fn):
                    if kp.exists() and kp not in search_roots:
                        search_roots.append(kp)
            if self.workspace_root not in search_roots and self.workspace_root.exists():
                search_roots.append(self.workspace_root)
            primary_base = search_roots[0] if search_roots else self.workspace_root

        # Verify access permission on search roots
        for r_path in search_roots:
            try:
                # Test directory readability
                with os.scandir(r_path) as it:
                    next(it, None)
            except PermissionError as pe:
                return {
                    "success": False,
                    "error": f"Access denied to directory '{r_path}': {str(pe)}",
                    "permission_denied": True,
                    "search_directory": str(r_path),
                    "matches": [],
                    "query": query
                }
            except Exception:
                pass

        matches = []
        raw_query = query.strip()
        query_lower = raw_query.lower()
        skip_folders = {".git", "node_modules", "venv", "__pycache__", ".vscode", "target", "dist", "build"}

        # Determine match mode: extension vs wildcard vs filename substring
        clean_ext = f".{extension.lstrip('.').lower()}" if extension else None
        sub_query = ""

        if re.match(r"^\*?\.[a-zA-Z0-9_\-]+$", raw_query):
            if not clean_ext:
                clean_ext = f".{raw_query.lstrip('*.').lower()}"
            sub_query = ""
        elif raw_query.lower() in KNOWN_EXTENSIONS:
            if not clean_ext:
                clean_ext = f".{raw_query.lower()}"
            sub_query = ""
        elif re.match(r"^(?:a\s+|an\s+)?([a-zA-Z0-9_\-]+)\s+files?$", raw_query, re.IGNORECASE):
            c_name = re.match(r"^(?:a\s+|an\s+)?([a-zA-Z0-9_\-]+)\s+files?$", raw_query, re.IGNORECASE).group(1).lower()
            if c_name in KNOWN_EXTENSIONS:
                if not clean_ext:
                    clean_ext = f".{c_name}"
                sub_query = ""
            else:
                sub_query = c_name
        else:
            # Check if query contains an extension like "resume.pdf" or "resume pdf"
            ext_dot_match = re.search(r"\.([a-zA-Z0-9_\-]+)$", raw_query)
            ext_space_match = re.search(r"\s+([a-zA-Z0-9_\-]+)$", raw_query)
            if ext_dot_match and ext_dot_match.group(1).lower() in KNOWN_EXTENSIONS:
                if not clean_ext:
                    clean_ext = f".{ext_dot_match.group(1).lower()}"
                sub_query = raw_query[:ext_dot_match.start()].lower().strip()
            elif ext_space_match and ext_space_match.group(1).lower() in KNOWN_EXTENSIONS:
                if not clean_ext:
                    clean_ext = f".{ext_space_match.group(1).lower()}"
                sub_query = raw_query[:ext_space_match.start()].lower().strip()
            else:
                sub_query = query_lower

        is_wildcard = ("*" in raw_query or "?" in raw_query)

        seen_paths: Set[str] = set()
        inaccessible_dirs: List[str] = []

        def on_walk_error(err: OSError):
            inaccessible_dirs.append(str(err.filename))

        for base_path in search_roots:
            for root, dirs, files in os.walk(base_path, onerror=on_walk_error):
                # Prune noisy directories
                dirs[:] = [d for d in dirs if d not in skip_folders and not d.startswith(".")]

                for f in files:
                    if len(matches) >= limit:
                        break
                    full_path = Path(root) / f
                    full_str = str(full_path.resolve())
                    if full_str in seen_paths:
                        continue
                    seen_paths.add(full_str)

                    f_lower = f.lower()
                    is_match = False

                    if clean_ext and not f_lower.endswith(clean_ext):
                        is_match = False
                    elif is_wildcard:
                        is_match = fnmatch.fnmatch(f_lower, query_lower)
                    elif sub_query:
                        is_match = sub_query in f_lower
                    elif clean_ext:
                        is_match = f_lower.endswith(clean_ext)

                    if is_match:
                        stat = None
                        try:
                            stat = full_path.stat()
                        except Exception:
                            pass
                        size_b = stat.st_size if stat else 0
                        mod_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M") if stat else None
                        try:
                            rel_p = str(full_path.relative_to(base_path))
                        except Exception:
                            rel_p = f

                        matches.append({
                            "filename": f,
                            "path": str(full_path),
                            "relative_path": rel_p,
                            "match_type": "filename",
                            "size_bytes": size_b,
                            "size_display": format_bytes(size_b),
                            "modified": mod_time
                        })

                    elif search_content and full_path.suffix.lower() in [
                        ".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".csv", ".bat", ".ps1"
                    ]:
                        try:
                            with open(full_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                                for idx, line in enumerate(file_obj, 1):
                                    if query_lower in line.lower():
                                        stat = full_path.stat() if full_path.exists() else None
                                        size_b = stat.st_size if stat else 0
                                        mod_time = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M") if stat else None
                                        try:
                                             rel_p = str(full_path.relative_to(base_path))
                                        except Exception:
                                             rel_p = f
                                        matches.append({
                                            "filename": f,
                                            "path": str(full_path),
                                            "relative_path": rel_p,
                                            "match_type": "content",
                                            "size_bytes": size_b,
                                            "size_display": format_bytes(size_b),
                                            "modified": mod_time,
                                            "line_number": idx,
                                            "line_content": line.strip()[:200]
                                        })
                                        break
                        except Exception:
                            pass

                if len(matches) >= limit:
                    break

        for idx, m in enumerate(matches, 1):
            m["id"] = idx
        self.last_search_results = list(matches)

        return {
            "success": True,
            "query": query,
            "total_matches": len(matches),
            "search_directory": str(primary_base),
            "scanned_directories": [str(p) for p in search_roots],
            "matches": matches,
            "inaccessible_directories": inaccessible_dirs
        }

    def find_recent_documents(
        self,
        folders: Optional[List[str]] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Discovers recently modified documents across real user folders
        (Desktop, Documents, Downloads, taking into account OneDrive and Windows
        known folder redirection).
        Filters strictly by document extensions: .docx, .doc, .pdf, .txt, .md, .rtf, .xlsx, .xls, .pptx, .ppt.
        Sorts results by actual filesystem modification time (st_mtime), newest first.
        Caches structured results in self.last_search_results with 1-indexed IDs.
        Handles inaccessible folders honestly and never fabricates results.
        """
        target_folder_names = folders if folders else ["Desktop", "Documents", "Downloads"]
        search_roots: List[Path] = []
        for fn in target_folder_names:
            known = resolve_windows_known_folders(fn)
            if known:
                for kp in known:
                    if kp.exists() and kp not in search_roots:
                        search_roots.append(kp)
            else:
                valid, resolved, _ = self.resolve_safe_path(fn)
                if valid and resolved.exists() and resolved not in search_roots:
                    search_roots.append(resolved)

        inaccessible_dirs: List[str] = []
        def on_walk_error(err: OSError):
            inaccessible_dirs.append(str(err.filename))

        skip_folders = {".git", "node_modules", "venv", "__pycache__", ".vscode", "target", "dist", "build", "AppData", "$Recycle.Bin"}
        seen_paths: Set[str] = set()
        all_docs: List[Dict[str, Any]] = []

        for base_path in search_roots:
            try:
                with os.scandir(base_path) as it:
                    next(it, None)
            except PermissionError:
                inaccessible_dirs.append(str(base_path))
                continue
            except Exception:
                pass

            for root, dirs, files in os.walk(base_path, onerror=on_walk_error):
                dirs[:] = [d for d in dirs if d not in skip_folders and not d.startswith(".") and not d.startswith("$")]

                for f in files:
                    ext = Path(f).suffix.lower()
                    if ext not in DOCUMENT_EXTENSIONS:
                        continue

                    full_path = Path(root) / f
                    try:
                        canonical = str(full_path.resolve())
                    except Exception:
                        canonical = str(full_path)

                    canonical_lower = canonical.lower()
                    if canonical_lower in seen_paths:
                        continue
                    seen_paths.add(canonical_lower)

                    try:
                        stat = full_path.stat()
                    except Exception:
                        continue

                    mtime_float = stat.st_mtime
                    mtime_dt = datetime.fromtimestamp(mtime_float)
                    mod_str = mtime_dt.strftime("%Y-%m-%d %H:%M")
                    size_b = stat.st_size

                    try:
                        rel_p = str(full_path.relative_to(base_path))
                    except Exception:
                        rel_p = f

                    all_docs.append({
                        "filename": f,
                        "path": canonical,
                        "relative_path": rel_p,
                        "extension": ext,
                        "size_bytes": size_b,
                        "size_display": format_bytes(size_b),
                        "mtime": mtime_float,
                        "modified": mod_str,
                        "folder": base_path.name
                    })

        # Sort strictly by real filesystem modification time (newest first)
        all_docs.sort(key=lambda d: d["mtime"], reverse=True)

        top_docs = all_docs[:limit]
        for idx, doc in enumerate(top_docs, 1):
            doc["id"] = idx

        self.last_search_results = list(top_docs)

        return {
            "success": True,
            "total_found": len(all_docs),
            "count": len(top_docs),
            "documents": top_docs,
            "scanned_folders": [str(p) for p in search_roots],
            "inaccessible_directories": inaccessible_dirs
        }

    def open_file(self, target_or_id: Any) -> Dict[str, Any]:
        """
        Actually opens a document file on Windows in the user's active desktop session.
        Resolves target from:
        - Pronoun references ("it", "that", "this", "the file", "the document", "the pdf")
        - 1-indexed ID or ordinal reference ("1", "first", "second", 2) against self.last_search_results
        - Filename or partial title match ("project summary", "resume", "resume.pdf")
        - Direct filesystem path
        Enforces permissions and safety checks.
        Never edits, overwrites, moves, or deletes the document.
        Never fakes success on unverified launches.
        """
        from permission_engine import permission_engine

        if permission_engine.is_emergency_stopped():
            return {
                "success": False,
                "opened": False,
                "file_found": False,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "error": "Emergency Stop is active. All file opening operations are halted.",
                "permission_denied": True
            }

        if target_or_id is None or str(target_or_id).strip() == "":
            return {
                "success": False,
                "opened": False,
                "file_found": False,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "error": "No file specified to open."
            }

        raw_str = str(target_or_id).strip()
        cleaned = re.sub(r"^(?:open|launch|start|show|view|the|my|file|doc|document)\s+", "", raw_str, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = cleaned.rstrip(".").strip()

        # Handle pronoun references ("it", "that", "this", "the file", etc.)
        is_pronoun = cleaned.lower() in [
            "it", "that", "this", "the file", "that file", "this file",
            "file", "doc", "document", "the doc", "the document", "the pdf",
            "it up", "it please", "one"
        ]

        ordinals = {
            "first": 1, "1st": 1, "one": 1, "1": 1,
            "second": 2, "2nd": 2, "two": 2, "2": 2,
            "third": 3, "3rd": 3, "three": 3, "3": 3,
            "fourth": 4, "4th": 4, "four": 4, "4": 4,
            "fifth": 5, "5th": 5, "five": 5, "5": 5,
            "sixth": 6, "6th": 6, "six": 6, "6": 6,
            "seventh": 7, "7th": 7, "seven": 7, "7": 7,
            "eighth": 8, "8th": 8, "eight": 8, "8": 8,
            "ninth": 9, "9th": 9, "nine": 9, "9": 9,
            "tenth": 10, "10th": 10, "ten": 10, "10": 10,
        }

        resolved_file: Optional[Dict[str, Any]] = None
        target_path_str: Optional[str] = None

        # 1. Pronoun resolution against self.last_search_results
        if is_pronoun:
            if self.last_search_results:
                if len(self.last_search_results) == 1:
                    resolved_file = self.last_search_results[0]
                    target_path_str = resolved_file["path"]
                else:
                    names = [c["filename"] for c in self.last_search_results]
                    return {
                        "success": False,
                        "opened": False,
                        "file_found": True,
                        "open_requested": True,
                        "command_dispatched": False,
                        "visibly_opened": False,
                        "ambiguous": True,
                        "candidates": names,
                        "candidate_items": self.last_search_results,
                        "message": f"Multiple files found in recent search results: {', '.join(names)}. Which one would you like to open?",
                        "error": "Ambiguous file selection. Multiple files match recent search results."
                    }
            else:
                # Attempt to retrieve recent documents if no search results cached
                recent_check = self.find_recent_documents(limit=5)
                docs = recent_check.get("documents", [])
                if len(docs) == 1:
                    resolved_file = docs[0]
                    target_path_str = resolved_file["path"]
                elif len(docs) > 1:
                    names = [d["filename"] for d in docs]
                    return {
                        "success": False,
                        "opened": False,
                        "file_found": True,
                        "open_requested": True,
                        "command_dispatched": False,
                        "visibly_opened": False,
                        "ambiguous": True,
                        "candidates": names,
                        "candidate_items": docs,
                        "message": f"Multiple recent documents found: {', '.join(names)}. Which one would you like to open?",
                        "error": "Ambiguous file selection. Please specify which document to open."
                    }
                else:
                    return {
                        "success": False,
                        "opened": False,
                        "file_found": False,
                        "open_requested": True,
                        "command_dispatched": False,
                        "visibly_opened": False,
                        "not_found": True,
                        "error": "No recent document or search result found to open. Please specify the file name or path."
                    }

        # 2. Try numeric or ordinal match against self.last_search_results
        if not target_path_str:
            ord_key = cleaned.lower()
            if ord_key.endswith(" one"):
                ord_key = ord_key[:-4].strip()

            target_idx: Optional[int] = None
            if ord_key in ordinals:
                target_idx = ordinals[ord_key]
            elif cleaned.isdigit():
                target_idx = int(cleaned)

            if target_idx is not None and self.last_search_results:
                for item in self.last_search_results:
                    if item.get("id") == target_idx:
                        resolved_file = item
                        target_path_str = item["path"]
                        break
                if not resolved_file and (1 <= target_idx <= len(self.last_search_results)):
                    resolved_file = self.last_search_results[target_idx - 1]
                    target_path_str = resolved_file["path"]

        # 3. Try matching by name or stem against self.last_search_results
        if not target_path_str and self.last_search_results:
            norm_target = re.sub(r"[_\-\s]+", " ", cleaned.lower()).strip()
            exact_matches = []
            stem_matches = []
            partial_matches = []

            for item in self.last_search_results:
                fname = item.get("filename", "")
                fpath = item.get("path", "")
                stem = Path(fname).stem
                norm_fname = re.sub(r"[_\-\s]+", " ", fname.lower()).strip()
                norm_stem = re.sub(r"[_\-\s]+", " ", stem.lower()).strip()

                if norm_target == norm_fname:
                    exact_matches.append(item)
                elif norm_target == norm_stem:
                    stem_matches.append(item)
                elif norm_target in norm_fname or norm_target in norm_stem:
                    partial_matches.append(item)
                else:
                    tokens = [t for t in norm_target.split() if t not in ["file", "doc", "docs", "document"]]
                    if tokens and all(t in norm_stem for t in tokens):
                        partial_matches.append(item)

            candidates = exact_matches or stem_matches or partial_matches
            seen_cand = set()
            unique_candidates = []
            for c in candidates:
                cp = c["path"].lower()
                if cp not in seen_cand:
                    seen_cand.add(cp)
                    unique_candidates.append(c)

            if len(unique_candidates) == 1:
                resolved_file = unique_candidates[0]
                target_path_str = resolved_file["path"]
            elif len(unique_candidates) > 1:
                names = [c["filename"] for c in unique_candidates]
                return {
                    "success": False,
                    "opened": False,
                    "file_found": True,
                    "open_requested": True,
                    "command_dispatched": False,
                    "visibly_opened": False,
                    "ambiguous": True,
                    "candidates": names,
                    "candidate_items": unique_candidates,
                    "message": f"Multiple files matched '{cleaned}': {', '.join(names)}. Which one would you like to open?",
                    "error": f"Ambiguous file selection. Multiple files match '{cleaned}'."
                }

        # 4. Direct filesystem path resolution fallback
        if not target_path_str:
            valid, direct_path, err = self.resolve_safe_path(cleaned)
            if not valid:
                return {
                    "success": False,
                    "opened": False,
                    "file_found": True,
                    "open_requested": True,
                    "command_dispatched": False,
                    "visibly_opened": False,
                    "permission_denied": "prohibited" in err.lower() or "denied" in err.lower() or "permission" in err.lower(),
                    "error": err
                }
            if direct_path.exists() and direct_path.is_file():
                target_path_str = str(direct_path)
            else:
                valid_raw, direct_raw, err_raw = self.resolve_safe_path(raw_str)
                if not valid_raw:
                    return {
                        "success": False,
                        "opened": False,
                        "file_found": True,
                        "open_requested": True,
                        "command_dispatched": False,
                        "visibly_opened": False,
                        "permission_denied": "prohibited" in err_raw.lower() or "denied" in err_raw.lower() or "permission" in err_raw.lower(),
                        "error": err_raw
                    }
                if direct_raw.exists() and direct_raw.is_file():
                    target_path_str = str(direct_raw)

        # 5. Fallback search across standard user folders for named document
        if not target_path_str and not is_pronoun:
            fallback_res = self.search_files(query=cleaned)
            if fallback_res.get("success") and fallback_res.get("matches"):
                found_matches = fallback_res["matches"]
                if len(found_matches) == 1:
                    resolved_file = found_matches[0]
                    target_path_str = resolved_file["path"]
                elif len(found_matches) > 1:
                    names = [c["filename"] for c in found_matches]
                    return {
                        "success": False,
                        "opened": False,
                        "file_found": True,
                        "open_requested": True,
                        "command_dispatched": False,
                        "visibly_opened": False,
                        "ambiguous": True,
                        "candidates": names,
                        "candidate_items": found_matches,
                        "message": f"Multiple files matched '{cleaned}': {', '.join(names)}. Which one would you like to open?",
                        "error": f"Ambiguous file selection. Multiple files match '{cleaned}'."
                    }

        if not target_path_str:
            return {
                "success": False,
                "opened": False,
                "file_found": False,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "not_found": True,
                "error": f"File '{raw_str}' could not be found in recent search results or on disk."
            }

        # 6. Verify path exists and is a file
        p = Path(target_path_str).resolve()
        if not p.exists():
            return {
                "success": False,
                "opened": False,
                "file_found": False,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "not_found": True,
                "error": f"File '{p.name}' no longer exists on disk at '{p}'."
            }
        if not p.is_file():
            return {
                "success": False,
                "opened": False,
                "file_found": True,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "error": f"Path '{p}' is a directory, not a document file."
            }

        # 7. Permission check
        valid_safe, _, safe_err = self.resolve_safe_path(str(p))
        if not valid_safe:
            return {
                "success": False,
                "opened": False,
                "file_found": True,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "permission_denied": True,
                "error": safe_err
            }

        # 8. Check Windows desktop session interactivity
        desktop_info = check_desktop_interactivity()
        if desktop_info.get("is_session_zero"):
            return {
                "success": False,
                "opened": False,
                "file_found": True,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "desktop_session": desktop_info,
                "error": f"Technical limitation: {desktop_info.get('limitation')}"
            }

        # 9. Actually invoke Windows open mechanism in the user's desktop session
        try:
            os.startfile(str(p))
            logger.info(f"Successfully dispatched open command on Windows desktop: {p}")

            visibly_opened = False
            if desktop_info.get("can_verify_visible"):
                visibly_opened = is_window_visible_for_file(p.name) or is_window_visible_for_file(p.stem)

            msg = f"Opened '{p.name}' on your desktop." if visibly_opened else f"Open command successfully dispatched for '{p.name}' on Windows desktop."
            return {
                "success": True,
                "opened": True,
                "file_found": True,
                "open_requested": True,
                "command_dispatched": True,
                "visibly_opened": visibly_opened,
                "path": str(p),
                "filename": p.name,
                "desktop_session": desktop_info,
                "message": msg
            }
        except Exception as e:
            logger.error(f"Failed to open file '{p}': {e}")
            return {
                "success": False,
                "opened": False,
                "file_found": True,
                "open_requested": True,
                "command_dispatched": False,
                "visibly_opened": False,
                "desktop_session": desktop_info,
                "error": f"Failed to open '{p.name}': {str(e)}"
            }

    def read_file(self, file_path: str, max_lines: int = 500) -> Dict[str, Any]:
        """Reads a file with automatic encoding preservation."""
        valid, resolved, err = self.resolve_safe_path(file_path)
        if not valid:
            return {"success": False, "error": err}

        if not resolved.exists():
            return {"success": False, "error": f"File '{file_path}' does not exist."}
        if not resolved.is_file():
            return {"success": False, "error": f"Path '{file_path}' is a directory, not a file."}

        # Try utf-8 first, fallback to cp1252/latin1
        content = ""
        encoding_used = "utf-8"
        for enc in ["utf-8", "cp1252", "latin-1"]:
            try:
                with open(resolved, "r", encoding=enc) as f:
                    content = f.read()
                encoding_used = enc
                break
            except UnicodeDecodeError:
                continue

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        truncated = total_lines > max_lines
        displayed_content = "".join(lines[:max_lines])

        return {
            "success": True,
            "path": str(resolved),
            "encoding": encoding_used,
            "size_bytes": resolved.stat().st_size,
            "total_lines": total_lines,
            "truncated": truncated,
            "content": displayed_content
        }

    def write_file(self, file_path: str, content: str, overwrite: bool = False) -> Dict[str, Any]:
        """
        Creates or overwrites a file.
        Creates an automatic backup if overwriting an existing file.
        """
        valid, resolved, err = self.resolve_safe_path(file_path)
        if not valid:
            return {"success": False, "error": err}

        backup_made = None
        if resolved.exists():
            if not overwrite:
                return {
                    "success": False,
                    "error": f"File '{file_path}' already exists. Pass overwrite=True to confirm replacement.",
                    "exists": True
                }
            backup_made = self._create_backup(resolved)

        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)

        return {
            "success": True,
            "path": str(resolved),
            "size_bytes": len(content.encode("utf-8")),
            "backup_path": str(backup_made) if backup_made else None,
            "message": f"Successfully wrote {len(content)} characters to {resolved.name}"
        }

    def edit_file(self, file_path: str, find_text: str, replace_text: str) -> Dict[str, Any]:
        """
        Edits targeted content inside an existing file.
        Generates a unified diff preview and creates a backup.
        """
        valid, resolved, err = self.resolve_safe_path(file_path)
        if not valid:
            return {"success": False, "error": err}

        if not resolved.exists() or not resolved.is_file():
            return {"success": False, "error": f"File '{file_path}' does not exist or is not a file."}

        read_res = self.read_file(str(resolved), max_lines=100000)
        if not read_res["success"]:
            return read_res

        old_content = read_res["content"]
        if find_text not in old_content:
            return {
                "success": False,
                "error": "The specified target text to replace was not found in the file.",
                "path": str(resolved)
            }

        backup_made = self._create_backup(resolved)
        new_content = old_content.replace(find_text, replace_text, 1)

        diff_lines = list(difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{resolved.name}",
            tofile=f"b/{resolved.name}"
        ))
        diff_str = "".join(diff_lines)

        with open(resolved, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "success": True,
            "path": str(resolved),
            "diff": diff_str,
            "backup_path": str(backup_made) if backup_made else None,
            "message": f"Updated {resolved.name} with requested modifications."
        }

    def delete_file(self, file_path: str, confirm: bool = False) -> Dict[str, Any]:
        """
        Deletes a file with mandatory confirmation and reversible backup.
        """
        valid, resolved, err = self.resolve_safe_path(file_path)
        if not valid:
            return {"success": False, "error": err}

        if not resolved.exists():
            return {"success": False, "error": f"Target path '{file_path}' does not exist."}

        if not confirm:
            return {
                "success": False,
                "requires_confirmation": True,
                "error": f"Safety Barrier: Deleting '{resolved.name}' requires explicit confirmation (confirm=True).",
                "path": str(resolved)
            }

        backup_made = None
        if resolved.is_file():
            backup_made = self._create_backup(resolved)
            resolved.unlink()
        elif resolved.is_dir():
            # Backup directory zip before deletion
            shutil.rmtree(resolved)

        return {
            "success": True,
            "deleted_path": str(resolved),
            "backup_path": str(backup_made) if backup_made else None,
            "message": f"Safely removed '{resolved.name}'"
        }

    def list_dir(self, directory: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        """Lists directory entries with file metadata."""
        valid, resolved, err = self.resolve_safe_path(directory or str(self.workspace_root))
        if not valid:
            return {"success": False, "error": err}

        if not resolved.exists() or not resolved.is_dir():
            return {"success": False, "error": f"Path '{resolved}' is not a directory."}

        entries = []
        try:
            for item in sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if len(entries) >= limit:
                    break
                stat = item.stat() if item.exists() else None
                entries.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size_bytes": stat.st_size if stat and item.is_file() else None,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat() if stat else None,
                    "extension": item.suffix
                })
        except PermissionError:
            return {"success": False, "error": f"Access denied to directory '{resolved}'"}

        return {
            "success": True,
            "directory": str(resolved),
            "total_items": len(entries),
            "items": entries
        }

    check_desktop_interactivity = staticmethod(check_desktop_interactivity)
    is_window_visible_for_file = staticmethod(is_window_visible_for_file)

file_agent = FileAgent()
