"""Validate the whole workflow before sending the first input event."""

from copy import deepcopy
import math

ACTIONS = {'click_text', 'type_text', 'press', 'hotkey', 'wait_text'}
STEP_FIELDS = {'name', 'action', 'text', 'key', 'keys', 'contains', 'occurrence',
               'min_confidence', 'expect', 'timeout', 'lookup_attempts'}


def number(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be a finite number in {minimum}..{maximum}.')


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be a nonempty string.')
    if any(ord(char) < 32 for char in value):
        raise ValueError(f'{name} cannot contain control characters; use a key step.')


def validate_plan(plan, allowed_keys=None):
    """Return a normalized copy, rejecting errors anywhere before input starts.

    allowed_keys is supplied by the installed input backend for a full run;
    structure-only validation can omit it without accessing the desktop.
    """
    if not isinstance(plan, dict) or set(plan) - {'window_title', 'steps'}:
        raise ValueError('Plan supports only window_title and steps.')
    text(plan.get('window_title'), 'window_title')
    steps = plan.get('steps')
    if not isinstance(steps, list) or not steps:
        raise ValueError('steps must be a nonempty list.')
    result = deepcopy(plan)
    for index, step in enumerate(result['steps'], 1):
        if not isinstance(step, dict) or set(step) - STEP_FIELDS:
            raise ValueError(f'Step {index}: unsupported fields.')
        action = step.get('action')
        if action not in ACTIONS:
            raise ValueError(f'Step {index}: unknown action {action!r}.')
        step.setdefault('name', f'step_{index}')
        text(step['name'], 'step name')
        step.setdefault('timeout', 10)
        step.setdefault('lookup_attempts', 3)
        step.setdefault('min_confidence', 0.5)
        step.setdefault('contains', False)
        number(step['timeout'], 'timeout', 0.1, 300)
        number(step['min_confidence'], 'min_confidence', 0, 1)
        if type(step['lookup_attempts']) is not int or not 1 <= step['lookup_attempts'] <= 10:
            raise ValueError('lookup_attempts must be an integer in 1..10.')
        if type(step['contains']) is not bool:
            raise ValueError('contains must be boolean.')
        if action in {'click_text', 'type_text'}:
            text(step.get('text'), 'step text')
        if 'occurrence' in step and (type(step['occurrence']) is not int or step['occurrence'] < 1):
            raise ValueError('occurrence must be a positive integer.')
        if action == 'press':
            text(step.get('key'), 'key')
            if allowed_keys is not None and step['key'] not in allowed_keys:
                raise ValueError(f'Unknown key: {step["key"]}')
        if action == 'hotkey':
            if not isinstance(step.get('keys'), list) or not step['keys']:
                raise ValueError('hotkey needs a nonempty keys list.')
            for key in step['keys']:
                text(key, 'hotkey key')
                if allowed_keys is not None and key not in allowed_keys:
                    raise ValueError(f'Unknown key: {key}')
        condition = step.get('expect')
        if not isinstance(condition, dict) or set(condition) - {'text', 'present', 'contains', 'min_confidence', 'transition'}:
            raise ValueError(f'Step {index} needs an explicit expect condition.')
        text(condition.get('text'), 'expected text')
        condition.setdefault('present', True)
        condition.setdefault('contains', False)
        condition.setdefault('min_confidence', 0.5)
        condition.setdefault('transition', action != 'wait_text')
        for field in ['present', 'contains', 'transition']:
            if type(condition[field]) is not bool:
                raise ValueError(f'expect.{field} must be boolean.')
        number(condition['min_confidence'], 'expected confidence', 0, 1)
    return result
