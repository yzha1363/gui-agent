"""Offline data tests; fixtures are synthetic, downloaded data is audited separately."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from gui_data.acquire import dump
from gui_data.adapters import mind2web, screenagent, webarena
from gui_data.pipeline import audit, build, digest, split_records
from gui_data.schema import DataError, prepare_image, safe_path, validate_record


def sources():
    return {'sources': {name: {'revision': 'pinned-revision', 'official_split': 'benchmark' if name == 'webarena' else 'train',
                               'license': 'test-fixture'} for name in ['screenagent', 'mind2web', 'webarena']}}


def entry(name='screenagent', raw='sample.json', image='image.png'):
    return {'source': name, 'raw_path': raw, 'image_path': image, 'raw_sha256': 'raw-hash',
            'origin': 'https://example.invalid/test-fixture', 'row_index': 0}


def screen(actions=None, session='task1'):
    return {'task_prompt': '  test task  ', 'send_prompt': 'untrusted source context',
            'session_id': session, 'video_width': 200, 'video_height': 100,
            'actions': actions if actions is not None else [{'action_type': 'MouseAction', 'mouse_action_type': 'click',
                                                            'mouse_position': {'width': 100, 'height': 50}}]}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        Image.new('RGB', (200, 100), 'white').save(self.root / 'image.png')
        self.out = self.root / 'out'

    def tearDown(self):
        self.temp.cleanup()

    def convert(self, raw):
        return screenagent(raw, entry(), sources(), self.root, self.out, 100)

    def test_resize_maps_source_point_to_image_coordinates(self):
        record = self.convert(screen())
        self.assertEqual(record['label']['actions'][0]['point'], [50, 25])
        self.assertEqual(record['observation']['image']['scale_xy'], [.5, .5])
        self.assertIsNone(record['observation']['image']['desktop_origin'])
        self.assertFalse(record['execution_eligible'])

    def test_typing_keeps_chinese_spaces_and_newlines(self):
        value = '  中文\nA  B '
        record = self.convert(screen([{'action_type': 'KeyboardAction', 'keyboard_action_type': 'text', 'keyboard_text': value}]))
        self.assertEqual(record['label']['actions'][0]['text'], value)

    def test_drag_start_comes_from_prior_move(self):
        raw = screen([{'action_type': 'MouseAction', 'mouse_action_type': 'move', 'mouse_position': {'width': 10, 'height': 20}},
                      {'action_type': 'MouseAction', 'mouse_action_type': 'drag', 'mouse_position': {'width': 100, 'height': 60}}])
        action = self.convert(raw)['label']['actions'][1]
        self.assertEqual(action['from'], [5, 10])
        self.assertEqual(action['to'], [50, 30])

    def test_drag_without_start_is_rejected(self):
        raw = screen([{'action_type': 'MouseAction', 'mouse_action_type': 'drag', 'mouse_position': {'width': 100, 'height': 60}}])
        with self.assertRaisesRegex(DataError, 'preceding mouse point'):
            self.convert(raw)

    def test_out_of_bounds_is_not_clamped(self):
        raw = screen()
        raw['actions'][0]['mouse_position']['width'] = 200
        with self.assertRaises(DataError) as error:
            self.convert(raw)
        self.assertEqual(error.exception.code, 'out_of_bounds')

    def test_nan_coordinate_is_rejected(self):
        raw = screen()
        raw['actions'][0]['mouse_position']['height'] = float('nan')
        with self.assertRaises(DataError):
            self.convert(raw)

    def test_image_annotation_size_mismatch_is_rejected(self):
        raw = screen()
        raw['video_width'] = 999
        with self.assertRaises(DataError) as error:
            self.convert(raw)
        self.assertEqual(error.exception.code, 'image_size_mismatch')

    def test_unknown_action_is_not_silently_dropped(self):
        with self.assertRaises(DataError) as error:
            self.convert(screen([{'action_type': 'UnknownAction'}]))
        self.assertEqual(error.exception.code, 'unsupported_action')

    def test_missing_instruction_is_a_classified_data_error(self):
        raw = screen();raw['task_prompt'] = None
        with self.assertRaises(DataError) as error:
            self.convert(raw)
        self.assertEqual(error.exception.code, 'missing_instruction')

    def test_empty_plan_label_is_rejected(self):
        with self.assertRaises(DataError) as error:
            self.convert(screen([{'action_type': 'PlanAction', 'element': '   '}]))
        self.assertEqual(error.exception.code, 'invalid_plan')

    def test_reflection_does_not_become_verified_postcondition(self):
        r = self.convert(screen([{'action_type': 'EvaluateSubTaskAction', 'situation': 'sub_task_success'}]))
        self.assertEqual(r['record_type'], 'reflection')
        self.assertFalse(r['verification']['independent_postcondition_available'])

    def mind(self):
        return {'confirmed_task': 'web task', 'annotation_id': 'episode1',
                'screenshot': {'width': 200, 'height': 100}, 'website': 'fixture',
                'target_action_index': '1', 'action_reprs': ['past', 'target', 'future'],
                'target_action_reprs': 'target', 'operation': json.dumps({'op': 'CLICK', 'original_op': 'CLICK', 'value': ''}),
                'pos_candidates': [json.dumps({'tag': 'button', 'backend_node_id': '123',
                                               'attributes': json.dumps({'bounding_box_rect': '10,20,30,40'})})]}

    def test_mind2web_context_excludes_current_and_future_labels(self):
        r = mind2web(self.mind(), entry('mind2web'), sources(), self.root, self.out, 100)
        self.assertEqual(r['observation']['context']['history_before'], ['past'])
        self.assertNotIn('target_action_reprs', r['observation'])

    def test_dom_rect_does_not_become_desktop_or_pixel_click(self):
        r = mind2web(self.mind(), entry('mind2web'), sources(), self.root, self.out, 100)
        action = r['label']['actions'][0]
        self.assertNotIn('point', action)
        candidate = action['target']['candidates'][0]
        self.assertEqual(candidate['source_bbox_xywh'], [10, 20, 30, 40])
        self.assertEqual(candidate['coordinate_space'], 'source_css_rect_unverified')

    def test_original_hover_not_misrepresented_as_click(self):
        raw = self.mind()
        raw['operation'] = {'op': 'CLICK', 'original_op': 'HOVER', 'value': ''}
        r = mind2web(raw, entry('mind2web'), sources(), self.root, self.out, 100)
        self.assertEqual(r['label']['actions'][0]['type'], 'hover')

    def test_webarena_has_no_fabricated_screenshot_or_actions(self):
        raw = {'task_id': 1, 'intent': 'test', 'sites': ['shop'], 'start_url': '__SHOPPING__', 'eval': {'secret_gold': 'answer'}}
        r = webarena(raw, entry('webarena', image=None), sources(), self.root, self.out, 100)
        self.assertIsNone(r['observation']['image'])
        self.assertEqual(r['label']['actions'], [])
        self.assertNotIn('eval', r['observation']['context'])
        r['split'] = 'train'
        with self.assertRaises(DataError):
            validate_record(r)


# Keep fixture reuse separate from unittest inheritance to avoid double counting.
class DataPipelineTests(unittest.TestCase):
    setUp = AdapterTests.setUp
    tearDown = AdapterTests.tearDown
    convert = AdapterTests.convert

    def fixture_records(self):
        rows = []
        for i in range(4):
            row = self.convert(screen(session=str(i)))
            row['id'] = str(i)
            row['observation']['image']['pixel_sha256'] = str(i)
            rows.append(row)
        return rows

    def test_task_and_shared_images_never_cross_splits(self):
        rows = self.fixture_records()
        rows[1]['observation']['image']['pixel_sha256'] = rows[0]['observation']['image']['pixel_sha256']
        # Retain a distinct label so this pair exercises shared-image linkage.
        rows[1]['label']['actions'][0]['point'] = [10, 10]
        result, _, _ = split_records(rows)
        by_id = {r['id']: r for r in result}
        self.assertEqual(by_id['0']['split'], by_id['1']['split'])

    def test_split_is_reproducible_when_input_order_changes(self):
        rows = self.fixture_records()
        a = split_records(deepcopy(rows))[0]
        b = split_records(list(reversed(deepcopy(rows))))[0]
        self.assertEqual({r['id']: r['split'] for r in a}, {r['id']: r['split'] for r in b})

    def test_dedup_does_not_erase_task_link(self):
        rows = self.fixture_records()
        rows[1]['observation']['image']['pixel_sha256'] = rows[0]['observation']['image']['pixel_sha256']
        extra = deepcopy(rows[1]);extra['id'] = 'extra';extra['observation']['image']['pixel_sha256'] = 'extra-image'
        result, duplicates, _ = split_records(rows + [extra])
        self.assertEqual(len(duplicates), 1)
        by_id = {r['id']: r for r in result}
        self.assertEqual(by_id['0']['split'], by_id['extra']['split'])

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(DataError):
            safe_path(self.root, '../outside.png')

    def bundle(self, missing=False):
        manifest = sources();manifest.update(downloads=[], records=[])
        for i, color in enumerate(['red', 'green', 'blue']):
            image = f'{i}.png';Image.new('RGB', (200, 100), color).save(self.root / image)
            raw_path = f'{i}.json';dump(self.root / raw_path, screen(session=str(i)))
            e = entry(raw=raw_path, image=image);e['raw_sha256'] = digest(self.root / raw_path)
            manifest['records'].append(e)
        if missing:
            e = deepcopy(manifest['records'][0]);e['image_path'] = 'missing.png';manifest['records'].append(e)
        dump(self.root / 'manifest.json', manifest)
        return manifest

    def test_full_pipeline_audits_jsonl_assets_and_splits(self):
        self.bundle()
        report = build(self.root, self.out, max_side=100)
        self.assertEqual(report['counts']['records'], 3)
        self.assertEqual(report['counts']['by_split'], {'train': 2, 'validation': 1})
        self.assertTrue(audit(self.out)['passed'])

    def test_missing_image_is_quarantined_with_reason(self):
        self.bundle(missing=True)
        report = build(self.root, self.out, max_side=100)
        self.assertEqual(report['counts']['rejection_reasons'], {'missing_image': 1})
        self.assertEqual(report['counts']['records'], 3)

    def test_corrupted_raw_hash_aborts_instead_of_becoming_data_rejection(self):
        self.bundle()
        (self.root / '0.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(DataError) as error:
            build(self.root, self.out, max_side=100)
        self.assertEqual(error.exception.code, 'raw_hash_mismatch')

    def test_prepared_image_tampering_is_detected(self):
        self.bundle();build(self.root, self.out, max_side=100)
        next((self.out / 'images').glob('*.png')).write_bytes(b'corrupt')
        with self.assertRaises(DataError) as error:
            audit(self.out)
        self.assertEqual(error.exception.code, 'prepared_hash_mismatch')
