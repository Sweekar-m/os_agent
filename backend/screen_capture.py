import io
import base64
from PIL import Image, ImageDraw, ImageFont
import mss
from config import settings

_sct = None

def ensure_interactive_desktop():
    """Ensures current thread is attached to the interactive 'default' desktop on WinSta0 so BitBlt succeeds"""
    try:
        import win32service, win32con, ctypes
        hdesk = win32service.OpenDesktop('default', 0, False, win32con.GENERIC_ALL)
        if hdesk:
            ctypes.windll.user32.SetThreadDesktop(int(hdesk))
    except Exception:
        pass

def get_sct():
    global _sct
    if _sct is None:
        ensure_interactive_desktop()
        _sct = mss.MSS()
    return _sct

def get_screen_dimensions():
    try:
        sct = get_sct()
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        return monitor["width"], monitor["height"]
    except Exception:
        return 1920, 1080

def create_fallback_image(width=1920, height=1080):
    """Creates a sleek fallback image if GDI screen grab is temporarily unavailable"""
    img = Image.new("RGB", (width, height), color=(24, 24, 27))
    draw = ImageDraw.Draw(img)
    text = "Desktop Screen Mirror Active\n(Waiting for interactive desktop stream...)"
    draw.text((width // 2 - 200, height // 2 - 20), text, fill=(200, 200, 200))
    return img

def get_cursor_position():
    """Returns current desktop cursor position and visibility state"""
    ensure_interactive_desktop()
    try:
        import ctypes
        from ctypes import wintypes
        class POINT(ctypes.Structure):
            _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]
        class CURSORINFO(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.DWORD),
                ('flags', wintypes.DWORD),
                ('hCursor', ctypes.c_void_p),
                ('ptScreenPos', POINT)
            ]
        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(CURSORINFO)
        res = ctypes.windll.user32.GetCursorInfo(ctypes.byref(ci))
        if res and ci.flags & 1:
            return {"x": ci.ptScreenPos.x, "y": ci.ptScreenPos.y, "visible": True}

        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return {"x": pt.x, "y": pt.y, "visible": True}
    except Exception:
        pass
    return {"x": 0, "y": 0, "visible": False}

def draw_cursor_on_image(img, scale=None):
    """Draws a high-visibility, professional cursor pointer on the screenshot at the exact Windows cursor position"""
    pos = get_cursor_position()
    if not pos.get("visible"):
        return img

    x, y = pos["x"], pos["y"]
    if scale and scale < 1.0:
        x = int(x * scale)
        y = int(y * scale)
        size = max(18, int(26 * scale))
    else:
        size = 26

    # Only draw if within image bounds
    if 0 <= x < img.width and 0 <= y < img.height:
        draw = ImageDraw.Draw(img)
        s = size / 26.0

        # High-precision cursor polygon relative to (x, y) tip
        cursor_poly = [
            (x, y),
            (x, y + int(24 * s)),
            (x + int(6 * s), y + int(19 * s)),
            (x + int(12 * s), y + int(29 * s)),
            (x + int(16 * s), y + int(27 * s)),
            (x + int(10 * s), y + int(17 * s)),
            (x + int(18 * s), y + int(17 * s))
        ]

        # Bold outer black contour for 100% contrast against bright backgrounds
        for dx in [-2, -1, 0, 1, 2]:
            for dy in [-2, -1, 0, 1, 2]:
                if abs(dx) + abs(dy) <= 3:
                    draw.polygon([(px + dx, py + dy) for px, py in cursor_poly], fill=(0, 0, 0))

        # Crisp white body for contrast against dark backgrounds
        draw.polygon(cursor_poly, fill=(255, 255, 255), outline=(0, 0, 0))

        # Precision tip accent (sleek cyan dot)
        draw.ellipse((x - 1, y - 1, x + 2, y + 2), fill=(16, 163, 127))

    return img

def capture_screen_pil(scale=None, draw_cursor=True):
    global _sct
    img = None
    ensure_interactive_desktop()

    try:
        sct = get_sct()
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
    except Exception:
        # Re-initialize sct in case display DC changed or invalidated
        try:
            _sct = mss.MSS()
            monitor = _sct.monitors[1] if len(_sct.monitors) > 1 else _sct.monitors[0]
            sct_img = _sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception:
            # Fallback to PIL ImageGrab if available
            try:
                from PIL import ImageGrab
                img = ImageGrab.grab()
            except Exception:
                w, h = get_screen_dimensions()
                img = create_fallback_image(w, h)
    
    if scale and scale < 1.0:
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

    if draw_cursor:
        img = draw_cursor_on_image(img, scale=scale)
        
    return img

def get_screenshot_bytes(scale=None, quality=None, draw_cursor=True):
    if scale is None:
        scale = settings.STREAM_SCALE
    if quality is None:
        quality = settings.STREAM_QUALITY
        
    img = capture_screen_pil(scale=scale, draw_cursor=draw_cursor)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()

def get_screenshot_base64(scale=None, quality=None, draw_cursor=True):
    data = get_screenshot_bytes(scale=scale, quality=quality, draw_cursor=draw_cursor)
    return base64.b64encode(data).decode("utf-8")

def get_frame_with_cursor(scale=None, quality=None):
    """Returns tuple of (b64_frame, cursor_info)"""
    cursor_info = get_cursor_position()
    b64_frame = get_screenshot_base64(scale=scale, quality=quality, draw_cursor=True)
    return b64_frame, cursor_info
