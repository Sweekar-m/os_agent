import os
import time
import datetime
import string
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import pptx
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE

try:
    import win32com.client
    import pythoncom
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False

EXCLUDE_DIRS = {
    'windows', 'program files', 'program files (x86)', 'programdata',
    '$recycle.bin', 'recycle.bin', 'system volume information', 'appdata', '.git',
    'node_modules', 'venv', '.venv', '__pycache__', '.cache', 'site-packages',
    'microsoft', 'windowsapps', '$windows.~bt', '$windows.~ws', 'recovery',
    'boot', '$sysreset', 'perflogs', '.vscode', '.gemini', 'dist', 'build'
}

def format_file_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def format_relative_time(mtime: float) -> str:
    dt = datetime.datetime.fromtimestamp(mtime)
    now = datetime.datetime.now()
    diff = now - dt

    if diff.days == 0:
        return f"Today, {dt.strftime('%I:%M %p')}"
    elif diff.days == 1:
        return f"Yesterday, {dt.strftime('%I:%M %p')}"
    elif diff.days < 7:
        return f"{diff.days} days ago ({dt.strftime('%b %d')})"
    else:
        return dt.strftime('%b %d, %Y')

class PptAgent:
    def __init__(self):
        self.active_file: Optional[str] = None
        self.recent_files: List[Dict[str, Any]] = []
        self._last_scan_time: float = 0.0

    def get_available_drives(self) -> List[str]:
        r"""Detects all mounted drive letters on the system (C:\, D:\, etc.)"""
        drives = []
        for letter in string.ascii_uppercase:
            drive_root = f"{letter}:\\"
            if os.path.exists(drive_root):
                drives.append(drive_root)
        return drives

    def _get_priority_directories(self) -> List[Path]:
        """Returns high-value user directories where presentations are most likely located"""
        dirs = []
        home = Path.home()
        candidates = [
            home / "Documents",
            home / "Desktop",
            home / "Downloads",
            home / "OneDrive",
            home / "OneDrive" / "Documents",
            home / "OneDrive" / "Desktop",
            home / "OneDrive" / "Downloads",
            Path("D:/"),
            Path("D:/PROJECT"),
            Path("D:/project_a"),
            Path("C:/")
        ]
        seen = set()
        for c in candidates:
            try:
                if c.exists():
                    resolved = str(c.resolve()).lower()
                    if resolved not in seen:
                        seen.add(resolved)
                        dirs.append(c)
            except Exception:
                pass
        return dirs

    def find_recent_ppts(
        self,
        query: Optional[str] = None,
        drive: Optional[str] = None,
        limit: int = 25,
        force_refresh: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Scans both C: and D: (and any other mounted drives) for PowerPoint files.
        Prioritizes user document/project directories first for instant results,
        then thoroughly scans drives in parallel with intelligent pruning.
        """
        now = time.time()
        # Return cached results if scanned less than 10 seconds ago and no query/drive filter
        if not force_refresh and not query and not drive and self.recent_files and (now - self._last_scan_time < 10):
            return self.recent_files[:limit]

        drives_to_scan = [drive] if drive and os.path.exists(drive) else self.get_available_drives()
        found_files = []
        seen_paths = set()
        query_lower = query.lower().strip() if query else None

        def check_and_add_file(p_str: str, f_name: str):
            p_lower = p_str.lower()
            if p_lower in seen_paths:
                return
            if f_name.startswith('~$') or not f_name.lower().endswith(('.pptx', '.ppt')):
                return
            if query_lower and (query_lower not in f_name.lower() and query_lower not in p_lower):
                return
            seen_paths.add(p_lower)
            try:
                st = os.stat(p_str)
                # Ignore zero-byte or temporary files
                if st.st_size > 0:
                    found_files.append({
                        "name": f_name,
                        "path": p_str,
                        "mtime": st.st_mtime,
                        "size_bytes": st.st_size,
                        "size_display": format_file_size(st.st_size),
                        "modified_display": format_relative_time(st.st_mtime),
                        "drive": p_str[:2].upper()
                    })
            except Exception:
                pass

        # Phase 1: High priority user folders for instant discovery
        priority_dirs = self._get_priority_directories()
        for p_dir in priority_dirs:
            # Check if this dir matches requested drive filter
            if drive and not str(p_dir).lower().startswith(drive.lower()):
                continue
            try:
                # Walk priority dirs up to 5 levels deep
                base_depth = len(p_dir.parts)
                for root, dirs, files in os.walk(p_dir, topdown=True):
                    curr_depth = len(Path(root).parts)
                    if curr_depth - base_depth > 5:
                        dirs.clear()
                        continue
                    dirs[:] = [
                        d for d in dirs
                        if d.lower() not in EXCLUDE_DIRS
                        and not d.startswith('.')
                        and not d.startswith('$')
                        and 'recycle' not in d.lower()
                    ]
                    for f in files:
                        check_and_add_file(os.path.join(root, f), f)
            except Exception:
                pass

        # Phase 2: Full drive scan in parallel for anything else
        def scan_drive_root(d_root: str):
            t_start = time.time()
            try:
                for root, dirs, files in os.walk(d_root, topdown=True):
                    # Timeout guard: don't spend more than 3 seconds on deep scans of a single drive
                    if time.time() - t_start > 3.0:
                        break

                    dirs[:] = [
                        d for d in dirs
                        if d.lower() not in EXCLUDE_DIRS
                        and not d.startswith('.')
                        and not d.startswith('$')
                        and 'recycle' not in d.lower()
                    ]

                    root_lower = root.lower()
                    if any(ex in root_lower for ex in ('$recycle.bin', 'recycle.bin', '\\appdata\\', '/appdata/', '\\windows\\', '/windows/')):
                        continue

                    for f in files:
                        check_and_add_file(os.path.join(root, f), f)
            except Exception as e:
                print(f"[PptAgent] Scan error on {d_root}: {e}")

        with ThreadPoolExecutor(max_workers=max(1, len(drives_to_scan))) as executor:
            list(executor.map(scan_drive_root, drives_to_scan))

        # Sort by mtime descending (most recently modified first)
        found_files.sort(key=lambda x: x["mtime"], reverse=True)
        top_files = found_files[:limit]

        # Enrich top files with slide count
        for item in top_files:
            try:
                if item["path"].lower().endswith('.pptx'):
                    prs = pptx.Presentation(item["path"])
                    item["slide_count"] = len(prs.slides)
                else:
                    item["slide_count"] = None
            except Exception:
                item["slide_count"] = None

        if not query and not drive:
            self.recent_files = top_files
            self._last_scan_time = now

        return top_files

    def inspect_ppt(self, file_path: str) -> Dict[str, Any]:
        """Inspects presentation slides, titles, preview text, and total slides"""
        p = Path(file_path)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        prs = pptx.Presentation(file_path)
        slides_info = []

        for idx, slide in enumerate(prs.slides, start=1):
            title = ""
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        txt = paragraph.text.strip()
                        if txt:
                            texts.append(txt)
            if slide.shapes.title and slide.shapes.title.text:
                title = slide.shapes.title.text.strip()
            elif texts:
                title = texts[0]

            slides_info.append({
                "slide_num": idx,
                "title": title or f"Slide {idx}",
                "preview_text": " | ".join(texts[:3]) if texts else "Empty slide"
            })

        self.active_file = str(p.resolve())
        return {
            "name": p.name,
            "path": str(p.resolve()),
            "total_slides": len(prs.slides),
            "slides": slides_info
        }

    # --------------------------------------------------------------------------
    # COM Automation Helpers (Preserves 100% Native Design)
    # --------------------------------------------------------------------------
    def _edit_via_com_replace(
        self,
        file_path: str,
        find_text: str,
        replace_text: str,
        slide_num: Optional[int] = None,
        case_sensitive: bool = False
    ) -> Optional[int]:
        """Uses PowerPoint's native COM engine to replace text without changing any design"""
        if not HAS_WIN32COM:
            return None

        pythoncom.CoInitialize()
        ppt_app = None
        pres = None
        was_already_open = False
        norm_path = os.path.abspath(file_path).lower()

        try:
            # Check if PowerPoint is currently open
            try:
                ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
                for p in ppt_app.Presentations:
                    try:
                        if os.path.abspath(p.FullName).lower() == norm_path:
                            pres = p
                            was_already_open = True
                            break
                    except Exception:
                        pass
            except Exception:
                pass

            if pres is None:
                ppt_app = win32com.client.Dispatch("PowerPoint.Application")
                pres = ppt_app.Presentations.Open(os.path.abspath(file_path), False, False, False)

            replaced_count = 0
            target_slides = [pres.Slides(slide_num)] if (slide_num and 1 <= slide_num <= pres.Slides.Count) else list(pres.Slides)

            def replace_in_shape(shape):
                count = 0
                # Direct text frame
                if shape.HasTextFrame:
                    try:
                        tr = shape.TextFrame.TextRange
                        while True:
                            found = tr.Replace(find_text, replace_text, 0, case_sensitive, False)
                            if found is not None:
                                count += 1
                            else:
                                break
                    except Exception:
                        pass

                # Table cells
                if shape.HasTable:
                    try:
                        table = shape.Table
                        for r in range(1, table.Rows.Count + 1):
                            for c in range(1, table.Columns.Count + 1):
                                cell_tr = table.Cell(r, c).Shape.TextFrame.TextRange
                                while True:
                                    found = cell_tr.Replace(find_text, replace_text, 0, case_sensitive, False)
                                    if found is not None:
                                        count += 1
                                    else:
                                        break
                    except Exception:
                        pass

                # Group items
                try:
                    if shape.Type == 6:  # msoGroup
                        for subshape in shape.GroupItems:
                            count += replace_in_shape(subshape)
                except Exception:
                    pass

                return count

            for s in target_slides:
                for shp in s.Shapes:
                    replaced_count += replace_in_shape(shp)

            pres.Save()
            if not was_already_open and pres is not None:
                pres.Close()

            return replaced_count
        except Exception as e:
            print(f"[PptAgent] COM replace error: {e}")
            return None
        finally:
            pythoncom.CoUninitialize()

    def _add_slide_via_com(
        self,
        file_path: str,
        title: str,
        bullet_points: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Uses PowerPoint COM to duplicate existing slide layout/styling to guarantee 100% design preservation"""
        if not HAS_WIN32COM:
            return None

        pythoncom.CoInitialize()
        ppt_app = None
        pres = None
        was_already_open = False
        norm_path = os.path.abspath(file_path).lower()

        try:
            try:
                ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
                for p in ppt_app.Presentations:
                    try:
                        if os.path.abspath(p.FullName).lower() == norm_path:
                            pres = p
                            was_already_open = True
                            break
                    except Exception:
                        pass
            except Exception:
                pass

            if pres is None:
                ppt_app = win32com.client.Dispatch("PowerPoint.Application")
                pres = ppt_app.Presentations.Open(os.path.abspath(file_path), False, False, False)

            total_slides = pres.Slides.Count
            new_slide = None

            if total_slides > 0:
                # Duplicate the last slide to replicate the exact design, theme, colors, fonts, and graphics
                dup_slide = pres.Slides(total_slides).Duplicate()
                new_slide = pres.Slides(pres.Slides.Count)
                title_shape = None
                try:
                    if new_slide.Shapes.HasTitle:
                        title_shape = new_slide.Shapes.Title
                        title_shape.TextFrame.TextRange.Text = title
                except Exception:
                    title_shape = None

                # Find body placeholder or next text box
                content_updated = False
                for shp in new_slide.Shapes:
                    if title_shape is not None and shp == title_shape:
                        continue
                    if shp.HasTextFrame:
                        if title_shape is None:
                            shp.TextFrame.TextRange.Text = title
                            title_shape = shp
                        else:
                            bullet_text = "\r".join(bullet_points)
                            shp.TextFrame.TextRange.Text = bullet_text
                            content_updated = True
                            break
            else:
                # Empty deck: create slide with default custom layout
                custom_layout = pres.SlideMaster.CustomLayouts(1) if pres.SlideMaster.CustomLayouts.Count > 0 else 1
                new_slide = pres.Slides.AddSlide(1, custom_layout)
                try:
                    if new_slide.Shapes.HasTitle:
                        new_slide.Shapes.Title.TextFrame.TextRange.Text = title
                except Exception:
                    pass

            pres.Save()
            res_num = pres.Slides.Count
            if not was_already_open:
                pres.Close()

            return {
                "status": "success",
                "action": "add_slide",
                "engine": "PowerPoint COM (100% Native Design Preserved)",
                "file": file_path,
                "new_slide_num": res_num,
                "title": title,
                "bullets": bullet_points
            }
        except Exception as e:
            print(f"[PptAgent] COM add_slide error: {e}")
            return None
        finally:
            pythoncom.CoUninitialize()

    # --------------------------------------------------------------------------
    # python-pptx Fallback Helpers (Run-level Formatting Preservation)
    # --------------------------------------------------------------------------
    def _replace_text_in_paragraph_preserving_format(
        self,
        paragraph,
        find_text: str,
        replace_text: str,
        case_sensitive: bool = False
    ) -> int:
        """
        Replaces find_text with replace_text inside a paragraph while strictly preserving
        character formatting (font family, font size, color, bold, italic) across runs.
        """
        if not paragraph.runs:
            if find_text in paragraph.text:
                paragraph.text = paragraph.text.replace(find_text, replace_text)
                return 1
            return 0

        count = 0
        flags = 0 if case_sensitive else re.IGNORECASE

        # 1. Single run replacement (cleanest: run's font/color/size remain 100% untouched)
        for run in paragraph.runs:
            if not run.text:
                continue
            if re.search(re.escape(find_text), run.text, flags):
                new_text, n = re.subn(re.escape(find_text), replace_text, run.text, flags=flags)
                if n > 0:
                    run.text = new_text
                    count += n

        if count > 0:
            return count

        # 2. Multi-run replacement (find_text spans across multiple XML runs)
        full_text = "".join(r.text for r in paragraph.runs)
        match = re.search(re.escape(find_text), full_text, flags)
        if not match:
            return 0

        start_idx, end_idx = match.span()
        curr_pos = 0
        first_run_idx = -1
        last_run_idx = -1
        start_in_run = 0
        end_in_run = 0

        for i, run in enumerate(paragraph.runs):
            run_len = len(run.text)
            run_end = curr_pos + run_len

            if first_run_idx == -1 and run_end > start_idx:
                first_run_idx = i
                start_in_run = start_idx - curr_pos

            if run_end >= end_idx and last_run_idx == -1:
                last_run_idx = i
                end_in_run = end_idx - curr_pos
                break

            curr_pos = run_end

        if first_run_idx != -1 and last_run_idx != -1:
            if first_run_idx == last_run_idx:
                r = paragraph.runs[first_run_idx]
                r.text = r.text[:start_in_run] + replace_text + r.text[end_in_run:]
            else:
                # First run keeps its font styling and receives replacement
                r_first = paragraph.runs[first_run_idx]
                r_first.text = r_first.text[:start_in_run] + replace_text
                # Middle runs cleared
                for mid in range(first_run_idx + 1, last_run_idx):
                    paragraph.runs[mid].text = ""
                # Last run has matched portion removed
                r_last = paragraph.runs[last_run_idx]
                r_last.text = r_last.text[end_in_run:]
            count += 1

        return count

    def _replace_in_shape_recursive(self, shape, find_text: str, replace_text: str, case_sensitive: bool = False) -> int:
        """Recursively replaces text in shapes, grouped shapes, and tables"""
        count = 0
        # Text Frame
        if shape.has_text_frame:
            for p in shape.text_frame.paragraphs:
                count += self._replace_text_in_paragraph_preserving_format(p, find_text, replace_text, case_sensitive)

        # Table
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    if cell.text_frame:
                        for p in cell.text_frame.paragraphs:
                            count += self._replace_text_in_paragraph_preserving_format(p, find_text, replace_text, case_sensitive)

        # Grouped shapes
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            for subshape in shape.shapes:
                count += self._replace_in_shape_recursive(subshape, find_text, replace_text, case_sensitive)

        return count

    # --------------------------------------------------------------------------
    # Public Editing APIs
    # --------------------------------------------------------------------------
    def replace_text(
        self,
        file_path: str,
        find_text: str,
        replace_text: str,
        slide_num: Optional[int] = None,
        case_sensitive: bool = False
    ) -> Dict[str, Any]:
        """
        Replaces text across all slides or on a specific slide without altering any
        underlying design, themes, colors, or typography.
        Tries PowerPoint COM first; falls back to run-level python-pptx preservation.
        """
        # Try native COM first
        com_count = self._edit_via_com_replace(file_path, find_text, replace_text, slide_num, case_sensitive)
        if com_count is not None:
            return {
                "status": "success",
                "action": "replace_text",
                "engine": "PowerPoint COM",
                "occurrences_replaced": com_count,
                "find_text": find_text,
                "replace_text": replace_text,
                "design_preserved": True
            }

        # Fallback to python-pptx with strict run preservation
        prs = pptx.Presentation(file_path)
        count = 0
        target_slides = [prs.slides[slide_num - 1]] if (slide_num and 1 <= slide_num <= len(prs.slides)) else prs.slides

        for slide in target_slides:
            for shape in slide.shapes:
                count += self._replace_in_shape_recursive(shape, find_text, replace_text, case_sensitive)

        prs.save(file_path)
        return {
            "status": "success",
            "action": "replace_text",
            "engine": "python-pptx (Run-level Format Preservation)",
            "occurrences_replaced": count,
            "find_text": find_text,
            "replace_text": replace_text,
            "design_preserved": True
        }

    def add_slide(
        self,
        file_path: str,
        title: str,
        bullet_points: List[str]
    ) -> Dict[str, Any]:
        """
        Adds a new slide inheriting the presentation's exact theme, colors, fonts,
        and layouts without changing any existing design.
        """
        # Try native COM duplication first
        com_res = self._add_slide_via_com(file_path, title, bullet_points)
        if com_res is not None:
            return com_res

        # Fallback to python-pptx with style inheritance
        prs = pptx.Presentation(file_path)
        # Choose layout matching content slide (usually layout 1)
        layout_idx = 1 if len(prs.slide_layouts) > 1 else 0
        slide_layout = prs.slide_layouts[layout_idx]
        slide = prs.slides.add_slide(slide_layout)

        # Inspect previous slide to inherit typography styles
        font_name = None
        font_color = None
        if len(prs.slides) > 1:
            prev_slide = prs.slides[-2]
            if prev_slide.shapes.title and prev_slide.shapes.title.has_text_frame:
                p0 = prev_slide.shapes.title.text_frame.paragraphs[0]
                if p0.runs:
                    font_name = p0.runs[0].font.name
                    if p0.runs[0].font.color and p0.runs[0].font.color.type == 1:
                        font_color = p0.runs[0].font.color.rgb

        if slide.shapes.title:
            slide.shapes.title.text = title
            if font_name and slide.shapes.title.text_frame.paragraphs[0].runs:
                r = slide.shapes.title.text_frame.paragraphs[0].runs[0]
                r.font.name = font_name
                if font_color:
                    r.font.color.rgb = font_color

        if len(slide.placeholders) > 1:
            body_shape = slide.placeholders[1]
            tf = body_shape.text_frame
            tf.clear()
            for i, bullet in enumerate(bullet_points):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = bullet
                if font_name and p.runs:
                    p.runs[0].font.name = font_name
        else:
            left = Inches(1.0)
            top = Inches(2.0)
            width = Inches(8.0)
            height = Inches(4.5)
            txBox = slide.shapes.add_textbox(left, top, width, height)
            tf = txBox.text_frame
            for i, bullet in enumerate(bullet_points):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = f"• {bullet}"
                if font_name and p.runs:
                    p.runs[0].font.name = font_name

        prs.save(file_path)
        return {
            "status": "success",
            "action": "add_slide",
            "engine": "python-pptx (Inherited Design)",
            "file": file_path,
            "new_slide_num": len(prs.slides),
            "title": title,
            "bullets": bullet_points
        }

    def update_slide(
        self,
        file_path: str,
        slide_num: int,
        title: Optional[str] = None,
        bullet_points: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Updates title and/or bullets on an existing slide while preserving its design"""
        prs = pptx.Presentation(file_path)
        if not (1 <= slide_num <= len(prs.slides)):
            raise ValueError(f"Slide number {slide_num} is out of range (1 - {len(prs.slides)})")

        slide = prs.slides[slide_num - 1]

        # Update title if provided
        if title is not None and slide.shapes.title and slide.shapes.title.has_text_frame:
            tf = slide.shapes.title.text_frame
            if tf.paragraphs and tf.paragraphs[0].runs:
                # Keep font properties of first run
                r0 = tf.paragraphs[0].runs[0]
                fname = r0.font.name
                fsize = r0.font.size
                fbold = r0.font.bold
                fcolor = r0.font.color.rgb if r0.font.color and r0.font.color.type == 1 else None
                r0.text = title
                # Clear extra runs
                for r_extra in tf.paragraphs[0].runs[1:]:
                    r_extra.text = ""
            else:
                slide.shapes.title.text = title

        # Update bullets if provided
        if bullet_points is not None:
            # Find body placeholder
            body_shape = None
            for shp in slide.placeholders:
                if shp != slide.shapes.title and shp.has_text_frame:
                    body_shape = shp
                    break
            if not body_shape:
                for shp in slide.shapes:
                    if shp != slide.shapes.title and shp.has_text_frame:
                        body_shape = shp
                        break

            if body_shape:
                tf = body_shape.text_frame
                tf.clear()
                for i, b in enumerate(bullet_points):
                    p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                    p.text = b

        prs.save(file_path)
        return {
            "status": "success",
            "action": "update_slide",
            "slide_num": slide_num,
            "title": title,
            "bullets": bullet_points,
            "design_preserved": True
        }

    def open_in_powerpoint(self, file_path: str) -> bool:
        """Launches the file in PowerPoint or default Windows application and brings to focus"""
        self.active_file = str(Path(file_path).resolve())
        abs_path = os.path.abspath(file_path)

        # Launch via native Windows ShellExecute (guarantees real interactive GUI window, never hangs)
        try:
            os.startfile(abs_path)
            return True
        except Exception as e:
            print(f"[PptAgent] Could not startfile: {e}")
            return False

    def read_full_content(self, file_path: Optional[str] = None) -> str:
        """Reads and returns the full text content of a PPT file, slide by slide, for LLM context."""
        target = file_path or self.active_file
        if not target or not Path(target).exists():
            return "No presentation loaded."
        try:
            prs = pptx.Presentation(target)
        except Exception as e:
            return f"Error reading presentation: {e}"

        lines = [f"Presentation: {Path(target).name} ({len(prs.slides)} slides)\n"]
        for idx, slide in enumerate(prs.slides, start=1):
            title = ""
            if slide.shapes.title and slide.shapes.title.has_text_frame:
                title = slide.shapes.title.text.strip()
            texts = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t and t != title:
                            texts.append(t)
                if shape.has_table:
                    for row in shape.table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if row_cells:
                            texts.append(" | ".join(row_cells))
            try:
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    note = slide.notes_slide.notes_text_frame.text.strip()
                    if note:
                        texts.append(f"[Speaker Notes: {note}]")
            except Exception:
                pass
            lines.append(f"--- Slide {idx}: {title or '(no title)'} ---")
            lines.extend(texts)
            lines.append("")
        return "\n".join(lines)

    def create_ppt(
        self,
        file_path: str,
        title: str,
        slides_data: List[Dict[str, Any]],
        theme: str = "dark"
    ) -> Dict[str, Any]:
        """
        Creates a brand-new PowerPoint presentation with a premium designed template.
        slides_data: list of {"title": str, "bullets": [str], "type": "title"|"content"|"closing"}
        theme: "dark" | "light" | "corporate"
        """
        from pptx import Presentation as PptxPresentation
        from pptx.util import Inches, Pt, Emu
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
        from pptx.util import Pt

        # Color palettes
        THEMES = {
            "dark": {
                "bg": RGBColor(0x0D, 0x1B, 0x2A),        # Deep navy
                "accent": RGBColor(0x00, 0xB4, 0xD8),    # Cyan blue
                "accent2": RGBColor(0x90, 0xE0, 0xEF),   # Light blue
                "title_text": RGBColor(0xFF, 0xFF, 0xFF), # White
                "body_text": RGBColor(0xCA, 0xD3, 0xDB),  # Light grey
                "bar": RGBColor(0x00, 0xB4, 0xD8),
                "slide_bg": RGBColor(0x14, 0x27, 0x3E),   # Slightly lighter navy for content slides
            },
            "light": {
                "bg": RGBColor(0xF8, 0xF9, 0xFA),
                "accent": RGBColor(0x45, 0x52, 0xE1),
                "accent2": RGBColor(0x74, 0x80, 0xEB),
                "title_text": RGBColor(0x1A, 0x1A, 0x2E),
                "body_text": RGBColor(0x33, 0x33, 0x44),
                "bar": RGBColor(0x45, 0x52, 0xE1),
                "slide_bg": RGBColor(0xFF, 0xFF, 0xFF),
            },
            "corporate": {
                "bg": RGBColor(0x1B, 0x26, 0x3B),
                "accent": RGBColor(0xE8, 0x63, 0x1A),
                "accent2": RGBColor(0xF4, 0xA2, 0x61),
                "title_text": RGBColor(0xFF, 0xFF, 0xFF),
                "body_text": RGBColor(0xE0, 0xE0, 0xE0),
                "bar": RGBColor(0xE8, 0x63, 0x1A),
                "slide_bg": RGBColor(0x22, 0x33, 0x4C),
            }
        }
        C = THEMES.get(theme, THEMES["dark"])

        prs = PptxPresentation()
        prs.slide_width = Inches(13.33)
        prs.slide_height = Inches(7.5)

        def set_bg(slide, color):
            from pptx.oxml.ns import qn
            from lxml import etree
            bg = slide.background
            fill = bg.fill
            fill.solid()
            fill.fore_color.rgb = color

        def add_rect(slide, left, top, width, height, color, transparency=0):
            shape = slide.shapes.add_shape(
                1,  # MSO_SHAPE_TYPE.RECTANGLE
                Inches(left), Inches(top), Inches(width), Inches(height)
            )
            shape.fill.solid()
            shape.fill.fore_color.rgb = color
            shape.line.fill.background()
            return shape

        def add_textbox(slide, text, left, top, width, height, font_size, color,
                        bold=False, align=PP_ALIGN.LEFT, font_name="Calibri"):
            txBox = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
            tf = txBox.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.alignment = align
            run = p.add_run()
            run.text = text
            run.font.size = Pt(font_size)
            run.font.color.rgb = color
            run.font.bold = bold
            run.font.name = font_name
            return txBox

        # ---- TITLE SLIDE ----
        blank_layout = prs.slide_layouts[6]  # blank
        title_slide = prs.slides.add_slide(blank_layout)
        set_bg(title_slide, C["bg"])

        # Left accent bar
        add_rect(title_slide, 0, 0, 0.4, 7.5, C["accent"])
        # Bottom accent strip
        add_rect(title_slide, 0, 6.8, 13.33, 0.7, C["accent"], transparency=0)

        # Big title
        add_textbox(title_slide, title,
                    left=0.9, top=2.2, width=11.5, height=1.8,
                    font_size=48, color=C["title_text"], bold=True,
                    align=PP_ALIGN.LEFT, font_name="Calibri Light")

        # Subtitle from first bullet of first content slide if available
        subtitle_text = slides_data[0].get("subtitle", "") if slides_data else ""
        if not subtitle_text and slides_data and slides_data[0].get("bullets"):
            subtitle_text = slides_data[0]["bullets"][0]
        if subtitle_text:
            add_textbox(title_slide, subtitle_text,
                        left=0.9, top=4.2, width=10.0, height=0.7,
                        font_size=18, color=C["accent2"],
                        align=PP_ALIGN.LEFT, font_name="Calibri")

        # Date
        import datetime
        date_str = datetime.datetime.now().strftime("%B %Y")
        add_textbox(title_slide, date_str,
                    left=0.9, top=6.85, width=6.0, height=0.55,
                    font_size=13, color=C["title_text"],
                    align=PP_ALIGN.LEFT, font_name="Calibri")

        # ---- CONTENT SLIDES ----
        for sdata in slides_data:
            s_type = sdata.get("type", "content")
            s_title = sdata.get("title", "")
            s_bullets = sdata.get("bullets", [])

            slide = prs.slides.add_slide(blank_layout)
            set_bg(slide, C["slide_bg"])

            # Top accent bar
            add_rect(slide, 0, 0, 13.33, 0.12, C["accent"])
            # Left thin stripe
            add_rect(slide, 0, 0, 0.08, 7.5, C["accent"])

            # Slide title
            add_textbox(slide, s_title,
                        left=0.4, top=0.3, width=12.0, height=0.85,
                        font_size=28, color=C["title_text"], bold=True,
                        align=PP_ALIGN.LEFT, font_name="Calibri Light")

            # Horizontal divider under title
            add_rect(slide, 0.4, 1.2, 12.5, 0.04, C["accent"])

            # Bullet points
            top_offset = 1.4
            for i, bullet in enumerate(s_bullets[:6]):
                # Bullet dot accent
                add_rect(slide, 0.4, top_offset + 0.07, 0.12, 0.12, C["accent"])
                add_textbox(slide, bullet,
                            left=0.65, top=top_offset, width=12.0, height=0.6,
                            font_size=16, color=C["body_text"],
                            align=PP_ALIGN.LEFT, font_name="Calibri")
                top_offset += 0.72 if len(bullet) < 80 else 0.90

            # Slide number bottom right
            slide_num = len(prs.slides)
            add_textbox(slide, str(slide_num),
                        left=12.5, top=7.1, width=0.7, height=0.35,
                        font_size=11, color=C["accent2"],
                        align=PP_ALIGN.RIGHT, font_name="Calibri")

        # ---- CLOSING SLIDE ----
        closing_slide = prs.slides.add_slide(blank_layout)
        set_bg(closing_slide, C["bg"])
        add_rect(closing_slide, 0, 0, 0.4, 7.5, C["accent"])
        add_rect(closing_slide, 0, 6.8, 13.33, 0.7, C["accent"])
        add_textbox(closing_slide, "Thank You",
                    left=0.9, top=2.8, width=11.5, height=1.3,
                    font_size=52, color=C["title_text"], bold=True,
                    align=PP_ALIGN.LEFT, font_name="Calibri Light")
        add_textbox(closing_slide, title,
                    left=0.9, top=4.3, width=10.0, height=0.65,
                    font_size=18, color=C["accent2"],
                    align=PP_ALIGN.LEFT, font_name="Calibri")

        # Save
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        prs.save(file_path)
        self.active_file = str(Path(file_path).resolve())

        return {
            "status": "success",
            "action": "create_ppt",
            "file": file_path,
            "title": title,
            "total_slides": len(prs.slides),
            "theme": theme
        }

    def get_rag_context(self, file_path: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
        """Builds or queries in-memory RAG index for PowerPoint presentations"""
        target = file_path or self.active_file
        if not target or not Path(target).exists():
            return {"error": "No active presentation selected"}

        if not hasattr(self, "_rag_cache"):
            self._rag_cache = {}

        norm_path = os.path.abspath(target)
        if norm_path not in self._rag_cache or (time.time() - self._rag_cache[norm_path].indexed_at > 30):
            self._rag_cache[norm_path] = SlideRAGIndex(norm_path)

        index = self._rag_cache[norm_path]
        if query:
            results = index.search(query, top_k=4)
        else:
            results = index.slides

        return {
            "name": Path(target).name,
            "path": target,
            "total_slides": len(index.slides),
            "summary": index.get_summary_text(),
            "matched_slides": results,
            "all_slides": index.slides
        }


class SlideRAGIndex:
    """RAG pipeline for PowerPoint presentations: indexes slides, shapes, tables, notes for semantic/keyword retrieval"""
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.slides: List[Dict[str, Any]] = []
        self.indexed_at = 0.0
        self.build_index()

    def build_index(self):
        p = Path(self.file_path)
        if not p.exists():
            return

        try:
            prs = pptx.Presentation(self.file_path)
        except Exception as e:
            print(f"[SlideRAGIndex] Error opening {self.file_path}: {e}")
            return

        self.slides = []

        for idx, slide in enumerate(prs.slides, start=1):
            title = ""
            bullets = []
            tables_text = []
            notes = ""

            # Extract title
            if slide.shapes.title and slide.shapes.title.has_text_frame and slide.shapes.title.text:
                title = slide.shapes.title.text.strip()

            # Extract shape text and tables
            for shape in slide.shapes:
                if shape == getattr(slide.shapes, "title", None):
                    continue
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = para.text.strip()
                        if t and t != title:
                            bullets.append(t)
                if shape.has_table:
                    for row in shape.table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                        if row_cells:
                            tables_text.append(" | ".join(row_cells))

            # Extract speaker notes
            try:
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    notes = slide.notes_slide.notes_text_frame.text.strip()
            except Exception:
                pass

            if not title and bullets:
                title = bullets[0]
                bullets = bullets[1:]

            full_content = "\n".join([f"Title: {title}"] + [f"• {b}" for b in bullets] + tables_text + ([f"Notes: {notes}"] if notes else []))

            self.slides.append({
                "slide_num": idx,
                "title": title or f"Slide {idx}",
                "bullets": bullets,
                "tables": tables_text,
                "notes": notes,
                "full_text": full_content,
                "tokens": set(re.findall(r"\w+", full_content.lower()))
            })

        self.indexed_at = time.time()

    def search(self, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        """Retrieves top_k most relevant slides for the query with score"""
        q_tokens = set(re.findall(r"\w+", query.lower()))
        if not q_tokens:
            return self.slides[:top_k]

        scored = []
        for s in self.slides:
            overlap = len(q_tokens.intersection(s["tokens"]))
            title_tokens = set(re.findall(r"\w+", s["title"].lower()))
            title_overlap = len(q_tokens.intersection(title_tokens))
            score = overlap + (title_overlap * 3)
            if score > 0:
                scored.append((score, s))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:top_k]]

    def get_summary_text(self) -> str:
        """Returns structured outline of presentation for RAG summary"""
        p = Path(self.file_path)
        lines = [f"📊 Presentation: {p.name} ({len(self.slides)} slides)"]
        for s in self.slides:
            b_sample = s["bullets"][:2]
            b_str = (" — " + "; ".join(b_sample)) if b_sample else ""
            lines.append(f"  Slide {s['slide_num']}: {s['title']}{b_str}")
        return "\n".join(lines)


ppt_agent = PptAgent()

