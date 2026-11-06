"""Audit saved GPU comparisons without generating again or operating a desktop."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui_data.pipeline import read_jsonl
from gui_data.schema import safe_path
from gui_training.data import audit_dataset, sha256
from gui_training.evaluate_run import read_json, verify_training, write_json
from gui_training.evaluation import (PROMPTS, VARIANTS, aggregate, digest, inference_sample, paired_checks,
                                     prompt_for, select_prompt, verify_case)


def audit(run, project_check):
    run = Path(run); summary = read_json(run / 'summary.json'); config = read_json(run / 'config.json')
    training = read_json(ROOT / config['training_config']); dataset = ROOT / training['dataset_dir']
    plan = read_json(run / 'evaluation-plan.json'); selection = read_json(run / 'selection.json')
    manifest = read_json(run / 'evidence-manifest.json')
    declared = {f['path'] for f in manifest['files']}
    if len(declared) != len(manifest['files']): raise ValueError('Duplicate inventory paths.')
    required = {'config.json', 'training-provenance.json', 'environment.json', 'evaluation-plan.json', 'selection.json', 'validation.jsonl', 'test.jsonl'}
    checks = {'inventory_complete': required <= declared,
              'all_saved_hashes_match': all(sha256(safe_path(run, f['path'])) == f['sha256'] for f in manifest['files']),
              'dataset_audit': audit_dataset(dataset)['passed'],
              'training_provenance': verify_training(ROOT, config, training, dataset) == read_json(run / 'training-provenance.json'),
              'plan_config_match': plan['config'] == config,
              'source_code_hashes_match': all(sha256(ROOT / name) == expected for name, expected in plan['code_hashes'].items()),
              'frozen_plan_hash': summary['plan_sha256'] == selection['plan_sha256'] == sha256(run / 'evaluation-plan.json'),
              'frozen_selection_hash': summary['selection_sha256'] == sha256(run / 'selection.json'),
              'validation_log_hash': selection['validation_results_sha256'] == sha256(run / 'validation.jsonl')}
    validation = read_jsonl(run / 'validation.jsonl'); tests = read_jsonl(run / 'test.jsonl'); rows = validation + tests
    reference = {split: {r['id']: r for r in read_jsonl(dataset / (split + '.jsonl'))} for split in ('validation', 'test')}
    expected_selection = select_prompt(validation, reference['validation'])
    checks['selection_recomputed_on_validation_only'] = all(selection[k] == v for k, v in expected_selection.items())
    prompts = list(dict.fromkeys([config['primary_prompt'], selection['selected_prompt']]))
    checks['paired_test_inputs_and_settings'] = paired_checks(tests, prompts, reference['test'])
    from transformers import AutoProcessor
    processor = AutoProcessor.from_pretrained(ROOT / training['model_dir'], local_files_only=True, trust_remote_code=False, use_fast=False)
    processor.image_processor.size = {'longest_edge': training['image_longest_edge']}
    processor.image_processor.do_image_splitting = training['do_image_splitting']
    import torch
    checks['scores_recomputed'] = all(verify_case(r, config['coordinate_tolerance_pixels']) for r in rows)
    for row in rows:
        sample = reference[row['split']][row['sample_id']]
        if row['target'] != sample['target'] or row['image'] != sample['image']: raise ValueError('Reference or image identity changed.')
        expected_text = processor.apply_chat_template([{'role': 'user', 'content': [
            {'type': 'image'}, {'type': 'text', 'text': prompt_for(inference_sample(sample), row['prompt_variant'])}]}],
            add_generation_prompt=True, tokenize=False)
        if row['prompt'] != expected_text: raise ValueError('Prompt differs from frozen public input.')
        if processor.tokenizer.decode(row['generated_token_ids'], skip_special_tokens=True) != row['response']:
            raise ValueError('Raw decoded response and tokens differ.')
        result_path = safe_path(run, row['result_path'])
        image_path = result_path.parent / 'input.png'
        if read_json(result_path) != row or row['result_path'] not in declared or image_path.relative_to(run).as_posix() not in declared:
            raise ValueError('Per-case evidence differs from aggregate log or inventory.')
        from PIL import Image
        with Image.open(image_path) as saved, Image.open(safe_path(dataset, sample['image']['path'])) as original:
            if saved.size != original.size or saved.convert('RGB').tobytes() != original.convert('RGB').tobytes():
                raise ValueError('Saved screenshot differs from fixed dataset image.')
            inputs = dict(processor(text=expected_text, images=[original.convert('RGB')], return_tensors='pt'))
        input_hash = digest({name: {'shape': list(value.shape), 'dtype': str(value.dtype),
            'sha256': hashlib.sha256(value.contiguous().view(torch.uint8).numpy().tobytes()).hexdigest()}
            for name, value in inputs.items()})
        if input_hash != row['input_sha256'] or inputs['input_ids'].shape[1] != row['prompt_tokens']:
            raise ValueError('Actual input tensor hash differs from fixed processor output.')
        if row['new_tokens'] != len(row['generated_token_ids']) or row['new_tokens'] < 1: raise ValueError('No real generated tokens.')
        if row['vision_forward_calls'] < 1 or row['trainable_parameters'] != 0: raise ValueError('Missing visual call or unfrozen inference weights.')
        if row['variant'] != 'base' and row['adapter_tensors_verified'] is not True: raise ValueError('Adapter was not verified.')
        generation = row['generation_config']
        if any(generation[k] != config[k] for k in ('max_new_tokens', 'do_sample', 'num_beams')): raise ValueError('Decoding differs from frozen config.')
    checks['references_prompts_tokens_screenshots_and_readonly_weights'] = True
    checks['no_generated_errors'] = all(r['generation_error'] is None for r in rows)
    actual_case_files = {p.relative_to(run).as_posix() for p in (run / 'cases').rglob('result.json')}
    checks['no_hidden_or_dropped_cases'] = actual_case_files == {r['result_path'] for r in rows}
    checks['no_live_execution_claim'] = summary['desktop_input_sent'] is False and summary['real_gui_success_rate'] is None
    checks['completed'] = summary['status'] == 'completed' and summary['success']
    recomputed = {variant: {prompt: aggregate([r for r in tests if r['variant'] == variant and r['prompt_variant'] == prompt])
                            for prompt in prompts} for variant in VARIANTS}
    checks['summary_metrics_recomputed'] = recomputed == summary['comparison']
    checks['generation_counts'] = summary['validation_generation_count'] == len(validation) and summary['test_generation_count'] == len(tests)
    project = read_json(project_check)
    checks['full_project_passed'] = project['all_passed']
    log_path = next(c['log'] for c in project['checks'] if c['name'] == 'unit_tests')
    unit_log = (ROOT / log_path).read_text(encoding='utf-8')
    checks['evaluation_tests_in_project_check'] = unit_log.count('(test_lora_evaluation.') == 25
    count = next(c['test_count'] for c in project['checks'] if c['name'] == 'unit_tests')
    return {'kind': 'audit_of_real_gpu_outputs', 'run': run.relative_to(ROOT).as_posix(), 'checks': checks,
            'validation_generations': len(validation), 'test_generations': len(tests),
            'prompt_selection': expected_selection, 'comparison': recomputed,
            'failure_categories': dict(Counter(r['score']['error_category'] for r in rows if r['score']['error_category'])),
            'new_unit_tests': 25, 'full_project_tests': count, 'project_check': Path(project_check).relative_to(ROOT).as_posix(),
            'offline_evaluation_completed': True, 'real_gui_success_rate': None, 'nim_validation': 'pending',
            'all_passed': all(checks.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=ROOT / 'output/week3-wednesday/comparison-initial')
    parser.add_argument('--project-check', type=Path, required=True)
    args = parser.parse_args()
    directory = ROOT / 'output/week3-wednesday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    directory.mkdir(parents=True)
    try: result = audit(args.run.resolve(), args.project_check.resolve())
    except (Exception, KeyboardInterrupt) as exc:
        result = {'all_passed': False, 'error': {'type': type(exc).__name__, 'message': str(exc)}}
    result.update(created_at_utc=datetime.now(timezone.utc).isoformat(), directory=directory.relative_to(ROOT).as_posix())
    write_json(directory / 'verification.json', result); write_json(ROOT / 'output/week3-wednesday/verification.json', result)
    print(json.dumps({'all_passed': result['all_passed'], 'directory': result['directory'], 'error': result.get('error')}, ensure_ascii=True))
    return 0 if result['all_passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
