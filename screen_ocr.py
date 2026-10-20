r"""Run with .venv\Scripts\python.exe screen_ocr.py --help."""

import argparse
from datetime import datetime
from pathlib import Path
import sys
import time

from gui_perception.pipeline import capture_screen, create_reader, list_monitors, save_run


def main():
    parser = argparse.ArgumentParser(description='Local Chinese/English screen capture and OCR')
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--input', type=Path, help='OCR an image instead of the desktop')
    source.add_argument('--region', nargs=4, type=int, metavar=('LEFT', 'TOP', 'WIDTH', 'HEIGHT'))
    parser.add_argument('--monitor', type=int, default=1, help='1: first screen, 0: virtual desktop')
    parser.add_argument('--list-monitors', action='store_true')
    parser.add_argument('--output', type=Path, default=Path('output/wednesday'))
    parser.add_argument('--device', choices=['auto', 'cuda', 'cpu'], default='auto')
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--min-confidence', type=float, default=0.2)
    parser.add_argument('--canvas-size', type=int, default=1600)
    parser.add_argument('--capture-only', action='store_true')
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--interval', type=float, default=1.0, help='Minimum seconds between capture starts')
    args = parser.parse_args()
    if not 0 <= args.min_confidence <= 1:
        parser.error('--min-confidence must be between 0 and 1')
    if args.count < 1 or args.interval < 0 or args.canvas_size < 32:
        parser.error('count >= 1, interval >= 0, canvas-size >= 32 are required')
    if args.input and args.count != 1:
        parser.error('--input supports a single run')
    try:
        if args.list_monitors:
            import json
            print(json.dumps(list_monitors(), indent=2))
            return 0
        reader, device = (None, None) if args.capture_only else create_reader(args.device, args.model_dir)
        for index in range(args.count):
            start = time.perf_counter()
            if args.input:
                from PIL import Image, ImageOps
                with Image.open(args.input) as opened:
                    image = ImageOps.exif_transpose(opened).convert('RGB')
                source_info = {'kind': 'image', 'path': str(args.input.resolve()), 'coordinate_space': 'image-pixels'}
            else:
                image, source_info = capture_screen(args.monitor, args.region)
            directory = args.output / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            report = save_run(image, source_info, directory, reader, device,
                              args.min_confidence, args.canvas_size)
            print(f"Saved: {directory.resolve()} | {image.width}x{image.height} | "
                  f"texts={report['result_count']} | device={device} | OCR={report['ocr_ms']:.0f}ms", flush=True)
            if index + 1 < args.count:
                time.sleep(max(0, args.interval - (time.perf_counter() - start)))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == '__main__':
    raise SystemExit(main())
