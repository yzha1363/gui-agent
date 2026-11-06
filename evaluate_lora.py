"""Run validation-only prompt selection, freeze it, then compare fixed test outputs."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from gui_training.evaluate_run import run_evaluation

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'configs/week3-wednesday.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'output/week3-wednesday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    summary = run_evaluation(ROOT, json.loads(args.config.read_text(encoding='utf-8')), output)
    print(json.dumps({'status': summary['status'], 'summary': str(output / 'summary.json'), 'error': summary.get('error')}, ensure_ascii=True))
    return 0 if summary['success'] else 1


if __name__ == '__main__': raise SystemExit(main())
