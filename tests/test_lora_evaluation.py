"""Tests for offline scoring and protocol isolation; no model download/GPU."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from gui_data.schema import DataError
from gui_training.evaluation import (PROMPTS, aggregate, inference_sample, paired_checks, parse_actions, prompt_for,
                                     score_response, select_prompt, validate_eval_config, verify_case)

TARGET = {'actions': [{'type': 'click', 'point': [30, 40], 'button': 'left', 'clicks': 1}]}


def scored(response=None, **fields):
    row = {'split': 'validation', 'sample_id': 'v1', 'variant': 'best', 'prompt_variant': 'original',
           'response': json.dumps(TARGET) if response is None else response,
           'target': deepcopy(TARGET), 'image': {'width': 100, 'height': 100}, 'prompt': 'prompt',
           'generation_error': None, 'generation_config': {'max_new_tokens': 192}, 'input_sha256': 'same'}
    row.update(fields); row['prompt_sha256'] = hashlib.sha256(row['prompt'].encode()).hexdigest()
    row['score'] = score_response(row['response'], row['target'], 100, 100, generation_error=row['generation_error'])
    return row


class ParserTests(unittest.TestCase):
    def test_requires_one_root_actions_key_and_nonempty_list(self):
        for response in ['[]', '{}', '{"actions":[]}', '{"actions":{},"done":true}', '{"actions":[],"done":true}']:
            with self.subTest(response=response), self.assertRaises(DataError): parse_actions(response, 100, 100)

    def test_duplicate_keys_rejected_at_root_and_nested_levels(self):
        for response in ['{"actions":[],"actions":[]}', '{"actions":[{"type":"press","key":"a","key":"b"}]}']:
            with self.subTest(response=response), self.assertRaises(DataError): parse_actions(response, 100, 100)

    def test_nonfinite_json_is_never_a_valid_coordinate_or_wait(self):
        for value in ['NaN', 'Infinity', '-Infinity', '1e999']:
            with self.subTest(value=value), self.assertRaises(DataError):
                parse_actions('{"actions":[{"type":"wait","seconds":' + value + '}]}', 100, 100)

    def test_markdown_and_embedded_fragments_are_not_repaired(self):
        for response in ['```json\n' + json.dumps(TARGET) + '\n```', 'Here: ' + json.dumps(TARGET)]:
            with self.subTest(response=response), self.assertRaises(DataError): parse_actions(response, 100, 100)

    def test_boolean_coordinates_and_edges_outside_image_rejected(self):
        for p in [[True, 4], [100, 4], [4, -1], [0, False]]:
            with self.subTest(point=p), self.assertRaises(DataError):
                parse_actions(json.dumps({'actions': [{'type': 'click', 'point': p}]}), 100, 100)

    def test_unresolved_target_unknown_fields_and_unsupported_types_rejected(self):
        for action in [{'type': 'click', 'target': {'text': 'OK'}}, {'type': 'press', 'key': 'a', 'done': True},
                       {'type': 'plan', 'text': 'thinking'}, {'type': [], 'point': [2, 3]}]:
            with self.subTest(action=action), self.assertRaises(DataError): parse_actions(json.dumps({'actions': [action]}), 100, 100)

    def test_parameters_are_typed_and_not_coerced(self):
        for action in [{'type': 'click', 'point': [1, 2], 'clicks': True}, {'type': 'type_text', 'text': ''},
                       {'type': 'scroll', 'direction': 'up', 'notches': 1.5}, {'type': 'hotkey', 'keys': ['ctrl']}]:
            with self.subTest(action=action), self.assertRaises(DataError): parse_actions(json.dumps({'actions': [action]}), 100, 100)


class ScorerTests(unittest.TestCase):
    def test_defaults_match_without_mutating_reference(self):
        original = deepcopy(TARGET)
        score = score_response('{"actions":[{"type":"click","point":[30.0,40]}]}', TARGET, 100, 100)
        self.assertTrue(score['annotation_match']); self.assertTrue(score['exact_match']); self.assertEqual(TARGET, original)
        self.assertFalse(score['task_verified']); self.assertFalse(score['execution_eligible'])

    def test_valid_json_and_correct_type_do_not_hide_wrong_coordinate(self):
        score = scored('{"actions":[{"type":"click","point":[80,90]}]}')['score']
        self.assertTrue(score['schema_valid']); self.assertTrue(score['action_types_match']); self.assertTrue(score['parameters_match'])
        self.assertFalse(score['annotation_match']); self.assertEqual(score['error_category'], 'coordinate_mismatch')

    def test_correct_point_does_not_hide_wrong_button_or_click_count(self):
        for extra in [{'button': 'right'}, {'clicks': 2}]:
            response = json.dumps({'actions': [{'type': 'click', 'point': [30, 40], **extra}]})
            score = scored(response)['score']
            self.assertTrue(score['pointer_targets_match']); self.assertFalse(score['parameters_match']); self.assertFalse(score['annotation_match'])

    def test_text_whitespace_unicode_and_case_are_not_silently_normalized(self):
        label = {'actions': [{'type': 'type_text', 'text': 'A \u4e2d\u6587 '}]}
        for text in ['A \u4e2d\u6587', 'a \u4e2d\u6587 ']:
            score = score_response(json.dumps({'actions': [{'type': 'type_text', 'text': text}]}), label, 100, 100)
            self.assertFalse(score['annotation_match']); self.assertIsNone(score['pointer_targets_match'])

    def test_action_order_and_count_are_part_of_the_result(self):
        label = {'actions': [{'type': 'type_text', 'text': 'x'}, {'type': 'press', 'key': 'enter'}]}
        reversed_score = score_response(json.dumps({'actions': label['actions'][::-1]}), label, 100, 100)
        missing = score_response(json.dumps({'actions': label['actions'][:1]}), label, 100, 100)
        self.assertEqual(reversed_score['error_category'], 'action_type_mismatch'); self.assertEqual(missing['error_category'], 'action_count_mismatch')

    def test_key_aliases_and_hotkey_order_have_declared_equivalence(self):
        label = {'actions': [{'type': 'hotkey', 'keys': ['ctrl', 'a']}, {'type': 'press', 'key': 'enter'}]}
        response = {'actions': [{'type': 'hotkey', 'keys': ['A', 'Control']}, {'type': 'press', 'key': 'Return'}]}
        self.assertTrue(score_response(json.dumps(response), label, 100, 100)['annotation_match'])

    def test_tolerance_uses_original_pixels_and_both_drag_endpoints(self):
        label = {'actions': [{'type': 'drag', 'from': [0, 0], 'to': [80, 80]}]}
        good = {'actions': [{'type': 'drag', 'from': [6, 8], 'to': [80, 90]}]}
        self.assertTrue(score_response(json.dumps(good), label, 100, 100)['annotation_match'])
        good['actions'][0]['to'][1] = 91
        score = score_response(json.dumps(good), label, 100, 100)
        self.assertFalse(score['annotation_match']); self.assertEqual(score['pointer_points_correct'], 1)

    def test_failed_generations_remain_in_all_denominators(self):
        rows = [scored(), scored('not JSON'), scored('', generation_error={'type': 'OOM'})]
        metrics = aggregate(rows)['metrics']
        self.assertEqual(metrics['annotation_match']['total'], 3); self.assertEqual(metrics['annotation_match']['correct'], 1)
        self.assertEqual(metrics['pointer_points']['total'], 3); self.assertEqual(metrics['pointer_points']['correct'], 1)

    def test_no_pointer_metric_is_not_reported_as_perfect_accuracy(self):
        label = {'actions': [{'type': 'press', 'key': 'enter'}]}
        summary = aggregate([scored(json.dumps(label), target=label)])
        self.assertIsNone(summary['metrics']['pointer_points']['rate']); self.assertIsNone(summary['real_gui_success_rate'])

    def test_bad_reference_labels_fail_instead_of_making_false_negatives(self):
        with self.assertRaises(DataError): score_response('{}', {'actions': [{'type': 'click', 'point': [200, 0]}]}, 100, 100)


class ProtocolTests(unittest.TestCase):
    def validation_rows(self):
        return [scored(sample_id=identifier, prompt_variant=prompt) for prompt in PROMPTS for identifier in ('v1', 'v2')]

    def test_label_history_and_source_prompt_never_enter_model_prompt(self):
        sample = {'id': 'x', 'split': 'validation', 'instruction': 'Do a task', 'image': {'width': 100, 'height': 100},
                  'target': {'actions': [{'type': 'type_text', 'text': 'SECRET_LABEL'}]},
                  'source_prompt': 'SECRET_SOURCE', 'history': 'SECRET_HISTORY'}
        for prompt in PROMPTS:
            text = prompt_for(sample, prompt)
            for secret in ('SECRET_LABEL', 'SECRET_SOURCE', 'SECRET_HISTORY'): self.assertNotIn(secret, text)
        self.assertEqual(set(inference_sample(sample)), {'id', 'split', 'instruction', 'image'})

    def test_ties_keep_original_prompt(self):
        selection = select_prompt(self.validation_rows(), ['v1', 'v2'])
        self.assertEqual(selection['selected_prompt'], 'original'); self.assertFalse(selection['test_used_for_selection'])

    def test_selection_prioritizes_annotation_over_format(self):
        rows = self.validation_rows()
        for r in rows:
            if r['prompt_variant'] == 'original': r.update(scored('not JSON', sample_id=r['sample_id']))
        self.assertEqual(select_prompt(rows, ['v1', 'v2'])['selected_prompt'], 'schema_v1')

    def test_test_records_and_different_weights_cannot_select_prompt(self):
        for field, value in [('split', 'test'), ('variant', 'base')]:
            rows = self.validation_rows(); rows[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): select_prompt(rows, ['v1', 'v2'])

    def test_partial_duplicate_and_unknown_sample_coverage_rejected(self):
        rows = self.validation_rows()
        for changed in [rows[:-1], rows + [rows[0]], rows[:-1] + [scored(sample_id='unknown', prompt_variant='schema_v1')]]:
            with self.subTest(count=len(changed)), self.assertRaises(ValueError): select_prompt(changed, ['v1', 'v2'])

    def test_paired_comparison_rejects_different_inputs_or_decoding(self):
        rows = [scored(split='test', variant=name, sample_id='t1') for name in ('base', 'best', 'final')]
        self.assertTrue(paired_checks(rows, ['original'], ['t1']))
        for field, value in [('input_sha256', 'changed'), ('generation_config', {'max_new_tokens': 64})]:
            changed = deepcopy(rows); changed[-1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): paired_checks(changed, ['original'], ['t1'])

    def test_evidence_audit_rescores_raw_text_and_prompt_hash(self):
        row = scored(); self.assertTrue(verify_case(row, 10))
        for field, value in [('response', 'wrong'), ('prompt', 'changed')]:
            changed = deepcopy(row); changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): verify_case(changed, 10)

    def test_invalid_generation_and_tolerance_configs_are_rejected(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'configs/week3-wednesday.json').read_text())
        self.assertIs(validate_eval_config(config), config)
        for field, value in [('max_new_tokens', True), ('coordinate_tolerance_pixels', float('nan')),
                             ('coordinate_tolerance_pixels', -1), ('do_sample', True), ('num_beams', True)]:
            bad = {**config, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError): validate_eval_config(bad)


if __name__ == '__main__': unittest.main()
