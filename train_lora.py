"""Train the fixed local GUI LoRA dataset; resume only complete, verified checkpoints."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from gui_training.trainer import run_training

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/week3-tuesday.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--stop-after-step', type=int, help='Controlled stop after a complete optimizer update, for recovery checks')
    args = parser.parse_args()
    output = args.output or ROOT / 'output/week3-tuesday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    result = run_training(ROOT, json.loads(args.config.read_text(encoding='utf-8')), output, args.resume, args.stop_after_step)
    print(json.dumps({'status': result['status'], 'global_step': result['global_step'], 'summary': str(output / 'summary.json'),
                      'error': result.get('error')}, ensure_ascii=True))
    return 0 if result['status'] in ('completed', 'stopped') else 1


if __name__ == '__main__': raise SystemExit(main())
