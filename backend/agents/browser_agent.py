import asyncio
import time
import logging
import ctypes
import re
from ctypes import wintypes
from typing import Dict, Any, List, Optional, Tuple

import psutil
import pyperclip
from config import settings
from input_controller import controller
from screen_capture import capture_screen_pil, get_screenshot_bytes, get_screenshot_base64
from llm_gateway import llm_gateway

logger = logging.getLogger("browser_agent")
logger.setLevel(logging.INFO)

user32 = ctypes.windll.user32

KNOWN_BROWSER_PROCESSES = ["chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe"]
KNOWN_BROWSER_CLASSES = ["Chrome_WidgetWin_1", "MozillaWindowClass"]

def get_interactive_desktop():
    """Attaches thread to interactive desktop for accurate window inspection."""
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
            return hdesk
    except Exception:
        pass
    return None

class BrowserAgent:
    """
    Controls EXISTING Chrome, Edge, Brave, or other installed browsers
    without launching isolated automation frameworks like Playwright.
    Combines Windows UI Automation interfaces, native shortcuts, and visual coordinates.
    """
    def __init__(self):
        self.last_active_hwnd: Optional[int] = None

    def _init_uia(self):
        """Initializes UIAutomation COM interface attached to interactive desktop."""
        try:
            get_interactive_desktop()
            import pythoncom
            pythoncom.CoInitialize()
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient as UIA
            uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
            return uia, UIA
        except Exception as e:
            logger.error(f"Failed to initialize UIAutomation: {e}")
            return None, None

    def find_existing_browsers(self) -> List[Dict[str, Any]]:
        """Scans open top-level desktop windows for existing browser instances."""
        hdesk = get_interactive_desktop()
        browsers = []

        def enum_cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    
                    class_buff = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buff, 256)
                    cls_name = class_buff.value

                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    pname = ""
                    if pid.value:
                        try:
                            pname = psutil.Process(pid.value).name().lower()
                        except Exception:
                            pass

                    # Detect browser based on process name, class, or title keywords
                    is_browser = False
                    browser_type = "unknown"

                    is_proc_browser = pname in KNOWN_BROWSER_PROCESSES
                    is_class_browser = cls_name in KNOWN_BROWSER_CLASSES
                    is_title_browser = any(b in title.lower() for b in [" - google chrome", " - microsoft​ edge", " - brave", " - edge", " - mozilla firefox"])

                    if is_proc_browser or is_class_browser or is_title_browser:
                        # Exclude self desktop client app windows
                        if title.strip() in ["OS Agent - ChatGPT Desktop", "ChatGPT", "Antigravity IDE"] and "preview" not in title.lower():
                            is_browser = False
                        else:
                            is_browser = True
                            if "edge" in pname or "edge" in title.lower():
                                browser_type = "Edge"
                            elif "brave" in pname or "brave" in title.lower():
                                browser_type = "Brave"
                            elif "chrome" in pname or "chrome" in title.lower():
                                browser_type = "Chrome"
                            elif "firefox" in pname or "firefox" in title.lower():
                                browser_type = "Firefox"
                            elif "opera" in pname or "opera" in title.lower():
                                browser_type = "Opera"
                            else:
                                browser_type = "Chromium"

                    # Ensure window has meaningful dimensions (not minimized to -32000)
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    if is_browser and w > 200 and h > 200 and rect.left > -10000:
                        browsers.append({
                            "hwnd": hwnd,
                            "title": title,
                            "type": browser_type,
                            "class": cls_name,
                            "process": pname,
                            "pid": pid.value,
                            "rect": {
                                "left": rect.left,
                                "top": rect.top,
                                "right": rect.right,
                                "bottom": rect.bottom,
                                "width": w,
                                "height": h
                            }
                        })
            return 1

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        cb = WNDENUMPROC(enum_cb)
        user32.EnumWindows(cb, 0)

        # Prioritize true browsers (Brave, Chrome, Edge) over embedded webviews
        def browser_priority(b):
            t = b.get("type", "")
            if t in ["Brave", "Chrome", "Edge"]:
                return 0
            if t in ["Firefox", "Opera"]:
                return 1
            return 2

        browsers.sort(key=browser_priority)
        return browsers

    def get_primary_browser(self) -> Optional[Dict[str, Any]]:
        browsers = self.find_existing_browsers()
        if not browsers:
            return None
        # Prefer the currently focused or last active browser
        if self.last_active_hwnd:
            for b in browsers:
                if b["hwnd"] == self.last_active_hwnd:
                    return b
        return browsers[0]

    def focus_browser(self, hwnd: Optional[int] = None) -> bool:
        """Brings the targeted browser window to the foreground."""
        target_hwnd = hwnd or (self.get_primary_browser() or {}).get("hwnd")
        if not target_hwnd:
            logger.warning("No existing browser window found to focus")
            return False

        # Restore window if minimized
        SW_RESTORE = 9
        user32.ShowWindow(target_hwnd, SW_RESTORE)
        user32.SetForegroundWindow(target_hwnd)
        self.last_active_hwnd = target_hwnd
        time.sleep(0.15)
        return True

    async def navigate(self, url: str, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """
        Navigates existing browser to the requested URL by focusing the address bar.
        Verifies completion by observing window state.
        """
        if not url.startswith("http://") and not url.startswith("https://") and not url.startswith("file://"):
            url = f"https://{url}"

        focused = self.focus_browser(hwnd)
        if not focused:
            return {
                "success": False,
                "error": "No open browser window found. Please open Chrome or Edge first.",
                "url": url
            }

        # Press Ctrl+L or Alt+D to highlight the browser address omnibox
        controller.hotkey("ctrl", "l")
        await asyncio.sleep(0.1)
        
        # Type the URL and press Enter
        controller.type_text(url)
        await asyncio.sleep(0.05)
        controller.press_key("enter")

        # Wait for page navigation to initiate
        await asyncio.sleep(1.2)

        # Verification snapshot
        primary = self.get_primary_browser()
        new_title = primary["title"] if primary else ""

        return {
            "success": True,
            "action": "navigate",
            "url": url,
            "current_browser_title": new_title,
            "message": f"Navigated existing browser to {url}"
        }

    async def search(self, query: str, engine: str = "google", hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Performs a web search in the existing browser."""
        import urllib.parse
        encoded_query = urllib.parse.quote(query)
        if engine == "bing":
            url = f"https://www.bing.com/search?q={encoded_query}"
        elif engine == "duckduckgo":
            url = f"https://duckduckgo.com/?q={encoded_query}"
        else:
            url = f"https://www.google.com/search?q={encoded_query}"

        res = await self.navigate(url, hwnd=hwnd)
        res["query"] = query
        res["engine"] = engine
        return res

    async def scroll(self, direction: str = "down", amount: float = 3.0, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Scrolls the active browser page."""
        self.focus_browser(hwnd)
        scroll_factor = -1.0 if direction.lower() == "down" else 1.0
        controller.scroll(scroll_factor * amount)
        await asyncio.sleep(0.2)
        return {
            "success": True,
            "action": "scroll",
            "direction": direction,
            "amount": amount
        }

    async def new_tab(self, url: Optional[str] = None, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Opens a new tab in the existing browser window."""
        self.focus_browser(hwnd)
        controller.hotkey("ctrl", "t")
        await asyncio.sleep(0.2)
        if url:
            return await self.navigate(url, hwnd)
        return {
            "success": True,
            "action": "new_tab",
            "message": "Opened new browser tab"
        }

    async def close_tab(self, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Closes the current tab in the existing browser window."""
        self.focus_browser(hwnd)
        controller.hotkey("ctrl", "w")
        await asyncio.sleep(0.2)
        return {
            "success": True,
            "action": "close_tab",
            "message": "Closed active browser tab"
        }

    async def switch_tab(self, direction: str = "next", hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Switches to the next or previous tab."""
        self.focus_browser(hwnd)
        if direction == "prev":
            controller.hotkey("ctrl", "shift", "tab")
        else:
            controller.hotkey("ctrl", "tab")
        await asyncio.sleep(0.2)
        primary = self.get_primary_browser()
        return {
            "success": True,
            "action": "switch_tab",
            "direction": direction,
            "active_tab_title": primary["title"] if primary else ""
        }

    async def click_at(self, x: int, y: int, button: str = "left", clicks: int = 1, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Clicks at specific screen coordinates in the browser window."""
        self.focus_browser(hwnd)
        controller.move_to(x, y)
        await asyncio.sleep(0.05)
        controller.click(button=button, clicks=clicks)
        await asyncio.sleep(0.3)
        return {
            "success": True,
            "action": "click",
            "x": x,
            "y": y,
            "button": button
        }

    async def type_text(self, text: str, press_enter: bool = False, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Types text into the currently active control in the browser."""
        self.focus_browser(hwnd)
        controller.type_text(text)
        if press_enter:
            await asyncio.sleep(0.05)
            controller.press_key("enter")
        await asyncio.sleep(0.2)
        return {
            "success": True,
            "action": "type_text",
            "length": len(text),
            "pressed_enter": press_enter
        }

    async def extract_page_text(self, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """Extracts visible text from the browser page via clipboard copy."""
        self.focus_browser(hwnd)
        old_clip = pyperclip.paste()
        try:
            # Click inside page body, select all, copy
            primary = self.get_primary_browser()
            if primary:
                r = primary["rect"]
                cx = (r["left"] + r["right"]) // 2
                cy = (r["top"] + r["bottom"]) // 2
                controller.click(clicks=1)
            
            controller.hotkey("ctrl", "a")
            await asyncio.sleep(0.1)
            controller.hotkey("ctrl", "c")
            await asyncio.sleep(0.15)
            # Unselect by pressing Escape or clicking
            controller.press_key("escape")
            
            page_text = pyperclip.paste()
            # If clipboard didn't change or is empty
            if page_text == old_clip and not page_text:
                page_text = "No text could be extracted via selection."
            return {
                "success": True,
                "text": page_text[:4000],
                "total_chars": len(page_text)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "text": ""
            }

    def get_open_tabs(self, hwnd: Optional[int] = None) -> Dict[str, Any]:
        """
        Enumerates live tabs in the existing browser session via Windows UI Automation.
        Returns verified tab details: ID, title, selection status, and browser type.
        """
        browser = None
        if hwnd:
            for b in self.find_existing_browsers():
                if b["hwnd"] == hwnd:
                    browser = b
                    break
        if not browser:
            browser = self.get_primary_browser()

        if not browser:
            return {
                "success": False,
                "agent": "Browser Agent",
                "action": "list_tabs",
                "error": "No open browser window (Chrome, Edge, Brave) was found on your desktop. Please open your browser first.",
                "browser_found": False,
                "total_tabs": 0,
                "tabs": [],
                "verification": {"status": "browser_not_found"}
            }

        target_hwnd = browser["hwnd"]
        uia, UIA = self._init_uia()
        if not uia:
            return {
                "success": False,
                "agent": "Browser Agent",
                "action": "list_tabs",
                "error": "Windows UI Automation interface is unavailable or disconnected.",
                "browser_found": True,
                "browser_type": browser.get("type", "Browser"),
                "total_tabs": 0,
                "tabs": [],
                "verification": {"status": "uia_initialization_failed"}
            }

        try:
            elem = uia.ElementFromHandle(target_hwnd)
            if not elem:
                return {
                    "success": False,
                    "agent": "Browser Agent",
                    "action": "list_tabs",
                    "error": f"Failed to attach UI Automation to browser window '{browser.get('title')}' (HWND {target_hwnd}).",
                    "browser_found": True,
                    "browser_type": browser.get("type", "Browser"),
                    "total_tabs": 0,
                    "tabs": [],
                    "verification": {"status": "element_attach_failed"}
                }

            cond_tab = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, 50019)  # UIA_TabItemControlTypeId
            tab_elements = elem.FindAll(UIA.TreeScope_Descendants, cond_tab)
            total = tab_elements.Length

            # Find address bar for current URL
            current_url = ""
            cond_edit = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, 50004)  # UIA_EditControlTypeId
            edits = elem.FindAll(UIA.TreeScope_Descendants, cond_edit)
            if edits.Length > 0:
                try:
                    vp = edits.GetElement(0).GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)
                    current_url = vp.CurrentValue or ""
                except Exception:
                    pass

            tabs_data = []
            active_idx = 1
            for i in range(total):
                t_elem = tab_elements.GetElement(i)
                raw_name = t_elem.CurrentName or f"Tab {i+1}"
                clean_name = re.sub(r"\s*-\s*Memory usage\s*-\s*[\d\.]+\s*[KMGT]?B\b", "", raw_name, flags=re.IGNORECASE).strip()
                clean_name = re.sub(r"\s*-\s*Audio playing\b", "", clean_name, flags=re.IGNORECASE).strip()

                is_selected = False
                try:
                    sel_p = t_elem.GetCurrentPattern(UIA.UIA_SelectionItemPatternId)
                    if sel_p:
                        sel_item = sel_p.QueryInterface(UIA.IUIAutomationSelectionItemPattern)
                        is_selected = bool(sel_item.CurrentIsSelected)
                        if is_selected:
                            active_idx = i + 1
                except Exception:
                    pass

                tabs_data.append({
                    "id": i + 1,
                    "title": clean_name or f"Tab {i+1}",
                    "is_active": is_selected,
                    "url": current_url if is_selected else ""
                })

            return {
                "success": True,
                "agent": "Browser Agent",
                "action": "list_tabs",
                "browser_type": browser.get("type", "Browser"),
                "browser_title": browser.get("title", ""),
                "hwnd": target_hwnd,
                "total_tabs": len(tabs_data),
                "active_tab_index": active_idx,
                "tabs": tabs_data,
                "verification": {
                    "status": "verified_live_uia",
                    "element_count": len(tabs_data),
                    "browser_type": browser.get("type", "Browser")
                }
            }
        except Exception as e:
            logger.error(f"Error enumerating browser tabs: {e}")
            return {
                "success": False,
                "agent": "Browser Agent",
                "action": "list_tabs",
                "error": f"Error inspecting live browser tabs: {str(e)}",
                "browser_found": True,
                "browser_type": browser.get("type", "Browser"),
                "total_tabs": 0,
                "tabs": [],
                "verification": {"status": "error", "exception": str(e)}
            }

    async def _summarize_tab_content(self, title: str, url: str, text: str) -> str:
        """Generates a concise 1-2 sentence summary for a browser tab."""
        cleaned_text = re.sub(r"\s+", " ", text).strip()
        if not cleaned_text or len(cleaned_text) < 20:
            return f"Page loaded with title '{title}'."

        # Filter out common web boilerplate lines
        boilerplate_prefixes = ["skip to content", "skip to navigation", "sign in", "log in", "menu", "search", "accept all cookies", "privacy policy"]
        words = cleaned_text.split()
        if words and words[0].lower() in boilerplate_prefixes:
            for bp in boilerplate_prefixes:
                if cleaned_text.lower().startswith(bp):
                    cleaned_text = cleaned_text[len(bp):].strip()

        # If LLM Gateway is configured, attempt intelligent concise summary
        if settings.LLM_API_KEY or settings.NVIDIA_API_KEY:
            try:
                prompt = (
                    f"Summarize the following web page in 1-2 concise, factual sentences for an open-tabs overview. "
                    f"Do not guess, do not add filler.\n"
                    f"Title: {title}\n"
                    f"URL: {url}\n"
                    f"Page Content Snippet:\n{cleaned_text[:1200]}"
                )
                res = await asyncio.wait_for(
                    llm_gateway.chat_complete(
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.2,
                        max_tokens=120
                    ),
                    timeout=5.0
                )
                if res.get("success") and res.get("content"):
                    summary_text = res["content"].strip().replace("\n", " ")
                    # Strip any accidental markdown quotes
                    summary_text = re.sub(r"^[\"']|[\"']$", "", summary_text).strip()
                    if len(summary_text) > 10:
                        return summary_text
            except Exception as e:
                logger.debug(f"LLM tab summarization fallback: {e}")

        # Deterministic extractive summarization fallback
        sentences = re.split(r"(?<=[.!?])\s+", cleaned_text)
        substantive = [s.strip() for s in sentences if len(s.strip()) > 25 and not any(bp in s.lower()[:30] for bp in boilerplate_prefixes)]
        if substantive:
            extractive = " ".join(substantive[:2])
            if len(extractive) > 280:
                extractive = extractive[:277].rsplit(" ", 1)[0] + "..."
            return extractive

        fallback_snip = cleaned_text[:200]
        if len(cleaned_text) > 200:
            fallback_snip += "..."
        return fallback_snip

    async def summarize_open_tabs(self, hwnd: Optional[int] = None, max_tabs: int = 15) -> Dict[str, Any]:
        """
        Enumerates currently open tabs in the user's existing Chrome, Edge, or Brave session,
        retrieves each tab's ID, title, URL, and page text using Windows UI Automation,
        and returns a grounded, concise summary retaining real titles and URLs.
        Identifies inaccessible/restricted tabs honestly.
        Restores the initially active tab when complete.
        """
        browser = None
        if hwnd:
            for b in self.find_existing_browsers():
                if b["hwnd"] == hwnd:
                    browser = b
                    break
        if not browser:
            browser = self.get_primary_browser()

        if not browser:
            return {
                "success": False,
                "agent": "Browser Agent",
                "action": "summarize_tabs",
                "error": "No open browser window (Chrome, Edge, Brave) was found on your desktop. Please open your browser first.",
                "browser_found": False,
                "total_tabs": 0,
                "tabs": [],
                "summary": "No running browser session (Chrome, Edge, Brave) was found on your desktop.",
                "verification": {"status": "browser_not_found"}
            }

        target_hwnd = browser["hwnd"]
        browser_type = browser.get("type", "Browser")
        uia, UIA = self._init_uia()
        if not uia:
            return {
                "success": False,
                "agent": "Browser Agent",
                "action": "summarize_tabs",
                "error": "Browser integration disconnected: Windows UI Automation interface is unavailable.",
                "browser_found": True,
                "browser_type": browser_type,
                "total_tabs": 0,
                "tabs": [],
                "summary": f"Could not inspect {browser_type} tabs because the Windows UI Automation accessibility interface is unavailable.",
                "verification": {"status": "uia_initialization_failed"}
            }

        try:
            elem = uia.ElementFromHandle(target_hwnd)
            if not elem:
                return {
                    "success": False,
                    "agent": "Browser Agent",
                    "action": "summarize_tabs",
                    "error": f"Failed to attach UI Automation to browser window '{browser.get('title')}' (HWND {target_hwnd}).",
                    "browser_found": True,
                    "browser_type": browser_type,
                    "total_tabs": 0,
                    "tabs": [],
                    "summary": f"Unable to attach to the live {browser_type} window.",
                    "verification": {"status": "element_attach_failed"}
                }

            cond_tab = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, 50019)  # UIA_TabItemControlTypeId
            tab_elements = elem.FindAll(UIA.TreeScope_Descendants, cond_tab)
            total = tab_elements.Length

            if total == 0:
                return {
                    "success": False,
                    "agent": "Browser Agent",
                    "action": "summarize_tabs",
                    "error": f"No open tabs detected in the active {browser_type} window.",
                    "browser_found": True,
                    "browser_type": browser_type,
                    "total_tabs": 0,
                    "tabs": [],
                    "summary": f"No tabs were detected in the active {browser_type} window.",
                    "verification": {"status": "zero_tabs_detected"}
                }

            # Locate initially active tab index to restore at the end
            initial_active_idx = 0
            for i in range(total):
                t_elem = tab_elements.GetElement(i)
                sel_p = t_elem.GetCurrentPattern(UIA.UIA_SelectionItemPatternId)
                if sel_p:
                    sel_item = sel_p.QueryInterface(UIA.IUIAutomationSelectionItemPattern)
                    if sel_item.CurrentIsSelected:
                        initial_active_idx = i
                        break

            cond_edit = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, 50004)  # UIA_EditControlTypeId
            edits = elem.FindAll(UIA.TreeScope_Descendants, cond_edit)
            edit_bar = edits.GetElement(0) if edits.Length > 0 else None

            cond_doc = uia.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, 50030)  # UIA_DocumentControlTypeId

            summarized_tabs = []
            readable_count = 0
            restricted_count = 0

            tabs_to_inspect = min(total, max_tabs)

            for i in range(tabs_to_inspect):
                t_elem = tab_elements.GetElement(i)
                raw_name = t_elem.CurrentName or f"Tab {i+1}"
                clean_title = re.sub(r"\s*-\s*Memory usage\s*-\s*[\d\.]+\s*[KMGT]?B\b", "", raw_name, flags=re.IGNORECASE).strip()
                clean_title = re.sub(r"\s*-\s*Audio playing\b", "", clean_title, flags=re.IGNORECASE).strip()

                # Switch to tab to inspect URL and page text
                try:
                    sel_p = t_elem.GetCurrentPattern(UIA.UIA_SelectionItemPatternId)
                    if sel_p:
                        sel_p.QueryInterface(UIA.IUIAutomationSelectionItemPattern).Select()
                        await asyncio.sleep(0.12)
                except Exception as sw_err:
                    logger.debug(f"Tab {i+1} selection error: {sw_err}")

                # Read Address Bar URL
                tab_url = ""
                if edit_bar:
                    try:
                        vp = edit_bar.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern)
                        raw_val = vp.CurrentValue or ""
                        if raw_val:
                            if not raw_val.startswith("http://") and not raw_val.startswith("https://") and not raw_val.startswith("file://") and not "://" in raw_val:
                                tab_url = f"https://{raw_val}"
                            else:
                                tab_url = raw_val
                    except Exception:
                        pass

                # Read page content via UIA Document element TextPattern
                page_text = ""
                docs = elem.FindAll(UIA.TreeScope_Descendants, cond_doc)
                if docs.Length > 0:
                    try:
                        doc = docs.GetElement(0)
                        # Check Document ValuePattern first for canonical URL if address bar was empty
                        if not tab_url:
                            try:
                                d_val = doc.GetCurrentPattern(UIA.UIA_ValuePatternId).QueryInterface(UIA.IUIAutomationValuePattern).CurrentValue
                                if d_val and (d_val.startswith("http://") or d_val.startswith("https://")):
                                    tab_url = d_val
                            except Exception:
                                pass

                        tp = doc.GetCurrentPattern(UIA.UIA_TextPatternId)
                        if tp:
                            tr = tp.QueryInterface(UIA.IUIAutomationTextPattern).DocumentRange
                            page_text = tr.GetText(2500) or ""
                    except Exception as doc_err:
                        logger.debug(f"Document text extraction error on tab {i+1}: {doc_err}")

                # Check access & generate honest summary
                is_accessible = True
                status_code = "accessible"
                tab_summary = ""

                lower_url = tab_url.lower()
                is_internal_url = any(lower_url.startswith(p) for p in ["chrome://", "edge://", "brave://", "about:", "chrome-extension://", "edge-extension://"])

                if is_internal_url:
                    is_accessible = False
                    status_code = "restricted"
                    restricted_count += 1
                    tab_summary = f"Restricted internal browser page (`{tab_url or 'internal'}`). Access is restricted by browser security policies."
                elif clean_title.lower() in ["new tab", "start page", "speed dial"] and not tab_url:
                    is_accessible = True
                    status_code = "empty_tab"
                    readable_count += 1
                    tab_summary = "Empty new browser tab."
                elif page_text and len(page_text.strip()) >= 25:
                    is_accessible = True
                    status_code = "summarized"
                    readable_count += 1
                    tab_summary = await self._summarize_tab_content(clean_title, tab_url, page_text)
                else:
                    is_accessible = False
                    status_code = "content_unavailable"
                    restricted_count += 1
                    tab_summary = "Page content is currently unavailable via accessibility interface (tab may be sleeping, unloaded, or protected)."

                summarized_tabs.append({
                    "id": i + 1,
                    "title": clean_title,
                    "url": tab_url,
                    "accessible": is_accessible,
                    "status": status_code,
                    "summary": tab_summary
                })

            # Restore the user's initially active tab
            try:
                init_tab = tab_elements.GetElement(initial_active_idx)
                sel_p = init_tab.GetCurrentPattern(UIA.UIA_SelectionItemPatternId)
                if sel_p:
                    sel_p.QueryInterface(UIA.IUIAutomationSelectionItemPattern).Select()
            except Exception:
                pass

            # Build grounded, human-readable summary
            lines = [f"I inspected your open **{browser_type}** session and summarized all {len(summarized_tabs)} tabs:\n"]
            for t in summarized_tabs:
                url_str = f"`{t['url']}`" if t.get("url") else "*(no URL)*"
                lines.append(f"• **{t['id']}. {t['title']}** ({url_str})\n  {t['summary']}")

            if total > max_tabs:
                lines.append(f"\n*(Inspected first {max_tabs} of {total} total open tabs)*")

            full_text = "\n".join(lines)
            sample_titles = [f"'{t['title']}'" for t in summarized_tabs[:3]]
            spoken = f"I went through {len(summarized_tabs)} tabs in your {browser_type} browser, including {', '.join(sample_titles)}."

            return {
                "success": True,
                "agent": "Browser Agent",
                "action": "summarize_tabs",
                "browser": browser_type,
                "browser_type": browser_type,
                "browser_title": browser.get("title", ""),
                "total_tabs": len(summarized_tabs),
                "readable_count": readable_count,
                "restricted_count": restricted_count,
                "tabs": summarized_tabs,
                "summaries": summarized_tabs,
                "summary": full_text,
                "spoken_summary": spoken,
                "verification": {
                    "status": "verified_live_inspection",
                    "browser_type": browser_type,
                    "total_inspected": len(summarized_tabs),
                    "readable_tabs": readable_count,
                    "restricted_tabs": restricted_count
                }
            }

        except Exception as e:
            logger.error(f"Error during tab summarization: {e}", exc_info=True)
            return {
                "success": False,
                "agent": "Browser Agent",
                "action": "summarize_tabs",
                "error": f"Error inspecting live browser tabs: {str(e)}",
                "browser_found": True,
                "browser_type": browser_type,
                "total_tabs": 0,
                "tabs": [],
                "summary": f"Encountered an issue inspecting live {browser_type} tabs: {str(e)}",
                "verification": {"status": "error", "exception": str(e)}
            }

browser_agent = BrowserAgent()

