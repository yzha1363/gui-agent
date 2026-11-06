"""PyAutoGUI desktop controls with foreground targeting and an Escape stop key."""

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import threading
import time

from gui_perception.pipeline import enable_dpi_awareness


class ControlError(RuntimeError):
    pass


class StopRequested(ControlError):
    pass


class FocusLost(ControlError):
    pass


class WindowGuard:
    def __init__(self, title):
        if sys.platform != 'win32':
            raise ControlError('Foreground window validation currently supports Windows only.')
        self.title = title
        self.window_handle = None
        self.user32 = ctypes.windll.user32
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user32.WindowFromPoint.restype = wintypes.HWND
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    def active(self):
        handle = self.user32.GetForegroundWindow()
        title = ctypes.create_unicode_buffer(1024)
        self.user32.GetWindowTextW(handle, title, len(title))
        if title.value != self.title:
            raise FocusLost(f'Foreground window must be {self.title!r}; found {title.value!r}.')
        if self.window_handle is not None and handle != self.window_handle:
            raise FocusLost('Foreground window identity changed despite matching title.')
        self.window_handle = handle
        return handle

    def check(self, point=None):
        handle = self.active()
        if point is not None:
            rect = wintypes.RECT()
            if not self.user32.GetWindowRect(handle, ctypes.byref(rect)):
                raise ControlError('Cannot read target window bounds.')
            x, y = point
            if not rect.left <= x < rect.right or not rect.top <= y < rect.bottom:
                raise ControlError('Point is outside the target window.')
            at_point = self.user32.WindowFromPoint(wintypes.POINT(x, y))
            if self.user32.GetAncestor(at_point, 2) != handle:
                raise ControlError('Another window covers this point.')

    def client_region(self):
        """Return current client bounds in physical desktop pixels."""
        handle = self.active()
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        if not self.user32.GetClientRect(handle, ctypes.byref(rect)) or not self.user32.ClientToScreen(handle, ctypes.byref(origin)):
            raise ControlError('Cannot read target client bounds.')
        if rect.right <= 0 or rect.bottom <= 0:
            raise ControlError('Target window has no visible client area.')
        return [origin.x, origin.y, rect.right, rect.bottom]


class DesktopController:
    def __init__(self, window_title, pause=0.25, log_path='output/thursday/actions.jsonl',
                 backend=None, guard=None, listen=True, click_sender=None):
        enable_dpi_awareness()  # Must precede imports that inspect display metrics.
        if not math.isfinite(pause) or pause < 0.1:
            raise ValueError('Pause must be at least 0.1 seconds.')
        if backend is None:
            import pyautogui
            backend = pyautogui
        self.backend = backend
        self.click_sender = click_sender
        self.backend.FAILSAFE = True
        self.backend.PAUSE = pause
        self.guard = guard if guard is not None else WindowGuard(window_title)
        self.stopped = threading.Event()
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.listener = None
        if listen:
            from pynput import keyboard
            self.listener = keyboard.Listener(on_press=lambda key: self.stopped.set() if key == keyboard.Key.esc else None)
            self.listener.start()
            self.listener.wait()

    def close(self):
        if self.listener is not None:
            self.listener.stop()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def stop(self):
        self.stopped.set()

    def check(self, point=None):
        if self.stopped.is_set():
            raise StopRequested('Escape or stop requested; no further input will be sent.')
        self.backend.failSafeCheck()
        self.guard.check(point)

    def point(self, x, y):
        if not all(isinstance(n, (int, float)) and math.isfinite(n) for n in [x, y]):
            raise ValueError('Coordinates must be finite numbers.')
        point = (round(x), round(y))
        # PyAutoGUI officially supports the primary monitor; do not silently clamp.
        width, height = self.backend.size()
        if not 0 <= point[0] < width or not 0 <= point[1] < height:
            raise ControlError('This PyAutoGUI backend supports only primary-monitor coordinates.')
        self.check(point)
        return point

    def window_region(self):
        self.check()
        region = self.guard.client_region()
        left, top, width, height = region
        screen_width, screen_height = self.backend.size()
        if not (0 <= left and 0 <= top and left + width <= screen_width and top + height <= screen_height):
            raise ControlError('Keep the entire target client area on the primary monitor.')
        return region

    def action(self, name, args, call):
        try:
            self.check()
            result = call()
        except BaseException as exc:
            self.record(name, args, 'failed', type(exc).__name__ + ': ' + str(exc))
            raise
        self.record(name, args, 'sent')
        return result

    def record(self, action, args, status, error=None):
        entry = {'time_utc': datetime.now(timezone.utc).isoformat(), 'action': action,
                 'args': args, 'status': status, 'error': error}
        with self.log_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + '\n')

    def release(self, method, *args):
        # A corner fail-safe must not prevent releasing an already-held button/key.
        # Only the release is exempt; input checks remain enabled for every action.
        fail_safe = self.backend.FAILSAFE
        try:
            self.backend.FAILSAFE = False
            method(*args, _pause=False)
        finally:
            self.backend.FAILSAFE = fail_safe

    def click(self, x, y, clicks=1, button='left'):
        if clicks not in (1, 2) or button not in ('left', 'right', 'middle'):
            raise ValueError('Supported: 1 or 2 clicks; left/right/middle button.')
        def perform():
            point = self.point(x, y)
            self.backend.moveTo(*point, duration=0.2)
            for _ in range(clicks):
                self.check(point)
                if self.click_sender is None:
                    self.backend.click(*point, button=button)
                else:
                    if tuple(self.backend.position()) != point:
                        raise ControlError('Pointer did not reach the guarded target; no click dispatched.')
                    self.click_sender(button)
        return self.action('click', {'x': x, 'y': y, 'clicks': clicks, 'button': button}, perform)

    def type_text(self, text, interval=0.02):
        if not math.isfinite(interval) or interval < 0:
            raise ValueError('Typing interval must be nonnegative.')
        if any(ord(char) < 32 for char in text):
            raise ValueError('Use press() for Enter, Tab and other control keys.')
        def perform():
            from gui_control.unicode_input import type_character
            for char in text:
                self.check()
                type_character(char)
                time.sleep(interval)
        # Do not log typed text; retain only its length.
        return self.action('type_text', {'length': len(text)}, perform)

    def press(self, key):
        if key not in self.backend.KEYBOARD_KEYS:
            raise ValueError(f'Unknown key: {key}')
        return self.action('press', {'key': key}, lambda: self.backend.press(key))

    def hotkey(self, *keys):
        if not keys or any(key not in self.backend.KEYBOARD_KEYS for key in keys):
            raise ValueError('Unknown or empty keyboard shortcut.')
        def perform():
            held = []
            try:
                for key in keys:
                    self.check()
                    self.backend.keyDown(key)
                    held.append(key)
            finally:
                for key in reversed(held):
                    self.release(self.backend.keyUp, key)
        return self.action('hotkey', {'keys': keys}, perform)

    def scroll(self, clicks, x, y):
        if not isinstance(clicks, int) or clicks == 0 or abs(clicks) > 100:
            raise ValueError('Scroll clicks must be a nonzero integer in -100..100.')
        def perform():
            point = self.point(x, y)
            self.backend.moveTo(*point, duration=0.2)
            for _ in range(abs(clicks)):
                self.check(point)
                direction = 1 if clicks > 0 else -1
                # Pinned PyAutoGUI 0.9.54 passes Windows dwData through unchanged.
                # Windows expects WHEEL_DELTA=120 for a single wheel notch.
                amount = direction * (120 if sys.platform == 'win32' else 1)
                self.backend.scroll(amount, x=point[0], y=point[1])
        return self.action('scroll', {'clicks': clicks, 'x': x, 'y': y}, perform)

    def drag(self, x1, y1, x2, y2, duration=1.0):
        if not math.isfinite(duration) or not 0.2 <= duration <= 30:
            raise ValueError('Drag duration must be between 0.2 and 30 seconds.')
        def perform():
            start, end = self.point(x1, y1), self.point(x2, y2)
            self.backend.moveTo(*start, duration=0.2)
            self.check(start)
            self.backend.mouseDown()
            try:
                steps = max(4, round(duration / 0.05))
                for index in range(1, steps + 1):
                    point = self.point(start[0] + (end[0] - start[0]) * index / steps,
                                       start[1] + (end[1] - start[1]) * index / steps)
                    self.backend.moveTo(*point, _pause=False)
                    time.sleep(duration / steps)
            finally:
                # Release even after Esc/focus loss; do not generate a click.
                self.release(self.backend.mouseUp)
        return self.action('drag', {'from': [x1, y1], 'to': [x2, y2], 'duration': duration}, perform)
