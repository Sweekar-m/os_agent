import io
import time
import json
import base64
import logging
import ctypes
from ctypes import wintypes
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image

from screen_capture import capture_screen_pil, get_screenshot_base64, get_screen_dimensions
from llm_gateway import llm_gateway

logger = logging.getLogger("vision_agent")
logger.setLevel(logging.INFO)

user32 = ctypes.windll.user32

class VisionAgent:
    """
    Genuine screen understanding combining Windows UI introspection,
    pixel coordinate mapping, DPI awareness, and Multimodal Vision LLMs.
    """
    def __init__(self):
        self.last_capture_time: float = 0.0
        self.last_screenshot_b64: Optional[str] = None
        self.screen_width, self.screen_height = get_screen_dimensions()

    def capture_full_screen(self, scale: float = 1.0) -> Tuple[Image.Image, str]:
        """Captures full desktop and caches the base64 JPEG representation."""
        img = capture_screen_pil(scale=scale, draw_cursor=True)
        self.screen_width, self.screen_height = img.width, img.height
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        raw_bytes = buf.getvalue()
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        self.last_screenshot_b64 = b64
        self.last_capture_time = time.time()
        return img, b64

    def get_active_window_info(self) -> Dict[str, Any]:
        """Returns details about the currently active foreground window."""
        fg = user32.GetForegroundWindow()
        if not fg:
            return {"hwnd": 0, "title": "", "rect": {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0}}

        length = user32.GetWindowTextLengthW(fg)
        title = ""
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(fg, buff, length + 1)
            title = buff.value

        rect = wintypes.RECT()
        user32.GetWindowRect(fg, ctypes.byref(rect))

        return {
            "hwnd": fg,
            "title": title,
            "rect": {
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": max(0, rect.right - rect.left),
                "height": max(0, rect.bottom - rect.top)
            }
        }

    async def analyze_screen(
        self,
        prompt: Optional[str] = None,
        focus_window: bool = False
    ) -> Dict[str, Any]:
        """
        Analyzes the current screen using the Vision LLM.
        Identifies active applications, dialogs, buttons, text, and possible interactions.
        """
        active_win = self.get_active_window_info()
        img, b64 = self.capture_full_screen(scale=0.85)

        user_prompt = prompt or (
            "Analyze this Windows desktop screenshot. "
            "1. Identify the active application and main visible windows.\n"
            "2. Describe any prominent dialogs, buttons, input fields, or text.\n"
            "3. If there are any errors or warnings visible, highlight them.\n"
            "4. Suggest what actions or agent (Browser, File, System) could assist here."
        )

        system_prompt = (
            f"You are the Vision Agent for a Windows OS Assistant.\n"
            f"Screen Resolution: {img.width}x{img.height}.\n"
            f"Foreground Window: '{active_win.get('title')}' (HWND {active_win.get('hwnd')}).\n"
            f"Provide an accurate, grounded visual assessment."
        )

        res = await llm_gateway.vision_analyze(
            prompt=user_prompt,
            image_b64=b64,
            system_prompt=system_prompt
        )

        return {
            "success": res.get("success", False),
            "analysis": res.get("analysis", ""),
            "active_window": active_win,
            "timestamp": self.last_capture_time,
            "screenshot_b64": b64
        }

    async def locate_target_coordinates(self, target_description: str) -> Dict[str, Any]:
        """
        Locates the screen coordinates (x, y) for a described UI element.
        First attempts Windows UI Automation discovery, then falls back to Vision LLM.
        """
        active_win = self.get_active_window_info()
        
        # UI Automation discovery attempt
        try:
            import comtypes
            import comtypes.client
            UIAutomationClient = comtypes.client.GetModule("UIAutomationCore.dll")
            uia = comtypes.client.CreateObject(UIAutomationClient.CUIAutomation, interface=UIAutomationClient.IUIAutomation)
            
            fg = active_win.get("hwnd")
            if fg:
                elem = uia.ElementFromHandle(fg)
                if elem:
                    cond = uia.CreateTrueCondition()
                    walker = uia.CreateTreeWalker(cond)
                    
                    target_lower = target_description.lower()
                    def search_tree(el, depth=0):
                        if depth > 5:
                            return None
                        child = walker.GetFirstChildElement(el)
                        while child:
                            try:
                                name = child.CurrentName or ""
                                if name and target_lower in name.lower():
                                    r = child.CurrentBoundingRectangle
                                    if r.right > r.left and r.bottom > r.top:
                                        cx = (r.left + r.right) // 2
                                        cy = (r.top + r.bottom) // 2
                                        return (cx, cy, name)
                                res = search_tree(child, depth + 1)
                                if res:
                                    return res
                            except Exception:
                                pass
                            child = walker.GetNextSiblingElement(child)
                        return None
                        
                    found = search_tree(elem)
                    if found:
                        cx, cy, elem_name = found
                        return {
                            "success": True,
                            "method": "uiautomation",
                            "x": cx,
                            "y": cy,
                            "element_name": elem_name,
                            "target": target_description
                        }
        except Exception as e:
            logger.debug(f"UI Automation search note: {e}")

        # Fallback to Vision LLM coordinate prediction
        img, b64 = self.capture_full_screen(scale=1.0)
        prompt = (
            f"You are a precise screen coordinate locator. "
            f"Find the target element: '{target_description}'. "
            f"The image resolution is {img.width}x{img.height}. "
            f"Return a strict JSON object with these keys: "
            f'{{"found": true, "x": <center_x_int>, "y": <center_y_int>, "description": "<brief description>"}}.'
            f"If not found, return {{\"found\": false}}."
        )

        res = await llm_gateway.vision_analyze(prompt, b64)
        if res.get("success"):
            text = res.get("analysis", "")
            try:
                # Find JSON block
                import re
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    coord_data = json.loads(match.group(0))
                    if coord_data.get("found"):
                        return {
                            "success": True,
                            "method": "vision_llm",
                            "x": int(coord_data.get("x", 0)),
                            "y": int(coord_data.get("y", 0)),
                            "target": target_description,
                            "description": coord_data.get("description", "")
                        }
            except Exception:
                pass

        return {
            "success": False,
            "error": f"Target element '{target_description}' could not be resolved on screen.",
            "target": target_description
        }

    async def explain_error_on_screen(self) -> Dict[str, Any]:
        """Specifically inspects screen for dialog errors, tracebacks, or warning indicators."""
        prompt = (
            "Examine this screen for any error messages, crash dialogs, terminal tracebacks, "
            "or warning alerts. If an error is found:\n"
            "1. What is the exact error message or code?\n"
            "2. Which application or process threw it?\n"
            "3. What is the root cause?\n"
            "4. Provide step-by-step instructions or commands to resolve it."
        )
        return await self.analyze_screen(prompt=prompt)

vision_agent = VisionAgent()
