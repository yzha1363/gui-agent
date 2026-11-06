"""Assistant-only image/text batches and a real one-step local LoRA preflight."""

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import time

from PIL import Image
import torch

from gui_data.pipeline import read_jsonl
from gui_data.schema import DataError, safe_path
from gui_training.data import audit_dataset, sha256


def user_prompt(sample):
    """Do not include target actions, source prompts, prior labels or state guesses."""
    image = sample['image']
    return ('Predict the next annotated GUI action(s) for this screenshot and task. '
            'Return only a JSON object with an actions array. Coordinates are in the original screenshot pixels '
            f'({image["width"]} x {image["height"]}), not live desktop coordinates. '
            'Do not claim execution or invent post-action states. Task: ' + sample['instruction'])


def image_prefix(processor, sample, directory):
    messages = [{'role': 'user', 'content': [{'type': 'image'}, {'type': 'text', 'text': user_prompt(sample)}]}]
    text = processor.apply_chat_template(messages, add_generation_prompt=True, tokenize=False)
    with Image.open(safe_path(directory, sample['image']['path'])) as source:
        image = source.convert('RGB')
    batch = dict(processor(text=text, images=[image], return_tensors='pt'))
    if 'pixel_values' not in batch or batch['pixel_values'].numel() == 0:
        raise DataError('missing_visual_input', 'The processor did not produce image tensors.')
    if batch['input_ids'].shape[0] != 1:
        raise DataError('invalid_batch', 'Monday validates exactly one example at a time.')
    return batch, text


def assistant_batch(processor, sample, directory, max_length=1024):
    prefix, _ = image_prefix(processor, sample, directory)
    answer = json.dumps(sample['target'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    answer_ids = processor.tokenizer.encode(answer, add_special_tokens=False)
    eos = processor.tokenizer.eos_token_id
    if not answer_ids or eos is None: raise DataError('empty_supervision', 'Assistant tokens and EOS are required.')
    target = torch.tensor([answer_ids + [eos]], dtype=prefix['input_ids'].dtype)
    n_prompt = prefix['input_ids'].shape[1]
    if n_prompt + target.shape[1] > max_length:
        raise DataError('sequence_too_long', 'Reject overlong examples instead of truncating image tokens or labels.')
    batch = dict(prefix)
    batch['input_ids'] = torch.cat([prefix['input_ids'], target], dim=1)
    batch['attention_mask'] = torch.cat([prefix['attention_mask'], torch.ones_like(target)], dim=1)
    batch['labels'] = torch.cat([torch.full_like(prefix['input_ids'], -100), target], dim=1)
    # Concatenating token IDs avoids BPE boundary drift when estimating a text prefix length.
    audit = {'prompt_tokens': n_prompt, 'assistant_tokens_including_eos': target.shape[1],
             'sequence_tokens': batch['input_ids'].shape[1],
             'all_prompt_and_image_labels_ignored': bool((batch['labels'][:, :n_prompt] == -100).all()),
             'assistant_labels_equal_input_ids': bool(torch.equal(batch['labels'][:, n_prompt:], batch['input_ids'][:, n_prompt:])),
             'supervised_token_count': int((batch['labels'] != -100).sum()),
             'pixel_values_shape': list(batch['pixel_values'].shape), 'batch_size': 1}
    return batch, audit


def move_batch(batch, device, dtype):
    return {k: v.to(device=device, dtype=dtype if v.is_floating_point() else v.dtype) for k, v in batch.items()}


def inference_probe(model, inputs, accelerator):
    """Use the same autocast policy for prepared and freshly reloaded models."""
    with torch.inference_mode(), accelerator.autocast():
        return model(**inputs, use_cache=False).logits[:, -1, :].float().cpu()


def verify_model(directory, config):
    directory = Path(directory)
    manifest = json.loads((directory / 'download-manifest.json').read_text(encoding='utf-8'))
    if manifest['model_id'] != config['model_id'] or manifest['revision'] != config['model_revision']:
        raise DataError('model_revision_mismatch', 'Model inventory differs from the fixed config.')
    if not manifest.get('upstream_hashes_verified'):
        raise DataError('unverified_model', 'Upstream model object checks are required.')
    for item in manifest['files']:
        if sha256(safe_path(directory, item['path'])) != item['sha256']:
            raise DataError('model_hash_mismatch', item['path'])
    return manifest


def validate_config(config):
    if config.get('device') != 'cuda' or config.get('dtype') != 'bfloat16':
        raise ValueError('The reviewed Monday preflight requires CUDA bfloat16; no silent CPU fallback.')
    if any(config.get(k) != 1 for k in ('batch_size', 'gradient_accumulation_steps', 'optimizer_steps')):
        raise ValueError('Monday is exactly one optimizer step with one training example.')
    if config.get('image_longest_edge') != 512 or config.get('do_image_splitting') is not False:
        raise ValueError('Use the reviewed single-image 512-pixel processor configuration.')
    if type(config.get('lora_r')) is not int or not 1 <= config['lora_r'] <= 16:
        raise ValueError('Invalid LoRA rank.')
    if not 0 < config.get('learning_rate', 0) <= .01:
        raise ValueError('Invalid learning rate.')
    return config


def run_preflight(root, config, output):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    summary = {'status': 'running', 'success': False, 'kind': 'real_local_vlm_one_step',
               'started_at_utc': datetime.now(timezone.utc).isoformat(), 'model_id': config.get('model_id'),
               'model_revision': config.get('model_revision'), 'optimizer_steps_completed': 0,
               'real_model_used': False, 'desktop_input_sent': False, 'nim_validation': 'pending',
               'model_quality_evaluation': 'pending', 'stages': {}}
    started = time.perf_counter()

    def save(name, value):
        pending = (output / name).with_suffix('.tmp')
        pending.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        pending.replace(output / name)

    def stage(name):
        summary['stage'] = name
        save('summary.json', summary)
        print('Preflight stage: ' + name, flush=True)

    def memory():
        free, total = torch.cuda.mem_get_info()
        return {'peak_allocated_mib': round(torch.cuda.max_memory_allocated() / 2**20, 2),
                'peak_reserved_mib': round(torch.cuda.max_memory_reserved() / 2**20, 2),
                'current_allocated_mib': round(torch.cuda.memory_allocated() / 2**20, 2),
                'device_free_mib': round(free / 2**20, 2), 'device_total_mib': round(total / 2**20, 2)}

    try:
        validate_config(config)
        stage('auditing_inputs')
        dataset = root / config['dataset_dir']; model_dir = root / config['model_dir']
        dataset_audit = audit_dataset(dataset)
        model_manifest = verify_model(model_dir, config)
        training = read_jsonl(dataset / 'train.jsonl')
        sample = training[0]  # Fixed ordering, never select on model output or test labels.
        if sample['split'] != 'train': raise DataError('evaluation_leakage', 'Only training examples enter the preflight.')
        summary.update(dataset=str(dataset), dataset_manifest_sha256=sha256(dataset / 'manifest.json'),
                       sample_id=sample['id'], sample_split='train', dataset_audit=dataset_audit)
        save('config.json', config); save('model-manifest.json', model_manifest)
        with Image.open(safe_path(dataset, sample['image']['path'])) as im: im.save(output / 'model-input.png')
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError('CUDA with bfloat16 support is required.')
        for key in ['HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY']:
            os.environ[key] = '1'
        from accelerate import Accelerator
        from peft import LoraConfig, PeftModel, get_peft_model
        from transformers import AutoModelForImageTextToText, AutoProcessor
        environment = {'python': __import__('sys').version, 'python_executable': __import__('sys').executable,
                       'versions': {name: importlib.metadata.version(name) for name in
                                    ['torch', 'torchvision', 'transformers', 'peft', 'accelerate', 'safetensors', 'huggingface-hub', 'tokenizers']},
                       'gpu': torch.cuda.get_device_name(), 'compute_capability': list(torch.cuda.get_device_capability()),
                       'cuda_runtime': torch.version.cuda, 'network_in_model_run': False}
        save('environment.json', environment)
        random.seed(config['seed']); torch.manual_seed(config['seed']); torch.cuda.manual_seed_all(config['seed'])
        stage('loading_local_model')
        torch.cuda.reset_peak_memory_stats(); begin = time.perf_counter()
        processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False, use_fast=False)
        processor.image_processor.size = {'longest_edge': config['image_longest_edge']}
        processor.image_processor.do_image_splitting = config['do_image_splitting']
        def load_base():
            return AutoModelForImageTextToText.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False,
                dtype=torch.bfloat16, attn_implementation=config['attention_implementation']).to('cuda')
        model = load_base(); model.eval()
        torch.cuda.synchronize()
        summary['real_model_used'] = True
        summary['stages']['load'] = {'seconds': round(time.perf_counter() - begin, 6), **memory()}
        batch, batch_audit = assistant_batch(processor, sample, dataset, config['max_sequence_length'])
        image_token_id = model.config.image_token_id
        batch_audit['image_token_id'] = image_token_id
        batch_audit['image_token_count'] = int((batch['input_ids'] == image_token_id).sum())
        if batch_audit['image_token_count'] < 1: raise DataError('missing_visual_input', 'No image tokens in the batch.')
        prefix, prompt_text = image_prefix(processor, sample, dataset)
        save('batch-audit.json', batch_audit)
        vision_calls = []
        hook = model.model.vision_model.register_forward_hook(lambda *args: vision_calls.append(True))
        stage('base_model_inference')
        torch.cuda.reset_peak_memory_stats(); begin = time.perf_counter()
        with torch.inference_mode():
            device_prefix = move_batch(prefix, 'cuda', torch.bfloat16)
            generated = model.generate(**device_prefix, max_new_tokens=config['max_new_tokens'], do_sample=False)
        torch.cuda.synchronize()
        generated_ids = generated[:, device_prefix['input_ids'].shape[1]:]
        response = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
        try:
            parsed = json.loads(response)
            format_valid = isinstance(parsed, dict) and isinstance(parsed.get('actions'), list)
        except ValueError: format_valid = False
        baseline = {'sample_id': sample['id'], 'split': 'train', 'prompt': prompt_text, 'response': response,
                    'generated_token_count': generated_ids.shape[1], 'action_json_format_valid': format_valid,
                    'semantic_accuracy_measured': False, 'vision_forward_calls': len(vision_calls),
                    'seconds': round(time.perf_counter() - begin, 6), **memory()}
        if not vision_calls or not generated_ids.numel(): raise RuntimeError('Visual generation did not execute.')
        save('baseline.json', baseline); summary['stages']['inference'] = baseline
        hook.remove(); del generated, generated_ids
        stage('one_optimizer_step')
        model.config.use_cache = False
        lora = LoraConfig(r=config['lora_r'], lora_alpha=config['lora_alpha'], lora_dropout=config['lora_dropout'],
                          target_modules=config['lora_target_regex'], bias='none', task_type='CAUSAL_LM')
        model = get_peft_model(model, lora)
        trainable = {name: p for name, p in model.named_parameters() if p.requires_grad}
        if not trainable or any('lora_' not in name or 'vision_model' in name for name in trainable):
            raise RuntimeError('Only reviewed language LoRA weights may be trainable.')
        before = {name: p.detach().float().cpu().clone() for name, p in trainable.items()}
        optimizer = torch.optim.AdamW(trainable.values(), lr=config['learning_rate'], weight_decay=0)
        accelerator = Accelerator(mixed_precision='bf16', gradient_accumulation_steps=1)
        model, optimizer = accelerator.prepare(model, optimizer)
        device_batch = move_batch(batch, accelerator.device, torch.bfloat16)
        model.train(); torch.cuda.reset_peak_memory_stats(); begin = time.perf_counter()
        optimizer.zero_grad(set_to_none=True)
        result = model(**device_batch)
        loss = result.loss
        if not torch.isfinite(loss): raise RuntimeError('Nonfinite training loss.')
        accelerator.backward(loss)
        gradients = [p.grad for p in trainable.values() if p.grad is not None]
        if not gradients or not all(torch.isfinite(g).all() for g in gradients): raise RuntimeError('Missing or nonfinite LoRA gradients.')
        grad_norm = accelerator.clip_grad_norm_(list(trainable.values()), config['max_grad_norm'])
        if not math.isfinite(float(grad_norm)) or float(grad_norm) <= 0: raise RuntimeError('Zero or invalid gradient norm.')
        frozen_have_no_grad = all(p.grad is None for p in model.parameters() if not p.requires_grad)
        if not frozen_have_no_grad: raise RuntimeError('Frozen base parameters unexpectedly received gradients.')
        optimizer.step(); torch.cuda.synchronize()
        changed = sum(not torch.equal(before[name], p.detach().float().cpu()) for name, p in trainable.items())
        if not changed: raise RuntimeError('Optimizer step did not change any LoRA tensor.')
        summary['optimizer_steps_completed'] = 1
        step = {'optimizer_steps': 1, 'sample_id': sample['id'], 'split': 'train', 'loss': float(loss.detach()),
                'gradient_norm_before_clip': float(grad_norm), 'finite_gradients': True, 'frozen_parameters_have_no_grad': frozen_have_no_grad,
                'trainable_parameters': sum(p.numel() for p in trainable.values()),
                'total_parameters_with_adapter': sum(p.numel() for p in model.parameters()),
                'trainable_tensor_count': len(trainable), 'changed_lora_tensors': changed,
                'seconds': round(time.perf_counter() - begin, 6), **memory()}
        save('training-step.json', step); summary['stages']['train'] = step
        optimizer.zero_grad(set_to_none=True)
        del result, loss, device_batch
        stage('saving_and_reloading_adapter')
        unwrapped = accelerator.unwrap_model(model)
        unwrapped.save_pretrained(output / 'adapter', safe_serialization=True)
        processor.save_pretrained(output / 'adapter')
        unwrapped.eval()
        trained_lora = {n: p.detach().cpu().clone() for n, p in unwrapped.named_parameters() if 'lora_' in n}
        reference = inference_probe(unwrapped, device_prefix, accelerator)
        reloaded = PeftModel.from_pretrained(load_base(), output / 'adapter', is_trainable=False)
        reloaded.eval()
        loaded_lora = {n: p.detach().cpu() for n, p in reloaded.named_parameters() if 'lora_' in n}
        same = set(trained_lora) == set(loaded_lora) and all(torch.equal(trained_lora[n], loaded_lora[n]) for n in trained_lora)
        actual = inference_probe(reloaded, device_prefix, accelerator)
        logits_equal = torch.allclose(reference, actual, atol=.002, rtol=.002)
        reload_report = {'lora_tensors_exactly_equal': same, 'next_token_logits_close': logits_equal,
                         'max_logit_absolute_difference': float((reference - actual).abs().max()),
                         'autocast_policy': 'Same Accelerator bf16 autocast for both models',
                         'probe_split': 'train', 'atol': .002, 'rtol': .002,
                         'adapter_files': [{'path': p.relative_to(output).as_posix(), 'sha256': sha256(p)}
                                           for p in sorted((output / 'adapter').rglob('*')) if p.is_file()]}
        save('reload-verification.json', reload_report)
        if not same or not logits_equal: raise RuntimeError('Adapter reload equivalence failed.')
        summary.update(status='completed', success=True, adapter_reload_verified=True,
                       assistant_only_supervision_verified=batch_audit['all_prompt_and_image_labels_ignored'],
                       evaluation_examples_used_for_gradient=False)
    except (Exception, KeyboardInterrupt) as exc:
        summary.update(status='aborted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                       error={'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc),
                              'category': 'cuda_out_of_memory' if isinstance(exc, torch.cuda.OutOfMemoryError) else 'preflight_failure'})
    finally:
        summary['elapsed_seconds'] = round(time.perf_counter() - started, 6)
        summary['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        save('summary.json', summary)
    return summary
