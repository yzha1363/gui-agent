"""Small multi-example LoRA training with explicit sample/token accounting."""

from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import random
import shutil
import time

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset, Sampler

from gui_data.pipeline import read_jsonl
from gui_data.schema import DataError, safe_path
from gui_training.data import audit_dataset, sha256
from gui_training.vlm import assistant_batch, image_prefix, inference_probe, move_batch, verify_model


def dump(path, data):
    path = Path(path); temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_training_config(config):
    if config.get('device') != 'cuda' or config.get('dtype') != 'bfloat16':
        raise ValueError('Use the reviewed CUDA bf16 environment; no CPU fallback.')
    ranges = {'epochs': (1, 20), 'gradient_accumulation_steps': (1, 16), 'max_sequence_length': (64, 4096),
              'save_every_steps': (1, 1000), 'warmup_steps': (0, 1000), 'seed': (0, 2**31 - 1), 'max_new_tokens': (1, 256)}
    for name, (low, high) in ranges.items():
        if type(config.get(name)) is not int or not low <= config[name] <= high: raise ValueError('Invalid ' + name)
    if config.get('batch_size') != 1 or type(config.get('batch_size')) is not int:
        raise ValueError('Microbatch must be exactly one image; use token-weighted accumulation.')
    for name, high in [('learning_rate', .01), ('max_grad_norm', 100)]:
        value = config.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= high: raise ValueError('Invalid ' + name)
    if config.get('image_longest_edge') != 512 or config.get('do_image_splitting') is not False:
        raise ValueError('Use the reviewed 512-pixel single-image input configuration.')
    if config.get('attention_implementation') != 'sdpa' or config.get('lora_target_regex') != r'.*text_model.*\.(q_proj|v_proj)$':
        raise ValueError('Use the reviewed attention and language-only LoRA targets.')
    if type(config.get('lora_r')) is not int or not 1 <= config['lora_r'] <= 16:
        raise ValueError('Invalid LoRA rank.')
    for name, low, high in [('lora_alpha', 1, 64), ('lora_dropout', 0, .5)]:
        value = config.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high: raise ValueError('Invalid ' + name)
    return config


class EpochSampler(Sampler):
    """A local RNG gives reproducible order without consuming model RNG state."""
    def __init__(self, count, seed, epoch, offset=0):
        if type(offset) is not int or not 0 <= offset <= count: raise ValueError('Invalid sampler offset.')
        self.order = list(range(count)); random.Random(seed + epoch).shuffle(self.order)
        self.offset = offset
    def __iter__(self): return iter(self.order[self.offset:])
    def __len__(self): return len(self.order) - self.offset


def windows(iterator, size):
    pending = []
    for item in iterator:
        pending.append(item)
        if len(pending) == size:
            yield pending; pending = []
    if pending: yield pending


class VisualExamples(Dataset):
    def __init__(self, directory, split, processor, max_length):
        if split not in ('train', 'validation'): raise ValueError('The Tuesday trainer never loads test examples for model use.')
        self.rows = read_jsonl(Path(directory) / (split + '.jsonl'))
        if not self.rows or any(r['split'] != split for r in self.rows): raise ValueError('Invalid dataset split.')
        self.items = []
        # Prepare every sample before the first update; never silently skip long/bad examples.
        for row in self.rows:
            batch, audit = assistant_batch(processor, row, directory, max_length)
            self.items.append({'id': row['id'], 'split': split, 'batch': batch, 'audit': audit})
    def __len__(self): return len(self.items)
    def __getitem__(self, index): return self.items[index]


def token_count(batch):
    # Causal loss shifts labels by one. Prefix is masked, including all image tokens.
    count = int((batch['labels'][:, 1:] != -100).sum())
    if count < 1: raise ValueError('Every example needs supervised assistant tokens.')
    return count


def update_window(model, optimizer, items, accelerator, max_grad_norm):
    """Accumulate the mean per supervised token, including a short last window."""
    counts = [token_count(item['batch']) for item in items]; total = sum(counts)
    optimizer.zero_grad(set_to_none=True)
    losses = []
    for item, count in zip(items, counts):
        batch = move_batch(item['batch'], accelerator.device, torch.bfloat16)
        with accelerator.autocast(): result = model(**batch)
        loss = result.loss
        if not torch.isfinite(loss): raise RuntimeError('Nonfinite loss; optimizer was not stepped.')
        # Accelerator has accumulation_steps=1; normalization is explicit here.
        accelerator.backward(loss * (count / total))
        losses.append(float(loss.detach())); del result, loss, batch
    parameters = [p for p in model.parameters() if p.requires_grad]
    grads = [p.grad for p in parameters if p.grad is not None]
    if not grads or any(not torch.isfinite(g).all() for g in grads): raise RuntimeError('Missing or nonfinite gradients.')
    norm = float(accelerator.clip_grad_norm_(parameters, max_grad_norm))
    if not math.isfinite(norm) or norm <= 0: raise RuntimeError('Zero/nonfinite gradient norm.')
    if any(p.grad is not None for p in model.parameters() if not p.requires_grad): raise RuntimeError('Frozen weights received gradients.')
    optimizer.step(); optimizer.zero_grad(set_to_none=True)
    return {'loss': sum(v * n for v, n in zip(losses, counts)) / total, 'supervised_tokens': total,
            'example_losses': losses, 'example_token_counts': counts, 'gradient_norm_before_clip': norm,
            'sample_ids': [item['id'] for item in items], 'microbatches': len(items)}


def validation_loss(model, data, accelerator):
    was_training = model.training; model.eval()
    numerator, denominator, rows = 0., 0, []
    try:
        for item in data:
            count = token_count(item['batch']); batch = move_batch(item['batch'], accelerator.device, torch.bfloat16)
            with torch.inference_mode(), accelerator.autocast(): loss = float(model(**batch).loss)
            if not math.isfinite(loss): raise RuntimeError('Nonfinite validation loss.')
            numerator += loss * count; denominator += count
            rows.append({'id': item['id'], 'loss': loss, 'supervised_tokens': count})
    finally:
        model.train(was_training)
    return {'loss': numerator / denominator, 'supervised_tokens': denominator, 'examples': rows,
            'split': 'validation', 'teacher_forced': True, 'semantic_accuracy_measured': False}


def capture_rng():
    state = np.random.get_state()
    metadata = {'python': random.getstate(), 'numpy': {'algorithm': state[0], 'keys': state[1].tolist(),
                'position': state[2], 'has_gauss': state[3], 'cached_gaussian': state[4]}}
    tensors = {'torch_cpu': torch.get_rng_state()}
    if torch.cuda.is_initialized(): tensors['torch_cuda'] = torch.cuda.get_rng_state_all()
    return metadata, tensors


def restore_rng(metadata, tensors):
    def tuples(value): return tuple(tuples(v) for v in value) if isinstance(value, list) else value
    random.setstate(tuples(metadata['python']))
    n = metadata['numpy']
    np.random.set_state((n['algorithm'], np.array(n['keys'], dtype=np.uint32), n['position'], n['has_gauss'], n['cached_gaussian']))
    torch.set_rng_state(tensors['torch_cpu'])
    if 'torch_cuda' in tensors:
        if len(tensors['torch_cuda']) != torch.cuda.device_count(): raise ValueError('CUDA device count changed during resume.')
        torch.cuda.set_rng_state_all(tensors['torch_cuda'])


def save_checkpoint(destination, state, export_adapter, optimizer, scheduler, best_adapter=None):
    destination = Path(destination); temporary = destination.with_name(destination.name + '.pending')
    if destination.exists() or temporary.exists(): raise FileExistsError('Checkpoint directories are immutable.')
    temporary.mkdir(parents=True)
    export_adapter(temporary / 'adapter')
    if best_adapter is not None: shutil.copytree(best_adapter, temporary / 'best_adapter')
    rng_meta, rng_tensors = capture_rng()
    torch.save(optimizer.state_dict(), temporary / 'optimizer.pt')
    torch.save(rng_tensors, temporary / 'rng.pt')
    dump(temporary / 'state.json', {**state, 'rng': rng_meta, 'scheduler': scheduler.state_dict()})
    files = [{'path': p.relative_to(temporary).as_posix(), 'sha256': sha256(p)} for p in sorted(temporary.rglob('*')) if p.is_file()]
    dump(temporary / 'integrity.json', {'schema_version': '1.0', 'files': files})
    temporary.replace(destination)
    return destination


def inspect_checkpoint(directory, config_fingerprint, dataset_fingerprint):
    directory = Path(directory)
    inventory = json.loads((directory / 'integrity.json').read_text(encoding='utf-8'))
    declared = {item['path'] for item in inventory['files']}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file() and p.name != 'integrity.json'}
    if declared != actual: raise ValueError('Checkpoint file inventory mismatch.')
    for item in inventory['files']:
        if sha256(safe_path(directory, item['path'])) != item['sha256']: raise ValueError('Checkpoint hash mismatch: ' + item['path'])
    state = json.loads((directory / 'state.json').read_text(encoding='utf-8'))
    if state['config_sha256'] != config_fingerprint or state['dataset_sha256'] != dataset_fingerprint:
        raise ValueError('Cannot resume with a different config, model or dataset.')
    return state


def load_checkpoint(directory, state, load_adapter, optimizer, scheduler):
    directory = Path(directory)
    load_adapter(directory / 'adapter')
    # Only tensor/primitive state is loaded, using PyTorch's restricted loader.
    optimizer.load_state_dict(torch.load(directory / 'optimizer.pt', map_location='cpu', weights_only=True))
    scheduler.load_state_dict(state['scheduler'])
    rng = torch.load(directory / 'rng.pt', map_location='cpu', weights_only=True)
    restore_rng(state['rng'], rng)


def lr_factor(step, warmup, total):
    if step < warmup: return (step + 1) / max(1, warmup)
    return max(0., (total - step) / max(1, total - warmup))


def validate_cursor(cursor, count, accumulation, epochs):
    epoch, offset, step = (cursor.get(k) for k in ('epoch', 'offset', 'global_step'))
    if any(type(v) is not int for v in (epoch, offset, step)):
        raise ValueError('Checkpoint cursor must contain integers.')
    if not (0 <= epoch <= epochs and 0 <= offset < count and 0 <= step <= epochs * math.ceil(count / accumulation)):
        raise ValueError('Invalid checkpoint cursor.')
    if epoch == epochs and offset != 0: raise ValueError('Completed training must have offset zero.')
    expected = epoch * math.ceil(count / accumulation) + math.ceil(offset / accumulation)
    if step != expected or offset % accumulation != 0: raise ValueError('Checkpoint is not at a completed update boundary.')
    return epoch, offset, step


def memory_snapshot():
    return {'peak_allocated_mib': round(torch.cuda.max_memory_allocated() / 2**20, 2),
            'peak_reserved_mib': round(torch.cuda.max_memory_reserved() / 2**20, 2)}


def run_training(root, config, output, resume=None, stop_after_step=None):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    summary = {'status': 'running', 'success': False, 'kind': 'real_local_vlm_lora_training',
               'started_at_utc': datetime.now(timezone.utc).isoformat(), 'desktop_input_sent': False,
               'nim_validation': 'pending', 'model_quality_evaluation': 'pending', 'fixed_test_model_use': False,
               'global_step': 0, 'steps_this_process': 0, 'epochs_completed': 0}
    begin = time.perf_counter(); history = {'train': [], 'validation': []}

    def event(name, **fields):
        with (output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time_utc': datetime.now(timezone.utc).isoformat(), 'event': name, **fields}, ensure_ascii=False) + '\n')

    def stage(name):
        summary['stage'] = name; dump(output / 'summary.json', summary)
        event('stage', stage=name); print('Training stage: ' + name, flush=True)

    try:
        validate_training_config(config)
        stage('auditing_inputs')
        dataset = root / config['dataset_dir']; model_dir = root / config['model_dir']
        dataset_audit = audit_dataset(dataset); verify_model(model_dir, config)
        dataset_hash = sha256(dataset / 'manifest.json'); fingerprint = config_hash(config)
        dump(output / 'config.json', config)
        state = inspect_checkpoint(resume, fingerprint, dataset_hash) if resume else None
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported(): raise RuntimeError('CUDA bf16 is required.')
        if state and state['environment'] != {'torch': torch.__version__, 'gpu': torch.cuda.get_device_name()}:
            raise ValueError('Resume requires the recorded PyTorch version and GPU model.')
        os.environ['HF_HUB_OFFLINE'] = '1'; os.environ['TRANSFORMERS_OFFLINE'] = '1'; os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
        os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
        from accelerate import Accelerator
        from peft import LoraConfig, PeftModel, get_peft_model, set_peft_model_state_dict
        from safetensors.torch import load_file
        from transformers import AutoModelForImageTextToText, AutoProcessor
        random.seed(config['seed']); np.random.seed(config['seed']); torch.manual_seed(config['seed']); torch.cuda.manual_seed_all(config['seed'])
        torch.use_deterministic_algorithms(True)
        torch.backends.cuda.matmul.allow_tf32 = False; torch.backends.cudnn.allow_tf32 = False; torch.backends.cudnn.benchmark = False
        accelerator = Accelerator(mixed_precision='bf16', gradient_accumulation_steps=1)
        if accelerator.num_processes != 1: raise ValueError('This trainer is validated for one GPU process only.')
        dump(output / 'environment.json', {'python': __import__('sys').version, 'executable': __import__('sys').executable,
             'versions': {k: importlib.metadata.version(k) for k in ['torch', 'transformers', 'peft', 'accelerate', 'safetensors']},
             'gpu': torch.cuda.get_device_name(), 'network_in_model_run': False, 'deterministic_algorithms': True})
        stage('loading_model_and_examples')
        processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False, use_fast=False)
        processor.image_processor.size = {'longest_edge': 512}; processor.image_processor.do_image_splitting = False
        def load_base():
            return AutoModelForImageTextToText.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False,
                dtype=torch.bfloat16, attn_implementation=config['attention_implementation']).to(accelerator.device)
        model = load_base(); model.config.use_cache = False
        model = get_peft_model(model, LoraConfig(r=config['lora_r'], lora_alpha=config['lora_alpha'], lora_dropout=config['lora_dropout'],
                               target_modules=config['lora_target_regex'], task_type='CAUSAL_LM', bias='none'))
        parameters = {n: p for n, p in model.named_parameters() if p.requires_grad}
        if not parameters or any('lora_' not in n or 'vision_model' in n for n in parameters): raise RuntimeError('Unexpected trainable base/vision weights.')
        initial_lora = {n: p.detach().cpu().clone() for n, p in parameters.items()}
        train = VisualExamples(dataset, 'train', processor, config['max_sequence_length'])
        validation = VisualExamples(dataset, 'validation', processor, config['max_sequence_length'])
        accumulation = config['gradient_accumulation_steps']; total_steps = config['epochs'] * math.ceil(len(train) / accumulation)
        if stop_after_step is not None and (type(stop_after_step) is not int or not 1 <= stop_after_step < total_steps):
            raise ValueError('stop-after-step must be a boundary before the final step.')
        optimizer = torch.optim.AdamW(parameters.values(), lr=config['learning_rate'], weight_decay=0)
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda step: lr_factor(step, config['warmup_steps'], total_steps))
        model, optimizer = accelerator.prepare(model, optimizer); unwrapped = accelerator.unwrap_model(model)
        summary.update(model_id=config['model_id'], model_revision=config['model_revision'], config_sha256=fingerprint,
                       dataset_sha256=dataset_hash, dataset_audit=dataset_audit, training_examples=len(train), validation_examples=len(validation),
                       target_steps=total_steps, trainable_parameters=sum(p.numel() for p in parameters.values()),
                       accumulation_policy='Token-weighted explicit windows; Accelerator accumulation_steps=1')
        dump(output / 'batch-audits.json', {'train': [{'id': v['id'], **v['audit']} for v in train],
                                          'validation': [{'id': v['id'], **v['audit']} for v in validation]})
        next_epoch, next_offset, global_step = 0, 0, 0
        best_loss, best_step, last_checkpoint = float('inf'), None, None
        best_dir = output / 'best_adapter'

        def export_adapter(path): unwrapped.save_pretrained(path, safe_serialization=True)
        def export_best():
            # save_pretrained replaces only this run's own best adapter files.
            export_adapter(best_dir)
        if state:
            next_epoch, next_offset, global_step = validate_cursor(state['cursor'], len(train), accumulation, config['epochs'])
            if stop_after_step is not None and stop_after_step <= global_step: raise ValueError('Stop boundary is not after resume cursor.')
            def load_adapter(path):
                result = set_peft_model_state_dict(unwrapped, load_file(str(path / 'adapter_model.safetensors')))
                if result.unexpected_keys: raise ValueError('Unexpected adapter tensor keys.')
            load_checkpoint(resume, state, load_adapter, optimizer, scheduler)
            history = state['history']; best_loss, best_step = state['best_loss'], state['best_step']
            shutil.copytree(Path(resume) / 'best_adapter', best_dir)
            summary.update(resumed_from=str(resume), resumed_from_step=global_step, global_step=global_step, epochs_completed=next_epoch)
            event('resumed', global_step=global_step, epoch=next_epoch, offset=next_offset)
        else:
            baseline_val = validation_loss(model, validation, accelerator)
            baseline_val.update(step=0, epoch=0); history['validation'].append(baseline_val)
            best_loss, best_step = baseline_val['loss'], 0; export_best()
        # A new run contains inherited history explicitly, and records its resume origin in summary/events.
        for name in ['train', 'validation']:
            with (output / (name + '.jsonl')).open('w', encoding='utf-8') as stream:
                for item in history[name]: stream.write(json.dumps(item, ensure_ascii=False) + '\n')
        torch.cuda.reset_peak_memory_stats()

        def checkpoint():
            nonlocal last_checkpoint
            destination = output / f'checkpoint-{global_step:04d}'
            if last_checkpoint == str(destination): return
            checkpoint_state = {'config_sha256': fingerprint, 'dataset_sha256': dataset_hash,
                'cursor': {'epoch': next_epoch, 'offset': next_offset, 'global_step': global_step},
                'best_loss': best_loss, 'best_step': best_step, 'history': history,
                'environment': {'torch': torch.__version__, 'gpu': torch.cuda.get_device_name()}}
            save_checkpoint(destination, checkpoint_state, export_adapter, optimizer, scheduler, best_dir)
            last_checkpoint = str(destination); summary['latest_checkpoint'] = last_checkpoint
            event('checkpoint_saved', global_step=global_step, directory=last_checkpoint)

        stage('training')
        while next_epoch < config['epochs']:
            epoch = next_epoch
            loader = DataLoader(train, batch_size=1, sampler=EpochSampler(len(train), config['seed'], epoch, next_offset),
                collate_fn=lambda items: items[0], num_workers=0,
                generator=torch.Generator().manual_seed(config['seed'] + epoch))
            for items in windows(loader, accumulation):
                model.train(); started = time.perf_counter(); lr_used = optimizer.param_groups[0]['lr']
                row = update_window(model, optimizer, items, accelerator, config['max_grad_norm'])
                scheduler.step(); torch.cuda.synchronize()
                global_step += 1; next_offset += len(items)
                row.update(step=global_step, epoch=epoch + 1, learning_rate=lr_used, next_learning_rate=optimizer.param_groups[0]['lr'],
                           seconds=round(time.perf_counter() - started, 6), **memory_snapshot())
                history['train'].append(row)
                with (output / 'train.jsonl').open('a', encoding='utf-8') as stream: stream.write(json.dumps(row, ensure_ascii=False) + '\n')
                epoch_finished = next_offset == len(train)
                if epoch_finished:
                    next_epoch, next_offset = epoch + 1, 0
                    checked = validation_loss(model, validation, accelerator); checked.update(step=global_step, epoch=next_epoch)
                    history['validation'].append(checked)
                    with (output / 'validation.jsonl').open('a', encoding='utf-8') as stream: stream.write(json.dumps(checked, ensure_ascii=False) + '\n')
                    if checked['loss'] < best_loss:
                        best_loss, best_step = checked['loss'], global_step; export_best()
                summary.update(global_step=global_step, epochs_completed=next_epoch, steps_this_process=summary['steps_this_process'] + 1,
                               best_validation_loss=best_loss, best_step=best_step, **memory_snapshot())
                dump(output / 'summary.json', summary)
                print(f'Step {global_step}/{total_steps}: loss={row["loss"]:.6f}', flush=True)
                if global_step % config['save_every_steps'] == 0 or global_step == stop_after_step or global_step == total_steps: checkpoint()
                if global_step == stop_after_step:
                    summary.update(status='stopped', success=False, stop_reason='requested_optimizer_boundary')
                    return summary
                if epoch_finished: break
        if global_step != total_steps: raise RuntimeError('Training ended before the planned number of updates.')
        checkpoint()
        stage('exporting_and_reloading')
        export_adapter(output / 'adapter'); processor.save_pretrained(output / 'adapter')
        trained = {n: p.detach().cpu().clone() for n, p in unwrapped.named_parameters() if 'lora_' in n}
        changed = sum(not torch.equal(initial_lora[n], trained[n]) for n in initial_lora)
        if changed == 0: raise RuntimeError('Training did not change the adapter.')
        prefix, prompt = image_prefix(processor, train.rows[0], dataset)
        device_prefix = move_batch(prefix, accelerator.device, torch.bfloat16)
        unwrapped.eval(); reference = inference_probe(unwrapped, device_prefix, accelerator)
        reloaded = PeftModel.from_pretrained(load_base(), output / 'adapter', is_trainable=False)
        reloaded.eval()
        loaded = {n: p.detach().cpu() for n, p in reloaded.named_parameters() if 'lora_' in n}
        weights_equal = set(trained) == set(loaded) and all(torch.equal(trained[n], loaded[n]) for n in trained)
        actual = inference_probe(reloaded, device_prefix, accelerator)
        logits_close = torch.allclose(reference, actual, atol=.002, rtol=.002)
        reload_report = {'weights_exactly_equal': weights_equal, 'logits_close': logits_close,
            'max_logit_difference': float((reference - actual).abs().max()), 'probe_split': 'train',
            'adapter_hashes': [{'path': p.relative_to(output).as_posix(), 'sha256': sha256(p)}
                               for p in sorted((output / 'adapter').rglob('*')) if p.is_file()]}
        dump(output / 'reload-verification.json', reload_report)
        if not weights_equal or not logits_close: raise RuntimeError('Final adapter reload check failed.')
        with torch.inference_mode(), accelerator.autocast():
            generated = reloaded.generate(**device_prefix, max_new_tokens=config['max_new_tokens'], do_sample=False, use_cache=True)
        response = processor.batch_decode(generated[:, device_prefix['input_ids'].shape[1]:], skip_special_tokens=True)[0]
        try:
            parsed = json.loads(response); format_valid = isinstance(parsed, dict) and isinstance(parsed.get('actions'), list)
        except ValueError: format_valid = False
        dump(output / 'reloaded-generation.json', {'sample_id': train.rows[0]['id'], 'split': 'train', 'prompt': prompt,
            'response': response, 'new_tokens': generated.shape[1] - device_prefix['input_ids'].shape[1],
            'action_json_format_valid': format_valid, 'semantic_accuracy_measured': False})
        summary.update(status='completed', success=True, adapter_reload_verified=True, changed_lora_tensors=changed,
                       initial_validation_loss=history['validation'][0]['loss'], final_validation_loss=history['validation'][-1]['loss'],
                       best_validation_loss=best_loss, best_step=best_step, reloaded_generation_json_valid=format_valid)
    except (Exception, KeyboardInterrupt) as exc:
        summary.update(status='aborted' if isinstance(exc, KeyboardInterrupt) else 'failed', success=False,
            error={'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc),
                   'category': 'cuda_out_of_memory' if isinstance(exc, torch.cuda.OutOfMemoryError) else 'training_failure'})
    finally:
        summary.update(elapsed_seconds=round(time.perf_counter() - begin, 6), finished_at_utc=datetime.now(timezone.utc).isoformat())
        dump(output / 'summary.json', summary)
    return summary
