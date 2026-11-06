"""Read-only Win32 input diagnostics. Requests zero input events; never clicks or types."""

import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui_control.unicode_input import Input, InputUnion, KeyInput, MouseInput, _native_sender


def probe_input_api(sender=None):
    if sender is None and sys.platform != 'win32':
        raise OSError('This diagnostic requires Windows.')
    native = sender is None
    sender = _native_sender() if sender is None else sender
    if native:
        ctypes.set_last_error(0)
    result = int(sender(0, None, ctypes.sizeof(Input)))
    error = ctypes.get_last_error() if native else None
    return {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'python_version': sys.version.split()[0],
            'pointer_bits': ctypes.sizeof(ctypes.c_void_p) * 8,
            'sizes': {'INPUT': ctypes.sizeof(Input), 'union': ctypes.sizeof(InputUnion),
                      'MOUSEINPUT': ctypes.sizeof(MouseInput), 'KEYBDINPUT': ctypes.sizeof(KeyInput)},
            'events_requested': 0, 'zero_event_return': result, 'win32_last_error': error,
            'return_is_zero': result == 0, 'actual_input_delivery_tested': False,
            'diagnostic_status': 'inconclusive',
            'note': 'Zero-event observation only. It cannot establish actual input success, system damage, permission failure or a specific cause.'}


def main():
    try:
        report = probe_input_api()
        directory = ROOT / 'output/week2-thursday/input-diagnostic' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        directory.mkdir(parents=True, exist_ok=False)
        (directory / 'diagnostic.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({**report, 'report': str(directory / 'diagnostic.json')}, ensure_ascii=True))
        return 0  # The diagnostic ran; zero-length return alone is not a pass/fail criterion.
    except OSError:
        print(json.dumps({'status': 'diagnostic_failed', 'events_requested': 0}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
