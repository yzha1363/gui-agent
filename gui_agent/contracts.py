"""Bounded model input and strict, non-executable proposal contracts."""

from dataclasses import dataclass
import base64
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image

from gui_workflow.plan import validate_plan


class AgentError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key.')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON value.')
    def floating(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError('Nonfinite JSON value.')
        return value
    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=floating)
    except (ValueError, TypeError, RecursionError):
        raise AgentError('invalid_json', 'Expected a single strict JSON document.') from None


@dataclass(frozen=True)
class AgentInput:
    instruction: str
    image_path: Path
    window_title: str
    history: tuple[str, ...] = ()
    state_fields: tuple[str, ...] = ()

    def prepare(self):
        for name, value, limit in [('instruction', self.instruction, 8000), ('window_title', self.window_title, 256)]:
            if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
                raise AgentError('invalid_input', f'{name} must be nonempty text within {limit} characters.')
        if any(ord(char) < 32 for char in self.window_title):
            raise AgentError('invalid_input', 'Window title cannot contain control characters.')
        if not isinstance(self.history, (list, tuple)) or len(self.history) > 20 or any(
            not isinstance(item, str) or len(item) > 2000 for item in self.history
        ):
            raise AgentError('invalid_input', 'history supports at most 20 text entries, each at most 2000 characters.')
        if not isinstance(self.state_fields, (list, tuple)) or len(self.state_fields) > 32 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 64 for item in self.state_fields
        ):
            raise AgentError('invalid_input', 'Provide at most 32 known independent state field names.')
        try:
            path = Path(self.image_path)
            if path.stat().st_size > 8 * 1024 * 1024:
                raise AgentError('invalid_image', 'Input image exceeds 8 MiB.')
            with Image.open(path) as image:
                if image.width * image.height > 16_000_000 or image.getexif().get(274, 1) != 1:
                    raise AgentError('invalid_image', 'Image exceeds 16 million pixels or has unsupported EXIF rotation.')
                image.load()
                image = image.convert('RGB')
                buffer = BytesIO()
                image.save(buffer, format='PNG')
                content = buffer.getvalue()
                width, height = image.size
            if len(content) > 8 * 1024 * 1024:
                raise AgentError('invalid_image', 'Encoded PNG exceeds 8 MiB.')
        except AgentError:
            raise
        except (OSError, ValueError, TypeError, Image.DecompressionBombError):
            raise AgentError('invalid_image', 'Cannot decode the input image.') from None
        return {'instruction': self.instruction, 'window_title': self.window_title, 'state_fields': list(self.state_fields),
                'history': list(self.history), 'image_bytes': content,
                'image': {'width': width, 'height': height, 'sha256': hashlib.sha256(content).hexdigest(),
                          'coordinate_space': 'image_pixels', 'desktop_origin': None},
                'data_url': 'data:image/png;base64,' + base64.b64encode(content).decode('ascii')}


def validate_proposal(content, window_title, state_fields=()):
    if not isinstance(content, str) or len(content) > 64_000:
        raise AgentError('invalid_response', 'Model content must be bounded JSON text.')
    value = strict_json(content)
    if not isinstance(value, dict) or set(value) != {'status', 'reason', 'step'}:
        raise AgentError('invalid_proposal', 'Response requires exactly status, reason and step.')
    if value['status'] not in ('propose', 'clarify', 'done'):
        raise AgentError('invalid_proposal', 'Unknown proposal status.')
    if not isinstance(value['reason'], str) or not value['reason'].strip() or len(value['reason']) > 2000:
        raise AgentError('invalid_proposal', 'reason must be nonempty text within 2000 characters.')
    if value['status'] == 'propose':
        try:
            plan = validate_plan({'window_title': window_title, 'steps': [value['step']]})
            step = plan['steps'][0]
            if step['expect']['transition'] is not True and step['action'] != 'wait_text':
                raise ValueError('Model proposals cannot disable transition checks.')
        except (ValueError, TypeError, KeyError):
            raise AgentError('invalid_proposal', 'Step must satisfy the workflow contract and dual verification.') from None
        value['step'] = step
        if set(step['expect']['state']) - set(state_fields):
            raise AgentError('unsupported_state_field', 'Proposal used state fields not supplied by the caller.')
    elif value['step'] is not None:
        raise AgentError('invalid_proposal', 'clarify and done require step=null.')
    return {**value, 'window_title': window_title, 'execution_eligible': False,
            'task_verified': False, 'desktop_input_sent': False}


def load_dataset_input(directory, index, window_title, state_fields=()):
    """Read only instruction, screenshot and past history; never source labels."""
    from gui_data.pipeline import audit, read_jsonl
    from gui_data.schema import safe_path
    root = Path(directory)
    audit(root)
    rows = read_jsonl(root / 'records.jsonl')
    if type(index) is not int or not 0 <= index < len(rows):
        raise AgentError('invalid_input', 'Dataset record index is out of range.')
    row = rows[index]
    image = row['observation']['image']
    if image is None:
        raise AgentError('invalid_input', 'Task-only evaluation records have no screenshot.')
    history = row['observation']['context'].get('history_before', [])
    return AgentInput(row['instruction'], safe_path(root, image['path']), window_title, tuple(history), state_fields)
