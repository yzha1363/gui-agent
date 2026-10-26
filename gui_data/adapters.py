"""Preserve source semantics; never infer execution success or missing screenshots."""

import json

from gui_data.schema import DataError, VERSION, finite, point, prepare_image, validate_record


def base(entry, manifest, instruction, group_id, image):
    if not isinstance(instruction, str) or not instruction.strip():
        raise DataError('missing_instruction', 'The source instruction must be nonempty text.')
    if group_id is None or not str(group_id).strip():
        raise DataError('missing_group', 'The source task/session ID is required for leakage-free splitting.')
    name = entry['source']
    source = manifest['sources'][name]
    return {'schema_version': VERSION, 'id': name + ':' + entry['raw_path'],
            'group_id': name + ':' + str(group_id), 'record_type': 'action',
            'instruction': instruction.strip(),
            'source': {'name': name, 'revision': source['revision'], 'official_split': source['official_split'],
                       'license': source['license'], 'origin': entry['origin'],
                       'raw_path': entry['raw_path'], 'raw_sha256': entry['raw_sha256'],
                       'row_index': entry.get('row_index')},
            'observation': {'image': image, 'context': {}}, 'label': {'actions': []},
            'verification': {'independent_postcondition_available': False,
                             'reason': 'No independently verified post-action control state is provided in this sample.'},
            'execution_eligible': False, 'quality': {'warnings': []}}


def screenagent(raw, entry, manifest, raw_root, out, max_side):
    image = prepare_image(raw_root, entry['image_path'], out, max_side,
                          [raw['video_width'], raw['video_height']])
    record = base(entry, manifest, raw['task_prompt'], raw['session_id'], image)
    # Preserve the source's subtask context as untrusted dataset text, not a system instruction.
    record['observation']['context'] = {'source_prompt': raw.get('send_prompt', ''),
                                         'prompt_trust': 'dataset_text_only'}
    actions, previous_point = [], None
    for original in raw['actions']:
        kind = original['action_type']
        if kind == 'PlanAction':
            action = {'type': 'plan', 'text': original['element']}
        elif kind == 'EvaluateSubTaskAction':
            action = {'type': 'reflect', 'situation': original['situation'], 'advice': original.get('advice', '')}
        elif kind == 'MouseAction':
            op = original['mouse_action_type']
            if op in {'scroll_up', 'scroll_down'}:
                count = original.get('scroll_repeat', 1)
                if type(count) is not int or count < 1:
                    raise DataError('invalid_scroll', 'scroll_repeat must be a positive integer.')
                action = {'type': 'scroll', 'direction': 'up' if op == 'scroll_up' else 'down', 'notches': count}
            elif op in {'click', 'double_click', 'move', 'drag'}:
                position = original['mouse_position']
                source_point = [position['width'], position['height']]
                point(source_point, image['original_width'], image['original_height'])
                mapped = [round(n * scale, 6) for n, scale in zip(source_point, image['scale_xy'])]
                if op == 'drag':
                    if previous_point is None:
                        raise DataError('drag_start_missing', 'Drag has no preceding mouse point in this action sequence.')
                    action = {'type': 'drag', 'from': previous_point, 'to': mapped, 'button': original.get('mouse_button', 'left')}
                else:
                    action = {'type': 'move' if op == 'move' else 'click', 'point': mapped}
                    if op != 'move':
                        action.update(button=original.get('mouse_button', 'left'), clicks=2 if op == 'double_click' else 1)
                previous_point = mapped
            else:
                raise DataError('unsupported_action', 'Unknown ScreenAgent mouse action: ' + str(op))
        elif kind == 'KeyboardAction':
            op = original['keyboard_action_type']
            if op == 'text':
                action = {'type': 'type_text', 'text': original['keyboard_text']}
            elif op == 'press':
                aliases = {'Return': 'enter', 'Ctrl': 'ctrl', 'Control_L': 'ctrl', 'Alt_L': 'alt', 'Shift_L': 'shift'}
                keys = [aliases.get(k, k) for k in original['keyboard_key'].split('+')]
                action = {'type': 'hotkey', 'keys': keys} if len(keys) > 1 else {'type': 'press', 'key': keys[0]}
            else:
                raise DataError('unsupported_action', 'Unknown ScreenAgent keyboard action: ' + str(op))
        elif kind == 'WaitAction':
            seconds = original['wait_time']
            if not finite(seconds) or seconds < 0:
                raise DataError('invalid_wait', 'Wait must be finite and nonnegative.')
            action = {'type': 'wait', 'seconds': seconds}
        else:
            raise DataError('unsupported_action', 'Unknown ScreenAgent action: ' + str(kind))
        actions.append(action)
    record['label']['actions'] = actions
    types = {action['type'] for action in actions}
    record['record_type'] = 'plan' if types == {'plan'} else 'reflection' if types == {'reflect'} else 'action'
    record['quality']['coordinate_status'] = 'image_pixels_scaled_from_declared_screenshot'
    return validate_record(record)


def object_value(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise DataError('invalid_json_field', 'Expected object or serialized JSON object.')
    # Some candidate attribute strings have an extra layer of escaped quotes.
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        result = json.loads(value.replace('\\"', '"'))
    if not isinstance(result, dict):
        raise DataError('invalid_json_field', 'Decoded field is not an object.')
    return result


def mind2web(raw, entry, manifest, raw_root, out, max_side):
    expected = [raw['screenshot']['width'], raw['screenshot']['height']]
    image = prepare_image(raw_root, entry['image_path'], out, max_side, expected)
    record = base(entry, manifest, raw['confirmed_task'], raw['annotation_id'], image)
    index = int(raw['target_action_index'])
    sequence = raw['action_reprs']
    if index < 0 or index >= len(sequence):
        raise DataError('invalid_history', 'Action index is outside the source sequence.')
    record['observation']['context'] = {'website': raw['website'], 'history_before': sequence[:index]}
    operation = object_value(raw['operation'])
    op = operation.get('original_op', operation['op'])
    action_type = {'CLICK': 'click', 'TYPE': 'type_text', 'SELECT': 'select_option',
                   'HOVER': 'hover', 'ENTER': 'press'}.get(op)
    if action_type is None:
        raise DataError('unsupported_action', 'Unknown Mind2Web operation: ' + str(op))
    candidates = []
    for candidate in raw['pos_candidates']:
        candidate = object_value(candidate)
        attrs = object_value(candidate['attributes'])
        item = {'backend_node_id': str(candidate['backend_node_id']), 'tag': candidate['tag'],
                'is_original_target': candidate.get('is_original_target', False),
                'coordinate_space': 'source_css_rect_unverified'}
        rect = attrs.get('bounding_box_rect')
        if rect:
            values = [float(n) for n in rect.split(',')] if isinstance(rect, str) else rect
            if len(values) != 4 or not all(finite(n) for n in values) or values[2] <= 0 or values[3] <= 0:
                raise DataError('invalid_dom_bbox', 'DOM rectangle must be finite xywh with positive size.')
            item['source_bbox_xywh'] = values
        candidates.append(item)
    action = {'type': action_type, 'target': {'kind': 'dom_candidates', 'candidates': candidates,
                                             'requires_visual_grounding': True}}
    if action_type == 'type_text':
        action['text'] = operation['value']
    elif action_type == 'select_option':
        action['value'] = operation['value']
    elif action_type == 'press':
        action['key'] = 'enter'
    record['label']['actions'] = [action]
    record['label']['source_action_repr'] = raw['target_action_reprs']
    record['quality']['coordinate_status'] = 'DOM/CSS coordinates retained; no pixel conversion assumed'
    record['quality']['warnings'] = ['full_page_image_may_reduce_text_after_resize', 'visual_grounding_not_verified']
    if not candidates:
        record['quality']['warnings'].append('no_positive_dom_candidate')
    return validate_record(record)


def webarena(raw, entry, manifest, raw_root, out, max_side):
    record = base(entry, manifest, raw['intent'], raw['task_id'], None)
    record['record_type'] = 'task'
    record['observation']['context'] = {'sites': raw['sites'], 'start_url': raw['start_url'],
                                         'require_login': raw.get('require_login', False),
                                         'environment_required': 'self_hosted_webarena'}
    record['label']['evaluation'] = raw['eval']  # Never included in model observation/context.
    record['quality']['warnings'] = ['no_screenshot', 'no_demonstrated_action_sequence', 'evaluation_only']
    record['split'] = 'evaluation'
    return validate_record(record)


ADAPTERS = {'screenagent': screenagent, 'mind2web': mind2web, 'webarena': webarena}
