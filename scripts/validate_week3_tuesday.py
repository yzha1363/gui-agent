"""Audit multi-example GPU training, adapter reload and cross-process recovery."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from io import StringIO
import json
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import torch
from safetensors.torch import load_file
from gui_data.pipeline import read_jsonl
from gui_training.data import audit_dataset, sha256
from gui_training.trainer import config_hash, inspect_checkpoint


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def tree_equal(first, second):
    if isinstance(first, torch.Tensor): return isinstance(second, torch.Tensor) and torch.equal(first, second)
    if isinstance(first, dict): return isinstance(second, dict) and first.keys() == second.keys() and all(tree_equal(first[k], second[k]) for k in first)
    if isinstance(first, (list, tuple)): return isinstance(second, type(first)) and len(first) == len(second) and all(tree_equal(a, b) for a, b in zip(first, second))
    return first == second


def audit_run(directory, config, dataset):
    directory = Path(directory); summary = read(directory / 'summary.json')
    train_ids = {r['id'] for r in read_jsonl(dataset / 'train.jsonl')}
    validation_ids = {r['id'] for r in read_jsonl(dataset / 'validation.jsonl')}
    test_ids = {r['id'] for r in read_jsonl(dataset / 'test.jsonl')}
    rows = read_jsonl(directory / 'train.jsonl'); validation = read_jsonl(directory / 'validation.jsonl')
    checks = {'completed': summary['status'] == 'completed' and summary['success'],
              'config_and_dataset_match': summary['config_sha256'] == config_hash(config) and summary['dataset_sha256'] == sha256(dataset / 'manifest.json'),
              'planned_steps': summary['global_step'] == config['epochs'] * math.ceil(len(train_ids) / config['gradient_accumulation_steps']) and
                    [r['step'] for r in rows] == list(range(1, summary['global_step'] + 1)),
              'all_losses_gradients_finite': all(math.isfinite(r['loss']) and math.isfinite(r['gradient_norm_before_clip']) and r['gradient_norm_before_clip'] > 0 for r in rows),
              'token_weighted_loss': all(abs(r['loss'] - sum(a*b for a,b in zip(r['example_losses'], r['example_token_counts'])) / r['supervised_tokens']) < 1e-10 for r in rows),
              'no_validation_or_test_in_training': all(set(r['sample_ids']) <= train_ids and not set(r['sample_ids']) & (validation_ids | test_ids) for r in rows),
              'validation_only': len(validation) == config['epochs'] + 1 and all({x['id'] for x in r['examples']} == validation_ids and r['split'] == 'validation' for r in validation),
              'no_desktop_or_fixed_test_model_use': summary['desktop_input_sent'] is False and summary['fixed_test_model_use'] is False,
              'changed_adapter': summary['changed_lora_tensors'] > 0}
    checks['each_epoch_covers_training_once'] = all(Counter(identifier for r in rows if r['epoch'] == epoch for identifier in r['sample_ids']) == Counter(train_ids)
                                                     for epoch in range(1, config['epochs'] + 1))
    checks['validation_token_mean'] = all(abs(r['loss'] - sum(x['loss'] * x['supervised_tokens'] for x in r['examples']) / r['supervised_tokens']) < 1e-10 for r in validation)
    audits = read(directory / 'batch-audits.json')
    checks['assistant_only_supervision'] = all(x['all_prompt_and_image_labels_ignored'] and x['assistant_labels_equal_input_ids'] and x['supervised_token_count'] > 0
                                                for split in ('train', 'validation') for x in audits[split])
    reloaded = read(directory / 'reload-verification.json')
    checks['reload_verified'] = reloaded['weights_exactly_equal'] and reloaded['logits_close'] and reloaded['probe_split'] == 'train'
    checks['adapter_hashes'] = all(sha256(directory / f['path']) == f['sha256'] for f in reloaded['adapter_hashes'])
    generated = read(directory / 'reloaded-generation.json')
    checks['generation_uses_training_only'] = generated['split'] == 'train' and generated['sample_id'] in train_ids and generated['new_tokens'] > 0
    inspect_checkpoint(summary['latest_checkpoint'], config_hash(config), summary['dataset_sha256'])
    return {'summary': str(directory / 'summary.json'), 'passed': all(checks.values()), 'checks': checks,
            'global_step': summary['global_step'], 'steps_this_process': summary['steps_this_process'],
            'initial_validation_loss': summary['initial_validation_loss'], 'final_validation_loss': summary['final_validation_loss'],
            'best_validation_loss': summary['best_validation_loss'], 'best_step': summary['best_step'],
            'peak_allocated_mib': summary['peak_allocated_mib'], 'peak_reserved_mib': summary['peak_reserved_mib'],
            'generated_action_json_valid': summary['reloaded_generation_json_valid']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', type=Path, default=ROOT / 'output/week3-tuesday/full-initial')
    parser.add_argument('--partial', type=Path, default=ROOT / 'output/week3-tuesday/recovery-partial')
    parser.add_argument('--resumed', type=Path, default=ROOT / 'output/week3-tuesday/recovery-resumed')
    parser.add_argument('--project-check', type=Path, required=True)
    args = parser.parse_args()
    output = ROOT / 'output/week3-tuesday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True)
    class Results(unittest.TextTestResult):
        def __init__(self, *a, **kw): super().__init__(*a, **kw); self.cases = []
        def addSuccess(self, test): super().addSuccess(test); self.cases.append({'case': test.id(), 'passed': True})
        def addFailure(self, test, error): super().addFailure(test, error); self.cases.append({'case': test.id(), 'passed': False})
        def addError(self, test, error): super().addError(test, error); self.cases.append({'case': test.id(), 'passed': False})
    stream = StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(
        unittest.defaultTestLoader.loadTestsFromName('tests.test_lora_trainer'))
    (output / 'unit_tests.log').write_text(stream.getvalue(), encoding='utf-8')
    config = read(ROOT / 'configs/week3-tuesday.json'); dataset = ROOT / config['dataset_dir']; audit = audit_dataset(dataset)
    full = audit_run(args.full, config, dataset); resumed = audit_run(args.resumed, config, dataset)
    first = read(args.full / 'summary.json'); second = read(args.resumed / 'summary.json'); partial = read(args.partial / 'summary.json')
    a = load_file(str(args.full / 'adapter/adapter_model.safetensors')); b = load_file(str(args.resumed / 'adapter/adapter_model.safetensors'))
    aa = read_jsonl(args.full / 'train.jsonl'); bb = read_jsonl(args.resumed / 'train.jsonl')
    end1, end2 = Path(first['latest_checkpoint']), Path(second['latest_checkpoint'])
    recovery = {'controlled_stop_at_step5': partial['status'] == 'stopped' and partial['global_step'] == 5,
                'resumed_at_step5_with_seven_new_steps': second.get('resumed_from_step') == 5 and second['steps_this_process'] == 7,
                'all_adapter_tensors_exact': tree_equal(a, b),
                'sample_order_exact': [x['sample_ids'] for x in aa] == [x['sample_ids'] for x in bb],
                'training_losses_exact': [x['loss'] for x in aa] == [x['loss'] for x in bb],
                'learning_rates_exact': [x['learning_rate'] for x in aa] == [x['learning_rate'] for x in bb],
                'optimizer_tensors_exact': tree_equal(torch.load(end1 / 'optimizer.pt', map_location='cpu', weights_only=True), torch.load(end2 / 'optimizer.pt', map_location='cpu', weights_only=True)),
                'torch_rng_exact': tree_equal(torch.load(end1 / 'rng.pt', map_location='cpu', weights_only=True), torch.load(end2 / 'rng.pt', map_location='cpu', weights_only=True)),
                'python_numpy_rng_exact': read(end1 / 'state.json')['rng'] == read(end2 / 'state.json')['rng'],
                'scheduler_exact': read(end1 / 'state.json')['scheduler'] == read(end2 / 'state.json')['scheduler']}
    recovery['best_adapter_exact'] = tree_equal(load_file(str(args.full / 'best_adapter/adapter_model.safetensors')), load_file(str(args.resumed / 'best_adapter/adapter_model.safetensors')))
    project = read(args.project_check)
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'directory': output.relative_to(ROOT).as_posix(),
              'unit_test_count': result.testsRun, 'unit_cases': result.cases, 'unit_tests_passed': result.wasSuccessful(),
              'data_audit': audit, 'config': 'configs/week3-tuesday.json', 'full_training': full, 'resumed_training': resumed,
              'controlled_stop_summary': str(args.partial / 'summary.json'), 'recovery_checks': recovery,
              'project_check': str(args.project_check), 'full_project_passed': project['all_passed'],
              'full_project_tests': next(c['test_count'] for c in project['checks'] if c['name'] == 'unit_tests'),
              'model_quality_evaluation': 'pending', 'nim_validation': 'pending', 'fixed_test_model_use': False}
    report['all_passed'] = result.wasSuccessful() and audit['passed'] and full['passed'] and resumed['passed'] and all(recovery.values()) and project['all_passed']
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (output / 'verification.json').write_text(encoded, encoding='utf-8'); (ROOT / 'output/week3-tuesday/verification.json').write_text(encoded, encoding='utf-8')
    print(json.dumps({'all_passed': report['all_passed'], 'unit_tests': result.testsRun, 'full_project_tests': report['full_project_tests'],
                      'recovery_checks': recovery, 'directory': report['directory']}))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
