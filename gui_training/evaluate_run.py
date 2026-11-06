"""Local, read-only GPU comparison; frozen validation decisions precede test use."""

from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import random
import time

from PIL import Image

from gui_data.pipeline import read_jsonl
from gui_data.schema import safe_path
from gui_training.data import audit_dataset, sha256
from gui_training.evaluation import (PROMPTS, VARIANTS, SCHEMA_INSTRUCTION, aggregate, digest, inference_sample,
                                     paired_checks, prompt_for, score_response, select_prompt, validate_eval_config)


def write_json(path, value):
    path = Path(path); pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    pending.replace(path)


def read_json(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def verify_training(root, config, training, dataset):
    """Bind both adapter exports to Tuesday's hashed, complete checkpoint."""
    from gui_training.trainer import config_hash, inspect_checkpoint
    run = safe_path(root, config['training_run']); report = read_json(run / 'summary.json')
    if report['status'] != 'completed' or not report['success'] or not report['adapter_reload_verified']:
        raise ValueError('Tuesday training/reload did not complete.')
    fingerprint = config_hash(training); dataset_hash = sha256(dataset / 'manifest.json')
    if report['config_sha256'] != fingerprint or report['dataset_sha256'] != dataset_hash:
        raise ValueError('Training configuration or dataset changed.')
    if report['model_revision'] != training['model_revision'] or report['model_id'] != training['model_id']:
        raise ValueError('Training model identity differs.')
    checkpoint = run / ('checkpoint-%04d' % report['global_step'])
    state = inspect_checkpoint(checkpoint, fingerprint, dataset_hash)
    if state['cursor']['global_step'] != report['global_step'] or state['best_step'] != report['best_step']:
        raise ValueError('Export and checkpoint step mismatch.')
    entries = {}
    for variant, folder, checkpoint_folder in [('best', 'best_adapter', 'best_adapter'), ('final', 'adapter', 'adapter')]:
        directory = run / folder
        for name in ('adapter_config.json', 'adapter_model.safetensors'):
            if sha256(directory / name) != sha256(checkpoint / checkpoint_folder / name):
                raise ValueError('Adapter export differs from verified checkpoint: ' + variant)
        entries[variant] = {'directory': directory.relative_to(root).as_posix(),
                            'step': report['best_step'] if variant == 'best' else report['global_step'],
                            'weights_sha256': sha256(directory / 'adapter_model.safetensors'),
                            'config_sha256': sha256(directory / 'adapter_config.json')}
    return {'training_summary_sha256': sha256(run / 'summary.json'), 'training_config_sha256': fingerprint,
            'dataset_sha256': dataset_hash, 'checkpoint': checkpoint.relative_to(root).as_posix(), 'adapters': entries}


def run_evaluation(root, config, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    begin = time.perf_counter()
    summary = {'status': 'running', 'success': False, 'kind': 'real_local_vlm_offline_comparison',
               'started_at_utc': datetime.now(timezone.utc).isoformat(), 'desktop_input_sent': False,
               'real_gui_success_rate': None, 'nim_validation': 'pending', 'test_used_for_prompt_selection': False,
               'validation_generation_count': 0, 'test_generation_count': 0}
    results = []

    def stage(name):
        summary['stage'] = name; write_json(output / 'summary.json', summary)
        print('Evaluation stage: ' + name, flush=True)

    try:
        validate_eval_config(config)
        training = read_json(safe_path(root, config['training_config']))
        if training['device'] != 'cuda' or training['dtype'] != 'bfloat16': raise ValueError('CUDA bf16 config required.')
        stage('auditing_frozen_artifacts')
        dataset = safe_path(root, training['dataset_dir']); model_dir = safe_path(root, training['model_dir'])
        data_audit = audit_dataset(dataset)
        from gui_training.vlm import verify_model
        model_manifest = verify_model(model_dir, training)
        provenance = verify_training(root, config, training, dataset)
        write_json(output / 'config.json', config); write_json(output / 'training-provenance.json', provenance)
        # This plan is written before any inference, including candidate prompt text and tie rules.
        plan = {'schema_version': '1.0', 'created_at_utc': datetime.now(timezone.utc).isoformat(), 'config': config,
                'model_id': training['model_id'], 'revision': training['model_revision'], 'provenance': provenance,
                'prompt_candidates': {'original': 'Unchanged gui_training.vlm.user_prompt', 'schema_v1': SCHEMA_INSTRUCTION},
                'primary_comparison': 'base/best/final on fixed test, original prompt',
                'secondary_comparison': 'same three weights with validation-selected prompt, only when different',
                'selection_rule': 'best adapter on validation only: annotation_match, schema_valid, fewer generation errors; tie original',
                'metric_rule': 'strict JSON, ordered action types, non-coordinate parameters and <=10 pixel point distance; no live success',
                'code_hashes': {name: sha256(root / name) for name in ('gui_training/evaluation.py', 'gui_training/evaluate_run.py', 'gui_training/vlm.py')},
                'fixed_test_read_policy': 'Dataset integrity audit may read labels; model inference and prompt selection do not use test until selection is frozen.'}
        write_json(output / 'evaluation-plan.json', plan); plan_hash = sha256(output / 'evaluation-plan.json')
        for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'): os.environ[key] = '1'
        os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
        import torch
        from peft import PeftModel, get_peft_model_state_dict
        from safetensors.torch import load_file
        from transformers import AutoModelForImageTextToText, AutoProcessor, GenerationConfig
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported(): raise RuntimeError('CUDA bf16 required; no CPU fallback.')
        random.seed(config['seed']); torch.manual_seed(config['seed']); torch.cuda.manual_seed_all(config['seed'])
        torch.use_deterministic_algorithms(True)
        torch.backends.cuda.matmul.allow_tf32 = False; torch.backends.cudnn.allow_tf32 = False; torch.backends.cudnn.benchmark = False
        write_json(output / 'environment.json', {'python': __import__('sys').version, 'gpu': torch.cuda.get_device_name(),
                   'versions': {name: importlib.metadata.version(name) for name in ('torch', 'transformers', 'peft', 'accelerate', 'safetensors')},
                   'model_files_verified': len(model_manifest['files']), 'network_in_model_run': False, 'deterministic_algorithms': True})
        processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False, use_fast=False)
        processor.image_processor.size = {'longest_edge': training['image_longest_edge']}
        processor.image_processor.do_image_splitting = training['do_image_splitting']
        eos = processor.tokenizer.eos_token_id
        pad = processor.tokenizer.pad_token_id
        generation_settings = {'max_new_tokens': config['max_new_tokens'], 'do_sample': False, 'num_beams': 1, 'use_cache': True,
                               'eos_token_id': eos, 'pad_token_id': pad if pad is not None else eos,
                               'bos_token_id': processor.tokenizer.bos_token_id}
        generation = GenerationConfig(**generation_settings)

        def generate_group(phase, variant, prompts, rows):
            stage(phase + '_' + variant)
            loaded_at = time.perf_counter()
            model = AutoModelForImageTextToText.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False,
                dtype=torch.bfloat16, attn_implementation=training['attention_implementation']).to('cuda')
            adapter_verified = None
            if variant != 'base':
                adapter = safe_path(root, provenance['adapters'][variant]['directory'])
                model = PeftModel.from_pretrained(model, adapter, is_trainable=False, local_files_only=True)
                saved = load_file(str(adapter / 'adapter_model.safetensors'))
                active = get_peft_model_state_dict(model)
                adapter_verified = set(saved) == set(active) and all(torch.equal(saved[k], active[k].detach().cpu()) for k in saved)
                if not adapter_verified: raise ValueError('Loaded adapter tensors differ from export.')
                del saved, active
            model.requires_grad_(False); model.eval(); torch.cuda.synchronize()
            load_seconds = time.perf_counter() - loaded_at
            base = model if variant == 'base' else model.get_base_model()
            vision_calls = []
            hook = base.model.vision_model.register_forward_hook(lambda *a: vision_calls.append(True))
            first = True
            try:
                for prompt_name in prompts:
                    for index, sample in enumerate(rows, 1):
                        public = inference_sample(sample)
                        text = processor.apply_chat_template([{'role': 'user', 'content': [
                            {'type': 'image'}, {'type': 'text', 'text': prompt_for(public, prompt_name)}]}],
                            add_generation_prompt=True, tokenize=False)
                        case_dir = output / 'cases' / phase / variant / prompt_name / ('%02d' % index)
                        case_dir.mkdir(parents=True)
                        with Image.open(safe_path(dataset, public['image']['path'])) as source:
                            image = source.convert('RGB'); image.save(case_dir / 'input.png')
                        cpu_inputs = dict(processor(text=text, images=[image], return_tensors='pt'))
                        if 'pixel_values' not in cpu_inputs or cpu_inputs['pixel_values'].numel() == 0: raise ValueError('Missing image tensors.')
                        prompt_tokens = cpu_inputs['input_ids'].shape[1]
                        if prompt_tokens + config['max_new_tokens'] > config['max_sequence_length']:
                            raise ValueError('Input+generation exceeds reviewed context budget; no truncation.')
                        input_hash = digest({name: {'shape': list(value.shape), 'dtype': str(value.dtype),
                            'sha256': hashlib.sha256(value.contiguous().view(torch.uint8).numpy().tobytes()).hexdigest()}
                            for name, value in cpu_inputs.items()})
                        inputs = {k: v.to(device='cuda', dtype=torch.bfloat16 if v.is_floating_point() else v.dtype) for k, v in cpu_inputs.items()}
                        torch.cuda.reset_peak_memory_stats(); torch.cuda.synchronize(); start = time.perf_counter()
                        response, tokens, error = '', [], None
                        vision_start = len(vision_calls)
                        try:
                            with torch.inference_mode(), torch.autocast('cuda', dtype=torch.bfloat16):
                                generated = model.generate(**inputs, generation_config=generation)
                            torch.cuda.synchronize()
                            tokens = generated[0, prompt_tokens:].detach().cpu().tolist()
                            response = processor.tokenizer.decode(tokens, skip_special_tokens=True)
                            if len(vision_calls) == vision_start: raise RuntimeError('Vision encoder did not run.')
                            if not tokens: raise RuntimeError('No tokens generated.')
                            del generated
                        except (RuntimeError, ValueError) as exc:
                            error = {'category': 'generation_failure', 'type': type(exc).__name__, 'message': str(exc)}
                        seconds = time.perf_counter() - start
                        row = {'sample_id': public['id'], 'split': phase, 'variant': variant, 'prompt_variant': prompt_name,
                               'image': public['image'], 'target': sample['target'], 'prompt': text,
                               'prompt_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'input_sha256': input_hash,
                               'prompt_tokens': prompt_tokens, 'response': response, 'generated_token_ids': tokens,
                               'new_tokens': len(tokens), 'generation_config': generation_settings, 'generation_error': error,
                               'token_limit_hit': len(tokens) >= config['max_new_tokens'] and (not tokens or tokens[-1] != eos),
                               'vision_forward_calls': len(vision_calls) - vision_start,
                               'generation_seconds': round(seconds, 6), 'first_generation_after_load': first,
                               'model_load_seconds': round(load_seconds, 6), 'adapter_tensors_verified': adapter_verified,
                               'trainable_parameters': sum(p.numel() for p in model.parameters() if p.requires_grad),
                               'peak_allocated_mib': round(torch.cuda.max_memory_allocated() / 2**20, 2),
                               'peak_reserved_mib': round(torch.cuda.max_memory_reserved() / 2**20, 2),
                               'result_path': (case_dir / 'result.json').relative_to(output).as_posix()}
                        row['score'] = score_response(response, sample['target'], public['image']['width'], public['image']['height'],
                                                      config['coordinate_tolerance_pixels'], error)
                        write_json(case_dir / 'result.json', row)
                        with (output / (phase + '.jsonl')).open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                        results.append(row); first = False
                        summary[phase + '_generation_count'] += 1; write_json(output / 'summary.json', summary)
                        print(f'{phase}/{variant}/{prompt_name}/{index}: {row["score"]["error_category"] or "annotation_match"}', flush=True)
                        del inputs, cpu_inputs
            finally:
                hook.remove(); del hook, base, model; gc.collect(); torch.cuda.empty_cache()

        validation = read_jsonl(dataset / 'validation.jsonl')
        if any(r['split'] != 'validation' for r in validation): raise ValueError('Wrong validation split.')
        generate_group('validation', 'best', list(PROMPTS), validation)
        selected = select_prompt(results, [r['id'] for r in validation])
        selected.update(created_at_utc=datetime.now(timezone.utc).isoformat(), plan_sha256=plan_hash,
                        validation_results_sha256=sha256(output / 'validation.jsonl'))
        stage('freezing_validation_selection')
        write_json(output / 'selection.json', selected); selection_hash = sha256(output / 'selection.json')
        # Model-test use begins here; never revise prompts or hyperparameters from subsequent output.
        tests = read_jsonl(dataset / 'test.jsonl')
        if any(r['split'] != 'test' for r in tests): raise ValueError('Wrong test split.')
        test_prompts = list(dict.fromkeys([config['primary_prompt'], selected['selected_prompt']]))
        for variant in VARIANTS: generate_group('test', variant, test_prompts, tests)
        test_rows = [r for r in results if r['split'] == 'test']
        paired_checks(test_rows, test_prompts, [r['id'] for r in tests])
        if sha256(output / 'selection.json') != selection_hash or sha256(output / 'evaluation-plan.json') != plan_hash:
            raise ValueError('Frozen protocol or selection changed after test use.')
        after = verify_training(root, config, training, dataset)
        if after != provenance: raise ValueError('Training artifacts changed during read-only evaluation.')
        groups = {variant: {name: aggregate([r for r in test_rows if r['variant'] == variant and r['prompt_variant'] == name])
                             for name in test_prompts} for variant in VARIANTS}
        generation_errors = sum(r['generation_error'] is not None for r in results)
        summary.update(status='completed' if not generation_errors else 'failed', success=not generation_errors,
                       data_audit=data_audit, real_model_used=True, variants=list(VARIANTS), test_prompts=test_prompts,
                       fixed_test_examples=len(tests), independent_test_task_groups=len({r['component_id'] for r in tests}),
                       validation_examples=len(validation), selected_prompt=selected['selected_prompt'],
                       primary_prompt=config['primary_prompt'], comparison=groups,
                       selection_sha256=selection_hash, plan_sha256=plan_hash, generation_errors=generation_errors,
                       artifacts_unchanged=True, paired_inputs_verified=True,
                       model_quality_accepted=False,
                       limitations=['Two fixed test records from one task group are not a representative benchmark.',
                                    'Annotation matching is an offline proxy; there are no control bounding boxes or independently verified postconditions.',
                                    'Single-process generation times are diagnostics, not a cold/hot performance benchmark.'])
        write_json(output / 'evidence-manifest.json', {'files': [{'path': p.relative_to(output).as_posix(), 'sha256': sha256(p)}
                   for p in sorted(output.rglob('*')) if p.is_file() and p.name not in ('summary.json', 'evidence-manifest.json')]})
    except (Exception, KeyboardInterrupt) as exc:
        summary.update(status='aborted' if isinstance(exc, KeyboardInterrupt) else 'failed', success=False,
                       error={'type': type(exc).__name__, 'code': getattr(exc, 'code', None), 'message': str(exc)})
    finally:
        summary.update(finished_at_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=round(time.perf_counter() - begin, 6))
        write_json(output / 'summary.json', summary)
    return summary
