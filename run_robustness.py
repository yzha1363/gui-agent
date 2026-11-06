"""Run labelled simulation robustness scenarios; never send real desktop input."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from gui_tasks.robustness import SCENARIOS, run_robustness

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / 'output/week3-friday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    result, _ = run_robustness(output, args.scenario)
    print(json.dumps({'status': result['status'], 'error': result.get('error'), 'environment': 'simulation',
                      'summary': str(output / 'session/summary.json')}, ensure_ascii=True))
    return 0 if result['success'] else 1


if __name__ == '__main__': raise SystemExit(main())
