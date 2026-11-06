"""Offline training-data, loss-mask and precision regressions; no model downloads."""

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image
import torch

from gui_data.pipeline import read_jsonl, write_jsonl
from gui_data.schema import DataError
from gui_training.data import audit_dataset, build_dataset, select_records, sha256
from gui_training.vlm import assistant_batch, image_prefix, inference_probe, user_prompt, validate_config, verify_model


def fixture(root, count=5):
    root = Path(root); (root / 'images').mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(count):
        image = Image.new('RGB', (20, 10), (i * 20, 50, 70)); path = root / f'images/{i}.png'; image.save(path)
        row = {'schema_version': '1.0', 'record_type': 'action', 'id': f'row{i}', 'group_id': f'task{i}', 'component_id': f'task{i}',
               'instruction': f'Find item {i}', 'source': {'name': 'screenagent', 'official_split': 'train',
                'revision': 'fixed-fixture-revision', 'origin': 'https://example.invalid/synthetic', 'raw_sha256': 'test-hash', 'license': 'test-fixture'},
               'observation': {'image': {'path': f'images/{i}.png', 'width': 20, 'height': 10,
                'coordinate_space': 'image_pixels', 'desktop_origin': None, 'sha256': sha256(path),
                'pixel_sha256': hashlib.sha256(image.tobytes()).hexdigest()},
                'context': {'source_prompt': 'UNTRUSTED ANSWER LEAK'}},
               'label': {'actions': [{'type': 'click', 'point': [10, 5], 'button': 'left', 'clicks': 1}]},
               'quality': {'coordinate_status': 'image_pixels_scaled_from_declared_screenshot', 'warnings': []},
               'split': 'validation' if i >= count - 2 else 'train', 'execution_eligible': False}
        rows.append(row)
    write_jsonl(root / 'records.jsonl', rows)
    for split, name in [('train', 'train.jsonl'), ('validation', 'validation.jsonl'), ('evaluation', 'evaluation_tasks.jsonl')]:
        write_jsonl(root / name, [r for r in rows if r['split'] == split])
    manifest = {'files': [{'path': p.relative_to(root).as_posix(), 'sha256': sha256(p)} for p in root.rglob('*') if p.is_file()]}
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return rows


def rehash(directory):
    path = Path(directory) / 'manifest.json'; value = json.loads(path.read_text())
    for item in value['files']: item['sha256'] = sha256(Path(directory) / item['path'])
    path.write_text(json.dumps(value), encoding='utf-8')


class TrainingDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.source = self.root / 'source'
        self.rows = fixture(self.source)

    def test_fixed_splits_are_order_independent_and_preserve_heldout(self):
        a, _ = select_records(self.rows); b, _ = select_records(list(reversed(self.rows)))
        self.assertEqual(a, b)
        self.assertEqual({r['split'] for r in a}, {'train', 'validation', 'test'})
        self.assertTrue(all(r['split'] != 'train' for r in a if r['source_split'] != 'train'))

    def test_excluded_records_can_bridge_two_action_tasks(self):
        rows = deepcopy(self.rows)
        for i in (0, 1):
            r = deepcopy(rows[i]); r.update(id='bridge' + str(i), group_id='bridge', record_type='plan')
            r['label']['actions'] = [{'type': 'plan', 'text': 'Synthetic link only'}]
            rows.append(r)
        selected, excluded = select_records(rows)
        by_id = {r['id']: r for r in selected}
        self.assertEqual(by_id['row0']['component_id'], by_id['row1']['component_id'])
        self.assertEqual(by_id['row0']['split'], by_id['row1']['split'])
        self.assertEqual(len(excluded), 2)

    def test_dom_targets_are_excluded_without_fabricating_points(self):
        rows = deepcopy(self.rows); r = deepcopy(rows[0]); r['id'] = 'dom'; r['source']['name'] = 'mind2web'
        r['label']['actions'] = [{'type': 'click', 'target': {'kind': 'dom_candidates'}}]; rows.append(r)
        selected, excluded = select_records(rows)
        self.assertNotIn('dom', [r['id'] for r in selected])
        self.assertEqual(excluded[-1]['reason'], 'unverified_dom_grounding')

    def test_source_prompts_and_verification_claims_are_not_training_inputs(self):
        selected, _ = select_records(self.rows)
        for row in selected:
            self.assertNotIn('context', row); self.assertNotIn('source_prompt', row)
            self.assertFalse(row['execution_eligible']); self.assertFalse(row['independent_postcondition_available'])

    def test_fewer_than_three_independent_groups_is_rejected(self):
        with self.assertRaises(DataError) as error: select_records(self.rows[:2])
        self.assertEqual(error.exception.code, 'insufficient_groups')

    def test_real_output_build_audit_and_byte_reproduction(self):
        a, b = self.root / 'a', self.root / 'b'
        first = build_dataset(self.source, a); second = build_dataset(self.source, b)
        self.assertTrue(first['audit']['passed']); self.assertEqual(first['counts'], second['counts'])
        for name in ['records.jsonl', 'train.jsonl', 'validation.jsonl', 'test.jsonl', 'excluded.jsonl']:
            self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())

    def test_image_tampering_is_detected(self):
        output = self.root / 'out'; build_dataset(self.source, output)
        next((output / 'images').glob('*.png')).write_bytes(b'changed')
        with self.assertRaises(DataError) as error: audit_dataset(output)
        self.assertEqual(error.exception.code, 'training_hash_mismatch')

    def test_group_leakage_is_detected_even_with_updated_file_hashes(self):
        output = self.root / 'out'; build_dataset(self.source, output)
        rows = read_jsonl(output / 'records.jsonl'); train = next(r for r in rows if r['split'] == 'train')
        other = next(r for r in rows if r['split'] != 'train'); other['group_id'] = train['group_id']
        write_jsonl(output / 'records.jsonl', rows)
        for split in ['train', 'validation', 'test']: write_jsonl(output / (split + '.jsonl'), [r for r in rows if r['split'] == split])
        rehash(output)
        with self.assertRaises(DataError) as error: audit_dataset(output)
        self.assertEqual(error.exception.code, 'task_leakage')

    def test_split_file_omissions_are_detected(self):
        output = self.root / 'out'; build_dataset(self.source, output)
        (output / 'test.jsonl').write_text('', encoding='utf-8'); rehash(output)
        with self.assertRaises(DataError) as error: audit_dataset(output)
        self.assertEqual(error.exception.code, 'split_content_mismatch')

    def test_same_image_cannot_cross_splits_under_different_task_ids(self):
        output = self.root / 'out'; build_dataset(self.source, output)
        rows = read_jsonl(output / 'records.jsonl'); train = next(r for r in rows if r['split'] == 'train')
        other = next(r for r in rows if r['split'] != 'train'); other['image'] = deepcopy(train['image'])
        write_jsonl(output / 'records.jsonl', rows)
        for split in ['train', 'validation', 'test']: write_jsonl(output / (split + '.jsonl'), [r for r in rows if r['split'] == split])
        rehash(output)
        with self.assertRaises(DataError) as error: audit_dataset(output)
        self.assertEqual(error.exception.code, 'image_leakage')

    def test_out_of_bounds_training_label_is_not_silently_accepted(self):
        output = self.root / 'out'; build_dataset(self.source, output)
        rows = read_jsonl(output / 'records.jsonl'); rows[0]['target']['actions'][0]['point'] = [9999, 5]
        write_jsonl(output / 'records.jsonl', rows)
        for split in ['train', 'validation', 'test']: write_jsonl(output / (split + '.jsonl'), [r for r in rows if r['split'] == split])
        rehash(output)
        with self.assertRaises(DataError) as error: audit_dataset(output)
        self.assertEqual(error.exception.code, 'out_of_bounds')


class FakeProcessor:
    def __init__(self):
        self.tokenizer = SimpleNamespace(encode=lambda text, **kw: [ord(c) + 20 for c in text], eos_token_id=2)
        self.last_text = None
    def apply_chat_template(self, messages, **kwargs):
        return 'User image ' + messages[0]['content'][1]['text'] + '\nAssistant:'
    def __call__(self, text, images, **kwargs):
        self.last_text = text
        return {'input_ids': torch.tensor([[5, 7, 7, 2]]), 'attention_mask': torch.ones((1, 4), dtype=torch.long),
                'pixel_values': torch.ones((1, 1, 3, 2, 2))}


class AssistantMaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sample = select_records(fixture(self.root))[0][0]
        self.processor = FakeProcessor()

    def test_all_prefix_and_image_labels_are_ignored(self):
        batch, audit = assistant_batch(self.processor, self.sample, self.root)
        self.assertTrue((batch['labels'][:, :4] == -100).all())
        self.assertTrue(torch.equal(batch['labels'][:, 4:], batch['input_ids'][:, 4:]))
        self.assertTrue(audit['all_prompt_and_image_labels_ignored'])

    def test_eos_is_supervised_even_if_the_prompt_uses_the_same_token(self):
        batch, _ = assistant_batch(self.processor, self.sample, self.root)
        self.assertEqual(batch['labels'][0, -1].item(), 2)
        self.assertEqual(batch['labels'][0, 3].item(), -100)

    def test_answer_and_untrusted_context_are_absent_from_inference_prefix(self):
        self.sample['target']['actions'] = [{'type': 'type_text', 'text': 'GROUND_TRUTH_ONLY'}]
        prefix, text = image_prefix(self.processor, self.sample, self.root)
        self.assertNotIn('GROUND_TRUTH_ONLY', text); self.assertNotIn('UNTRUSTED ANSWER LEAK', text)
        self.assertNotIn('labels', prefix)

    def test_unicode_and_significant_whitespace_survive_label_encoding(self):
        value = '  \u4e2d\u6587\nA  B '
        self.sample['target'] = {'actions': [{'type': 'type_text', 'text': value}]}
        batch, _ = assistant_batch(self.processor, self.sample, self.root)
        decoded = ''.join(chr(x - 20) for x in batch['labels'][0, 4:-1].tolist())
        self.assertEqual(json.loads(decoded)['actions'][0]['text'], value)

    def test_long_sequences_are_rejected_without_truncating_visual_tokens(self):
        with self.assertRaises(DataError) as error: assistant_batch(self.processor, self.sample, self.root, max_length=5)
        self.assertEqual(error.exception.code, 'sequence_too_long')

    def test_processor_without_images_is_rejected(self):
        class Missing(FakeProcessor):
            def __call__(self, **kw):
                result = super().__call__(**kw); result.pop('pixel_values'); return result
        with self.assertRaises(DataError) as error: image_prefix(Missing(), self.sample, self.root)
        self.assertEqual(error.exception.code, 'missing_visual_input')


class LocalModelContractTests(unittest.TestCase):
    def test_model_file_tampering_is_detected_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / 'model.safetensors').write_bytes(b'original')
            manifest = {'model_id': 'fixture', 'revision': 'fixed', 'upstream_hashes_verified': True,
                        'files': [{'path': 'model.safetensors', 'sha256': sha256(root / 'model.safetensors')}]}
            (root / 'download-manifest.json').write_text(json.dumps(manifest))
            verify_model(root, {'model_id': 'fixture', 'model_revision': 'fixed'})
            (root / 'model.safetensors').write_bytes(b'tampered')
            with self.assertRaises(DataError) as error: verify_model(root, {'model_id': 'fixture', 'model_revision': 'fixed'})
            self.assertEqual(error.exception.code, 'model_hash_mismatch')

    def test_revision_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'download-manifest.json').write_text(json.dumps({'model_id': 'fixture', 'revision': 'a'}))
            with self.assertRaises(DataError) as error: verify_model(directory, {'model_id': 'fixture', 'model_revision': 'b'})
            self.assertEqual(error.exception.code, 'model_revision_mismatch')

    def test_cpu_fallback_and_extra_steps_are_not_silently_accepted(self):
        config = json.loads((Path(__file__).resolve().parents[1] / 'configs/week3-monday.json').read_text())
        for change in [{'device': 'cpu'}, {'optimizer_steps': 2}, {'do_image_splitting': True}]:
            with self.subTest(change=change), self.assertRaises(ValueError): validate_config({**config, **change})

    def test_live_and_reloaded_probes_use_identical_autocast(self):
        torch.manual_seed(7)
        linear = torch.nn.Linear(4, 4)
        x = torch.randn(1, 3, 4)
        class Model(torch.nn.Module):
            def __init__(self, wrapped): super().__init__(); self.wrapped = wrapped
            def forward(self, inputs, **kw):
                if self.wrapped:
                    with torch.autocast('cpu', dtype=torch.bfloat16): logits = linear(inputs)
                else: logits = linear(inputs)
                return SimpleNamespace(logits=logits)
        class Precision:
            def __init__(self): self.calls = 0
            @contextmanager
            def autocast(self):
                self.calls += 1
                with torch.autocast('cpu', dtype=torch.bfloat16): yield
        policy = Precision()
        first = inference_probe(Model(True), {'inputs': x}, policy)
        second = inference_probe(Model(False), {'inputs': x}, policy)
        self.assertTrue(torch.equal(first, second)); self.assertEqual(policy.calls, 2)


if __name__ == '__main__': unittest.main()
