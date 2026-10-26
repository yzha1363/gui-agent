"""Same-input OCR process-cold and in-process warm latency benchmark."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def stats(values):
    ordered = sorted(values)
    # Nearest-rank p95, explicitly suited to a small descriptive sample.
    import math
    return {'n': len(values), 'mean_ms': round(statistics.mean(values), 2),
            'median_ms': round(statistics.median(values), 2),
            'p95_ms': round(ordered[math.ceil(.95 * len(values)) - 1], 2),
            'min_ms': round(min(values), 2), 'max_ms': round(max(values), 2)}


def worker(args):
    started = time.perf_counter()
    import numpy as np
    from PIL import Image
    import torch
    from gui_perception.pipeline import create_reader
    imported = time.perf_counter()
    image = Image.open(args.input).convert('RGB')
    pixels = np.asarray(image)
    loaded_image = time.perf_counter()
    reader, device = create_reader(args.device)
    # Do not synchronize before the first inference: lazy CUDA setup belongs
    # to the cold path. Synchronize after each measurement to include GPU work.
    if device == 'cuda':
        torch.cuda.synchronize()
    ready = time.perf_counter()
    def infer():
        start = time.perf_counter()
        raw = reader.readtext(pixels, detail=1, paragraph=False, canvas_size=1600, batch_size=1, workers=0)
        if device == 'cuda':
            torch.cuda.synchronize()
        return round((time.perf_counter() - start) * 1000, 3), [text for _, text, _ in raw]
    first_ms, texts = infer()
    cold_ready_ms = (time.perf_counter() - started) * 1000
    warm = [infer()[0] for _ in range(args.warm)]
    result = {'import_ms': (imported - started) * 1000,
              'image_load_ms': (loaded_image - imported) * 1000,
              'reader_init_ms': (ready - loaded_image) * 1000,
              'first_inference_ms': first_ms, 'cold_ready_ms': cold_ready_ms,
              'warm_inference_ms': warm, 'recognized_texts': texts,
              'device': device, 'image_size': list(image.size), 'python': sys.version,
              'torch': torch.__version__, 'cuda': torch.version.cuda,
              'gpu': torch.cuda.get_device_name() if device == 'cuda' else None}
    args.worker_output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=ROOT / 'output/wednesday-validation/1280x720/screenshot.png')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/revision/benchmark')
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--cold', type=int, default=3)
    parser.add_argument('--warm', type=int, default=10)
    parser.add_argument('--worker-output', type=Path)
    args = parser.parse_args()
    if args.cold < 1 or args.warm < 1:
        parser.error('Sample counts must be positive')
    if args.worker_output:
        worker(args)
        return
    directory = args.output / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    samples = []
    for index in range(args.cold):
        path = directory / f'process-{index+1}.json'
        start = time.perf_counter()
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--input', str(args.input.resolve()),
                        '--device', args.device, '--warm', str(args.warm), '--worker-output', str(path)],
                       check=True, cwd=ROOT)
        result = json.loads(path.read_text(encoding='utf-8'))
        result['process_wall_ms'] = (time.perf_counter() - start) * 1000
        samples.append(result)
        print(f'Cold process {index+1}/{args.cold} complete', flush=True)
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'definition': 'Fresh Python process, cached model files and OS caches; not reboot-cold or download time.',
              'warm_definition': 'Same reader and identical image after its first inference; GPU synchronized after every call.',
              'input': str(args.input.resolve()), 'input_sha256': hashlib.sha256(args.input.read_bytes()).hexdigest(),
              'parameters': {'canvas_size': 1600, 'batch_size': 1, 'workers': 0}, 'samples': samples,
              'statistics': {key: stats([sample[key] for sample in samples]) for key in
                             ['import_ms', 'reader_init_ms', 'first_inference_ms', 'cold_ready_ms', 'process_wall_ms']}}
    report['statistics']['warm_inference_ms'] = stats([value for sample in samples for value in sample['warm_inference_ms']])
    report['note'] = 'process_wall_ms includes warm runs and process teardown; use cold_ready_ms for cold OCR readiness.'
    (directory / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(directory / 'summary.json', flush=True)


if __name__ == '__main__':
    main()
