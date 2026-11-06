"""Windows Unicode input without clipboard changes or keyboard-layout conversion."""

import ctypes
from ctypes import wintypes
import sys
import time


class KeyInput(ctypes.Structure):
    _fields_ = [('vk', wintypes.WORD), ('scan', wintypes.WORD), ('flags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]


class MouseInput(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('data', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD), ('extra', ctypes.c_size_t)]


class HardwareInput(ctypes.Structure):
    _fields_ = [('message', wintypes.DWORD), ('low', wintypes.WORD), ('high', wintypes.WORD)]


class InputUnion(ctypes.Union):
    _fields_ = [('keyboard', KeyInput), ('mouse', MouseInput), ('hardware', HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('value', InputUnion)]


class InputSendError(OSError):
    def __init__(self, label, accepted, win32_error=None):
        detail = '' if win32_error is None else f' Win32 error={win32_error}.'
        super().__init__(f'{label} SendInput accepted {accepted}/1 event; no input retry.{detail}')
        self.accepted = accepted
        self.winerror = win32_error
        self.event_label = label


def _native_sender():
    if sys.platform != 'win32':
        raise OSError('Checked SendInput requires Windows.')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    sender = user32.SendInput
    sender.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    sender.restype = wintypes.UINT
    sender._gui_agent_native = True
    return sender


def _send_event(event, sender, label):
    single = (Input * 1)(event)
    native = getattr(sender, '_gui_agent_native', False) is True
    if native:
        ctypes.set_last_error(0)
    accepted = sender(1, single, ctypes.sizeof(Input))
    error = ctypes.get_last_error() if native else None
    if accepted != 1:
        raise InputSendError(label, accepted, error)


def _send_pair(down, up, sender, label, delay=0):
    _send_event(down, sender, label + ' down')
    try:
        if delay:
            time.sleep(delay)
        _send_event(up, sender, label + ' up')
    except BaseException:
        # Best-effort release only. Never resend a down event or the action.
        try:
            _send_event(up, sender, label + ' release')
        except OSError:
            pass
        raise


def click_button(button='left', sender=None):
    """Send checked down/up events individually; never fall back or replay."""
    flags = {'left': (0x0002, 0x0004), 'right': (0x0008, 0x0010), 'middle': (0x0020, 0x0040)}
    if button not in flags:
        raise ValueError('Unsupported mouse button.')
    sender = sender if sender is not None else _native_sender()
    events = []
    for flag in flags[button]:
        event = Input()
        event.type = 0
        event.value.mouse = MouseInput(0, 0, 0, flag, 0, 0)
        events.append(event)
    _send_pair(events[0], events[1], sender, 'Mouse', delay=.02)


def type_character(char, sender=None):
    if sys.platform != 'win32' and sender is None:
        from pynput.keyboard import Controller
        Controller().type(char)
        return
    sender = sender if sender is not None else _native_sender()
    encoded = char.encode('utf-16-le')
    units = [int.from_bytes(encoded[index:index + 2], 'little') for index in range(0, len(encoded), 2)]
    events = []
    for unit in units:
        for flags in (0x0004, 0x0004 | 0x0002):  # UNICODE / UNICODE + KEYUP
            event = Input()
            event.type = 1  # INPUT_KEYBOARD
            event.value.keyboard = KeyInput(0, unit, flags, 0, 0)
            events.append(event)
    for index in range(0, len(events), 2):
        _send_pair(events[index], events[index + 1], sender, 'Unicode')
