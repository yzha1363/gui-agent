"""Strict offline action scoring and validation-only prompt selection.

No generated action is sent to a desktop. Annotation agreement is a proxy,
not independent evidence of the state of a real application.
"""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math

from gui_data.schema import DataError, finite, point

PROMPTS = ('original', 'schema_v1')
VARIANTS = ('base', 'best', 'final')
FIELDS = {
    'click': ({'type', 'point'}, {'button', 'clicks'}),
    'move': ({'type', 'point'}, set()),
    'drag': ({'type', 'from', 'to'}, {'button'}),
    'scroll': ({'type', 'direction', 'notches'}, {'point'}),
    'type_text': ({'type', 'text'}, set()),
    'press': ({'type', 'key'}, set()),
    'hotkey': ({'type', 'keys'}, set()),
    'wait': ({'type', 'seconds'}, set()),
}
SCHEMA_INSTRUCTION = (
    '\nOutput exactly one JSON object with the single key "actions" containing a nonempty array. '
    'No explanation, Markdown, Python code or execution claims. '
    'Use only these action shapes: '
    '{"type":"click","point":[x,y],"button":"left","clicks":1}; '
    '{"type":"move","point":[x,y]}; '
    '{"type":"drag","from":[x,y],"to":[x,y],"button":"left"}; '
    '{"type":"scroll","direction":"up or down","notches":positive_integer}; '
    '{"type":"type_text","text":"text to type"}; '
    '{"type":"press","key":"key name"}; '
    '{"type":"hotkey","keys":["modifier","key"]}; '
    '{"type":"wait","seconds":nonnegative_number}. '
    'x and y are numeric ORIGINAL screenshot pixels inside the image. '
    'Predict only the next annotated action sequence visible from the screenshot and task.'
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def inference_sample(sample):
    """Allowlisted projection: no target, provenance, history or prior answer."""
    return {name: deepcopy(sample[name]) for name in ('id', 'split', 'instruction', 'image')}


def prompt_for(sample, name):
    from gui_training.vlm import user_prompt
    if name not in PROMPTS: raise ValueError('Unknown prompt variant.')
    text = user_prompt(inference_sample(sample))
    return text if name == 'original' else text + SCHEMA_INSTRUCTION


def validate_eval_config(config):
    if config.get('variants') != list(VARIANTS) or config.get('prompts') != list(PROMPTS):
        raise ValueError('Use the declared three weights and two validation prompts.')
    if config.get('primary_prompt') != 'original' or config.get('selection_model') != 'best':
        raise ValueError('The primary prompt and validation selection model are fixed.')
    for name, low, high in [('seed', 0, 2**31 - 1), ('max_new_tokens', 1, 512), ('max_sequence_length', 256, 4096)]:
        if type(config.get(name)) is not int or not low <= config[name] <= high: raise ValueError('Invalid ' + name)
    if config.get('do_sample') is not False or type(config.get('num_beams')) is not int or config['num_beams'] != 1:
        raise ValueError('Evaluation requires deterministic greedy decoding.')
    value = config.get('coordinate_tolerance_pixels')
    if not finite(value) or not 0 <= value <= 50: raise ValueError('Invalid coordinate tolerance.')
    for name in ('training_config', 'training_run'):
        if not isinstance(config.get(name), str) or not config[name]: raise ValueError('Missing ' + name)
    return config


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise DataError('duplicate_json_key', key)
        result[key] = value
    return result


def _constant(value):
    raise DataError('nonfinite_json', value)


def parse_actions(response, width, height):
    """Strict JSON; never extract a convenient fragment or repair the answer."""
    if not isinstance(response, str) or not response.strip(): raise DataError('empty_output', 'No generated text.')
    try: value = json.loads(response, object_pairs_hook=_pairs, parse_constant=_constant)
    except json.JSONDecodeError as exc: raise DataError('invalid_json', str(exc)) from exc
    if not isinstance(value, dict) or set(value) != {'actions'}:
        raise DataError('invalid_action_schema', 'Exactly one actions key is required.')
    actions = value['actions']
    if not isinstance(actions, list) or not 1 <= len(actions) <= 32:
        raise DataError('invalid_action_schema', 'Require 1..32 actions.')
    for action in actions:
        if not isinstance(action, dict) or not isinstance(action.get('type'), str) or action['type'] not in FIELDS:
            raise DataError('unsupported_action', 'Unknown action type.')
        kind = action['type']; required, optional = FIELDS[kind]
        if not required <= set(action) or set(action) - required - optional:
            raise DataError('invalid_action_schema', 'Missing or extra action fields.')
        for name in ('point', 'from', 'to'):
            if name in action: point(action[name], width, height)
        if 'button' in action and action['button'] not in ('left', 'right', 'middle'):
            raise DataError('invalid_action_schema', 'Invalid mouse button.')
        if 'clicks' in action and (type(action['clicks']) is not int or not 1 <= action['clicks'] <= 3):
            raise DataError('invalid_action_schema', 'Invalid click count.')
        if kind == 'scroll' and (action['direction'] not in ('up', 'down') or type(action['notches']) is not int or action['notches'] < 1):
            raise DataError('invalid_action_schema', 'Invalid scroll parameters.')
        if kind == 'type_text' and (not isinstance(action['text'], str) or not action['text']):
            raise DataError('invalid_action_schema', 'Nonempty text required.')
        if kind == 'press' and (not isinstance(action['key'], str) or not action['key'].strip()):
            raise DataError('invalid_action_schema', 'Nonempty key required.')
        if kind == 'hotkey' and (not isinstance(action['keys'], list) or len(action['keys']) < 2 or
                                any(not isinstance(k, str) or not k.strip() for k in action['keys'])):
            raise DataError('invalid_action_schema', 'Require at least two key names.')
        if kind == 'wait' and (not finite(action['seconds']) or action['seconds'] < 0):
            raise DataError('invalid_action_schema', 'Invalid wait duration.')
    return actions


def canonical(action):
    result = deepcopy(action)
    if result['type'] in ('click', 'drag'): result.setdefault('button', 'left')
    if result['type'] == 'click': result.setdefault('clicks', 1)
    aliases = {'return': 'enter', 'escape': 'esc', 'control': 'ctrl'}
    def key(value): return aliases.get(value.strip().casefold(), value.strip().casefold())
    if result['type'] == 'press': result['key'] = key(result['key'])
    if result['type'] == 'hotkey': result['keys'] = sorted(key(k) for k in result['keys'])
    return result


def score_response(response, target, width, height, tolerance=10, generation_error=None):
    if not finite(tolerance) or tolerance < 0: raise ValueError('Tolerance must be finite and nonnegative.')
    # Labels pass the same structural validator; a broken reference must fail the experiment.
    expected = [canonical(a) for a in parse_actions(json.dumps(target, ensure_ascii=False, allow_nan=False), width, height)]
    expected_points = sum(name in a for a in expected for name in ('point', 'from', 'to'))
    report = {'json_valid': False, 'schema_valid': False, 'action_types_match': False,
              'parameters_match': False, 'annotation_match': False, 'exact_match': False,
              'pointer_points_expected': expected_points, 'pointer_points_correct': 0,
              'pointer_targets_match': False if expected_points else None,
              'parsed_actions': None, 'error_category': None, 'coordinate_distances_pixels': [],
              'task_verified': False, 'execution_eligible': False}
    if generation_error:
        report['error_category'] = 'generation_failure'; return report
    try:
        json.loads(response, object_pairs_hook=_pairs, parse_constant=_constant)
        report['json_valid'] = True
        actual = [canonical(a) for a in parse_actions(response, width, height)]
    except (DataError, json.JSONDecodeError, TypeError) as exc:
        report['error_category'] = getattr(exc, 'code', 'invalid_json'); return report
    report.update(schema_valid=True, parsed_actions=actual, exact_match=actual == expected)
    same_types = [a['type'] for a in actual] == [a['type'] for a in expected]
    report['action_types_match'] = same_types
    # Ordered alignment; missing actions/points remain incorrect in the denominator.
    other_parameters = same_types
    for index, label in enumerate(expected):
        prediction = actual[index] if index < len(actual) else {}
        matched_type = prediction.get('type') == label['type']
        for name in ('point', 'from', 'to'):
            if name in label:
                distance = math.dist(label[name], prediction[name]) if matched_type and name in prediction else None
                report['coordinate_distances_pixels'].append(distance)
                if distance is not None and distance <= tolerance: report['pointer_points_correct'] += 1
        reference = {k: v for k, v in label.items() if k not in ('point', 'from', 'to')}
        predicted = {k: v for k, v in prediction.items() if k not in ('point', 'from', 'to')}
        if reference != predicted or {k for k in prediction if k in ('point', 'from', 'to')} != {k for k in label if k in ('point', 'from', 'to')}:
            other_parameters = False
    if expected_points: report['pointer_targets_match'] = same_types and report['pointer_points_correct'] == expected_points
    report['parameters_match'] = other_parameters
    report['annotation_match'] = same_types and other_parameters and report['pointer_targets_match'] is not False
    if len(actual) != len(expected): report['error_category'] = 'action_count_mismatch'
    elif not same_types: report['error_category'] = 'action_type_mismatch'
    elif report['pointer_targets_match'] is False: report['error_category'] = 'coordinate_mismatch'
    elif not other_parameters: report['error_category'] = 'parameter_mismatch'
    return report


def aggregate(rows):
    total = len(rows)
    metrics = {name: {'correct': sum(bool(r['score'][name]) for r in rows), 'total': total}
               for name in ('json_valid', 'schema_valid', 'action_types_match', 'parameters_match', 'annotation_match', 'exact_match')}
    metrics['pointer_points'] = {'correct': sum(r['score']['pointer_points_correct'] for r in rows),
                                'total': sum(r['score']['pointer_points_expected'] for r in rows)}
    pointer_rows = [r for r in rows if r['score']['pointer_targets_match'] is not None]
    metrics['pointer_targets_match'] = {'correct': sum(r['score']['pointer_targets_match'] for r in pointer_rows), 'total': len(pointer_rows)}
    for value in metrics.values(): value['rate'] = value['correct'] / value['total'] if value['total'] else None
    return {'sample_count': total, 'metrics': metrics,
            'errors': dict(Counter(r['score']['error_category'] for r in rows if r['score']['error_category'])),
            'generation_errors': sum(r.get('generation_error') is not None for r in rows),
            'token_limit_hits': sum(bool(r.get('token_limit_hit')) for r in rows),
            'real_gui_success_rate': None}


def select_prompt(rows, validation_ids):
    """No test records, partial coverage, other weights or output-based exclusions."""
    ids = set(validation_ids)
    expected = {(name, identifier) for name in PROMPTS for identifier in ids}
    pairs = [(r['prompt_variant'], r['sample_id']) for r in rows]
    if not ids or len(pairs) != len(expected) or set(pairs) != expected:
        raise ValueError('Prompt selection requires complete unique validation coverage.')
    if any(r['split'] != 'validation' or r['variant'] != 'best' for r in rows):
        raise ValueError('Only the fixed best adapter on validation may select the prompt.')
    groups = {name: aggregate([r for r in rows if r['prompt_variant'] == name]) for name in PROMPTS}
    def rank(name):
        m = groups[name]
        return (m['metrics']['annotation_match']['correct'], m['metrics']['schema_valid']['correct'], -m['generation_errors'], -PROMPTS.index(name))
    selected = max(PROMPTS, key=rank)
    return {'selected_prompt': selected, 'selection_split': 'validation', 'selection_variant': 'best',
            'selection_rule': 'annotation_match, then schema_valid, then fewer generation errors; tie keeps original',
            'validation_ids': sorted(ids), 'validation_metrics': groups, 'test_used_for_selection': False}


def verify_case(row, tolerance):
    expected = score_response(row['response'], row['target'], row['image']['width'], row['image']['height'],
                              tolerance, row.get('generation_error'))
    if expected != row['score']: raise ValueError('Saved score does not match raw response and reference.')
    if row['prompt_sha256'] != hashlib.sha256(row['prompt'].encode('utf-8')).hexdigest():
        raise ValueError('Prompt hash mismatch.')
    return True


def paired_checks(rows, prompts, test_ids):
    expected = {(variant, prompt, identifier) for variant in VARIANTS for prompt in prompts for identifier in test_ids}
    keys = [(r['variant'], r['prompt_variant'], r['sample_id']) for r in rows]
    if len(keys) != len(expected) or set(keys) != expected or any(r['split'] != 'test' for r in rows):
        raise ValueError('Missing, duplicate or non-test paired result.')
    for prompt in prompts:
        for identifier in test_ids:
            group = [r for r in rows if r['sample_id'] == identifier and r['prompt_variant'] == prompt]
            for field in ('prompt_sha256', 'input_sha256', 'generation_config', 'image'):
                if any(r[field] != group[0][field] for r in group[1:]): raise ValueError('Unequal paired ' + field)
    return True
