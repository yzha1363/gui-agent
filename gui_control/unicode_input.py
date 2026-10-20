"""Windows Unicode input without clipboard changes or keyboard-layout conversion."""

import ctypes
from ctypes import wintypes
import sys


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


def type_character(char):
    if sys.platform != 'win32':
        from pynput.keyboard import Controller
        Controller().type(char)
        return
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    encoded = char.encode('utf-16-le')
    units = [int.from_bytes(encoded[index:index + 2], 'little') for index in range(0, len(encoded), 2)]
    events = []
    for unit in units:
        for flags in (0x0004, 0x0004 | 0x0002):  # UNICODE / UNICODE + KEYUP
            event = Input()
            event.type = 1  # INPUT_KEYBOARD
            event.value.keyboard = KeyInput(0, unit, flags, 0, 0)
            events.append(event)
    batch = (Input * len(events))(*events)
    sent = user32.SendInput(len(batch), batch, ctypes.sizeof(Input))
    if sent != len(batch):
        raise OSError(ctypes.get_last_error(), 'Unicode SendInput failed or was blocked by target privileges.')
