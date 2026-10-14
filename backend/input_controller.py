import ctypes
from ctypes import wintypes
import time
import pyautogui

# Optimize PyAutoGUI for real-time remote control
pyautogui.PAUSE = 0.0
pyautogui.FAILSAFE = False

# Windows API constants for mouse_event
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x00010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

user32 = ctypes.windll.user32

class InputController:
    def __init__(self):
        self.sensitivity = 1.6
        
    def move_rel(self, dx: float, dy: float):
        """Relative cursor motion for trackpad (instant Windows API)"""
        scaled_dx = int(dx * self.sensitivity)
        scaled_dy = int(dy * self.sensitivity)
        user32.mouse_event(MOUSEEVENTF_MOVE, scaled_dx, scaled_dy, 0, 0)

    def move_to(self, x: int, y: int):
        """Move cursor to absolute coordinates"""
        pyautogui.moveTo(x, y)

    def click(self, button: str = "left", clicks: int = 1):
        """Perform mouse click"""
        if button == "left":
            if clicks == 1:
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                time.sleep(0.01)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            elif clicks == 2:
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                time.sleep(0.05)
                user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif button == "right":
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(0.01)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
        elif button == "middle":
            user32.mouse_event(MOUSEEVENTF_MIDDLEDOWN, 0, 0, 0, 0)
            time.sleep(0.01)
            user32.mouse_event(MOUSEEVENTF_MIDDLEUP, 0, 0, 0, 0)

    def mouse_down(self, button: str = "left"):
        """Hold mouse button down (for dragging)"""
        if button == "left":
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        elif button == "right":
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)

    def mouse_up(self, button: str = "left"):
        """Release held mouse button"""
        if button == "left":
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        elif button == "right":
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)

    def scroll(self, dy: float, dx: float = 0.0):
        """Scroll vertical (dy) or horizontal (dx)"""
        # In Windows mouse_event, WHEEL_DELTA is 120
        wheel_amount = int(dy * 120)
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, wheel_amount, 0)

    def type_text(self, text: str):
        """Type text into active window"""
        pyautogui.write(text, interval=0.01)

    def press_key(self, key: str):
        """Press a special key (e.g. enter, backspace, esc, win, space, up, down, etc.)"""
        key_map = {
            "enter": "enter",
            "return": "enter",
            "backspace": "backspace",
            "tab": "tab",
            "escape": "escape",
            "esc": "escape",
            "space": "space",
            "win": "win",
            "super": "win",
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "delete": "delete",
            "ctrl": "ctrl",
            "alt": "alt",
            "shift": "shift"
        }
        target_key = key_map.get(key.lower(), key)
        pyautogui.press(target_key)

    def hotkey(self, *keys):
        """Trigger key combinations like ctrl+c, ctrl+v, alt+tab, win+d"""
        pyautogui.hotkey(*keys)

controller = InputController()
