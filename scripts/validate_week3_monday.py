"""Offline regression, dataset reproduction and audit of saved real GPU evidence."""

import argparse
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui_training.data import audit_dataset, build_dataset, sha256
from gui_training.vlm import verify_model
from scripts.package_project import source_files


def read(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/week3-monday.json')
    parser.add_argument('--preflight', type=Path, default=ROOT / 'output/week3-monday/preflight-corrected')
    parser.add_argument('--attempt', type=Path, action='append', default=[])
    parser.add_argument('--project-check', type=Path, required=True)
    args = parser.parse_args()
    output = ROOT / 'output/week3-monday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True)
    class Results(unittest.TextTestResult):
        def __init__(self, *a, **kw): super().__init__(*a, **kw); self.cases = []
        def addSuccess(self, test): super().addSuccess(test); self.cases.append({'case': test.id(), 'passed': True})
        def addFailure(self, test, error): super().addFailure(test, error); self.cases.append({'case': test.id(), 'passed': False})
        def addError(self, test, error): super().addError(test, error); self.cases.append({'case': test.id(), 'passed': False})
    stream = StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(
        unittest.defaultTestLoader.loadTestsFromName('tests.test_training'))
    (output / 'unit_tests.log').write_text(stream.getvalue(), encoding='utf-8')
    config = read(args.config); dataset = ROOT / config['dataset_dir']; manifest = read(dataset / 'manifest.json')
    audit = audit_dataset(dataset)
    reproduction = build_dataset(manifest['source'], output / 'reproduced-data', seed=manifest['seed'])
    same_files = all(sha256(dataset / entry['path']) == sha256(output / 'reproduced-data' / entry['path']) for entry in manifest['files'])
    verify_model(ROOT / config['model_dir'], config)
    summary = read(args.preflight / 'summary.json'); step = read(args.preflight / 'training-step.json')
    batch = read(args.preflight / 'batch-audit.json'); reload = read(args.preflight / 'reload-verification.json')
    baseline = read(args.preflight / 'baseline.json')
    checks = {'status_completed': summary['status'] == 'completed' and summary['success'],
              'real_model_and_one_step': summary['real_model_used'] and summary['optimizer_steps_completed'] == 1 and step['optimizer_steps'] == 1,
              'data_identity': summary['dataset_manifest_sha256'] == sha256(dataset / 'manifest.json'),
              'sample_is_training': summary['sample_split'] == 'train' and step['split'] == 'train' and reload['probe_split'] == 'train',
              'finite_positive_gradient': step['finite_gradients'] and step['gradient_norm_before_clip'] > 0,
              'frozen_base_no_grad': step['frozen_parameters_have_no_grad'],
              'lora_weights_changed': step['changed_lora_tensors'] > 0,
              'assistant_only_labels': batch['all_prompt_and_image_labels_ignored'] and batch['assistant_labels_equal_input_ids'] and batch['supervised_token_count'] > 0,
              'image_used': batch['image_token_count'] > 0 and baseline['vision_forward_calls'] > 0,
              'saved_adapter_equivalent': reload['lora_tensors_exactly_equal'] and reload['next_token_logits_close'],
              'adapter_hashes_verified': all(sha256(args.preflight / f['path']) == f['sha256'] for f in reload['adapter_files']),
              'no_desktop_input': summary['desktop_input_sent'] is False,
              'source_package_complete': {'gui_training/data.py', 'gui_training/vlm.py', 'gui_tasks/basic.py', 'configs/week3-monday.json'} <=
                  {p.relative_to(ROOT).as_posix() for p in source_files()}}
    project = read(args.project_check)
    attempts = []
    for path in args.attempt + [args.preflight]:
        value = read(path / 'summary.json')
        attempts.append({'summary': str(path / 'summary.json'), 'status': value['status'], 'success': value['success'],
                         'optimizer_steps_completed': value['optimizer_steps_completed'], 'error': value.get('error')})
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'directory': output.relative_to(ROOT).as_posix(),
              'unit_test_count': result.testsRun, 'unit_cases': result.cases, 'unit_tests_passed': result.wasSuccessful(),
              'dataset': str(dataset), 'dataset_counts': manifest['counts'], 'excluded_records': manifest['excluded_records'],
              'exclusion_reasons': manifest['exclusion_reasons'], 'data_audit': audit,
              'reproduction_same_files': same_files and reproduction['audit']['passed'],
              'preflight_summary': str(args.preflight / 'summary.json'), 'preflight_checks': checks, 'preflight_attempts': attempts,
              'local_model_preflight': 'passed' if all(checks.values()) else 'failed',
              'baseline_action_json_valid': baseline['action_json_format_valid'], 'model_quality_evaluation': 'pending', 'nim_validation': 'pending',
              'training_step': {k: step[k] for k in ['loss', 'gradient_norm_before_clip', 'trainable_parameters', 'changed_lora_tensors', 'peak_allocated_mib', 'peak_reserved_mib']},
              'project_check': str(args.project_check), 'full_project_passed': project['all_passed'],
              'full_project_tests': next(c['test_count'] for c in project['checks'] if c['name'] == 'unit_tests')}
    report['all_passed'] = result.wasSuccessful() and audit['passed'] and same_files and all(checks.values()) and project['all_passed']
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (output / 'verification.json').write_text(encoded, encoding='utf-8')
    (ROOT / 'output/week3-monday/verification.json').write_text(encoded, encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('all_passed', 'unit_test_count', 'full_project_tests', 'local_model_preflight', 'baseline_action_json_valid', 'directory')}))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
