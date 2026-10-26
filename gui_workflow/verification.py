"""Independent application state checks. These values must never come from OCR."""

import json
from pathlib import Path
import time


class StateUnavailable(RuntimeError):
    pass


def publish_state(path, data):
    """Publish atomically; a transient Windows denial must not kill the UI loop.

    False means the previous snapshot remains untouched. The caller schedules
    another tick; it must not renew the previous snapshot's timestamp in place.
    Persistent failures therefore still trigger StateFile's freshness guard.
    """
    path = Path(path)
    pending = path.with_suffix('.tmp')
    try:
        pending.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        pending.replace(path)
    except PermissionError:
        return False
    return True


class StateFile:
    """Read a cooperative application's atomic state export, bound to its HWND.

    This is an application test adapter, not a generic accessibility reader or
    a security boundary. Never use an OCR report as this adapter's input.
    """
    def __init__(self, path, control, max_age=2):
        self.path, self.control, self.max_age = Path(path), control, max_age

    def __call__(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            age = time.time() - data['timestamp']
            if not 0 <= age <= self.max_age:
                raise StateUnavailable('Application state export is stale.')
            if data['hwnd'] != self.control.guard.active():
                raise StateUnavailable('Application state export belongs to another HWND.')
            if not isinstance(data['values'], dict):
                raise StateUnavailable('Invalid application state values.')
            return {'source': 'application-state-file', 'values': data['values']}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StateUnavailable(f'Cannot read independent application state: {exc}') from exc


def evaluate_state(snapshot, expected):
    if not snapshot or not isinstance(snapshot.get('values'), dict):
        return {'met': False, 'available': False, 'checks': []}
    values = snapshot['values']
    checks = [{'field': key, 'expected': value, 'actual': values.get(key),
               'met': key in values and type(values[key]) is type(value) and values[key] == value}
              for key, value in expected.items()]
    return {'met': all(check['met'] for check in checks), 'available': True,
            'source': snapshot.get('source', 'application-adapter'), 'checks': checks}


def failure_category(exc):
    """Stable broad classes; original exception code/message are preserved too."""
    code = getattr(exc, 'code', type(exc).__name__)
    if code in {'StopRequested', 'KeyboardInterrupt', 'FailSafeException'}:
        return 'user_aborted'
    if isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) == 5:
        return 'permission_denied'
    if code in {'TargetNotFound', 'AmbiguousTarget', 'TargetError', 'target_unavailable', 'StaleTarget'}:
        return 'localization_failure'
    if code in {'verification_timeout', 'condition_already_satisfied'}:
        return 'verification_failure'
    if code == 'StateUnavailable':
        return 'state_unavailable'
    if code == 'FocusLost':
        return 'window_guard_failure'
    return 'execution_failure'
