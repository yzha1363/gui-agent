"""Versioned offline records and image-coordinate validation."""

import hashlib
import json
import math
from pathlib import Path

from PIL import Image

VERSION = '1.0'
KINDS = {'action', 'plan', 'reflection', 'task'}
ACTIONS = {'click', 'move', 'drag', 'scroll', 'type_text', 'press', 'hotkey',
           'wait', 'plan', 'reflect', 'select_option', 'hover'}


class DataError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise DataError('invalid_path', 'An asset path must be nonempty and relative.')
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise DataError('invalid_path', 'Asset path escapes the dataset directory.')
    return path


def finite(value):
    return type(value) in {int, float} and math.isfinite(value)


def point(value, width, height):
    if not isinstance(value, list) or len(value) != 2 or not all(finite(n) for n in value):
        raise DataError('invalid_coordinate', 'Point requires two finite numbers.')
    if not 0 <= value[0] < width or not 0 <= value[1] < height:
        raise DataError('out_of_bounds', 'Point must be inside its screenshot, without clamping.')


def prepare_image(raw_root, relative, out, max_side=1600, expected_size=None):
    path = safe_path(raw_root, relative)
    if not path.is_file():
        raise DataError('missing_image', str(relative))
    try:
        with Image.open(path) as image:
            if image.width * image.height > 40_000_000:
                raise DataError('image_too_large', 'Decoded image exceeds 40 million pixels.')
            if image.getexif().get(274, 1) != 1:
                raise DataError('unsupported_orientation', 'Rotated EXIF images require an explicit coordinate transform.')
            image.load()
            image = image.convert('RGB')
    except DataError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DataError('corrupt_image', str(exc)) from exc
    original = list(image.size)
    if expected_size is not None and original != expected_size:
        raise DataError('image_size_mismatch', f'Annotation size {expected_size} differs from image {original}.')
    pixel_hash = hashlib.sha256(str(original).encode() + image.tobytes()).hexdigest()
    ratio = min(1, max_side / max(image.size))
    size = [max(1, round(n * ratio)) for n in image.size]
    if size != original:
        image = image.resize(size, Image.Resampling.LANCZOS)
    relative_out = f'images/{pixel_hash}.png'
    dest = Path(out) / relative_out
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        image.save(dest)
    return {'path': relative_out, 'width': size[0], 'height': size[1],
            'original_width': original[0], 'original_height': original[1], 'mode': 'RGB',
            'original_path': relative, 'original_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'scale_xy': [size[0] / original[0], size[1] / original[1]],
            'sha256': hashlib.sha256(dest.read_bytes()).hexdigest(), 'pixel_sha256': pixel_hash,
            'coordinate_space': 'image_pixels', 'desktop_origin': None}


def validate_record(record):
    if record.get('schema_version') != VERSION or record.get('record_type') not in KINDS:
        raise DataError('invalid_schema', 'Unknown version or record type.')
    for key in ['id', 'group_id', 'instruction']:
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise DataError('missing_field', f'{key} must be nonempty text.')
    if record.get('execution_eligible') is not False:
        raise DataError('invalid_schema', 'Offline labels must not be executable desktop commands.')
    source = record.get('source', {})
    if not all(source.get(k) for k in ['name', 'revision', 'origin', 'raw_sha256', 'license', 'official_split']):
        raise DataError('missing_provenance', 'Source provenance is incomplete.')
    image = record.get('observation', {}).get('image')
    if record['record_type'] != 'task' and image is None:
        raise DataError('missing_image', 'Visual training records require a screenshot.')
    if image is not None:
        if any(type(image.get(k)) is not int or image[k] <= 0 for k in ['width', 'height']):
            raise DataError('invalid_image', 'Image dimensions must be positive integers.')
        if image.get('coordinate_space') != 'image_pixels' or image.get('desktop_origin') is not None:
            raise DataError('invalid_coordinate_space', 'Dataset image coordinates are not live desktop coordinates.')
    actions = record.get('label', {}).get('actions')
    if not isinstance(actions, list) or (record['record_type'] != 'task' and not actions):
        raise DataError('invalid_actions', 'Non-task records require labeled actions.')
    for action in actions:
        if not isinstance(action, dict) or action.get('type') not in ACTIONS:
            raise DataError('unsupported_action', repr(action))
        for field in ['point', 'from', 'to']:
            if field in action:
                if image is None:
                    raise DataError('missing_image', 'Coordinates require an image.')
                point(action[field], image['width'], image['height'])
        if action['type'] == 'type_text' and (not isinstance(action.get('text'), str) or not action['text']):
            raise DataError('invalid_text', 'Typing labels must preserve nonempty text.')
        if action['type'] == 'plan' and (not isinstance(action.get('text'), str) or not action['text'].strip()):
            raise DataError('invalid_plan', 'Planning labels must be nonempty text.')
        if action['type'] in {'click', 'move', 'hover'} and 'point' not in action and not isinstance(action.get('target'), dict):
            raise DataError('missing_target', 'Pointer action needs an image point or an explicitly unresolved target.')
        if action['type'] == 'drag' and not all(k in action for k in ['from', 'to']):
            raise DataError('drag_start_missing', 'Drag requires both endpoints.')
        if action['type'] == 'press' and (not isinstance(action.get('key'), str) or not action['key'].strip()):
            raise DataError('invalid_key', 'Key label must be nonempty text.')
        if action['type'] == 'hotkey' and (not isinstance(action.get('keys'), list) or len(action['keys']) < 2
                                           or any(not isinstance(k, str) or not k.strip() for k in action['keys'])):
            raise DataError('invalid_key', 'Hotkey label needs at least two nonempty keys.')
        if action['type'] == 'scroll' and (action.get('direction') not in {'up', 'down'}
                                           or type(action.get('notches')) is not int or action['notches'] < 1):
            raise DataError('invalid_scroll', 'Scroll needs a direction and positive notch count.')
        if action['type'] == 'wait' and (not finite(action.get('seconds')) or action['seconds'] < 0):
            raise DataError('invalid_wait', 'Wait must be finite and nonnegative.')
    if (record['source']['name'] == 'webarena' or source['official_split'] != 'train') and record.get('split') in {'train', 'validation'}:
        raise DataError('benchmark_contamination', 'Official evaluation data cannot enter training splits.')
    return record


def fingerprint(record):
    semantic = {'source': record['source']['name'], 'record_type': record['record_type'],
                'instruction': record['instruction'], 'image': (record['observation']['image'] or {}).get('pixel_sha256'),
                'context': record['observation'].get('context'), 'label': record['label']}
    return hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
