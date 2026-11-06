"""Run the Monday local visual inference and exactly one real LoRA optimizer step."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from gui_training.vlm import run_preflight

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/week3-monday.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    output = args.output or ROOT / 'output/week3-monday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    summary = run_preflight(ROOT, config, output)
    print(json.dumps({'status': summary['status'], 'success': summary['success'], 'summary': str(output / 'summary.json'),
                      'error': summary.get('error')}, ensure_ascii=True))
    return 0 if summary['success'] else 1


if __name__ == '__main__': raise SystemExit(main())
