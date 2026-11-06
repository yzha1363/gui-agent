"""Run a bounded recovery scenario with simulated app/OCR/input only."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from gui_agent.recovery_simulation import SCENARIOS, run_recovery_simulation
from gui_workflow.recovery import RecoveryPolicy

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=SCENARIOS, default='normal')
    parser.add_argument('--policy', type=Path, default=ROOT / 'configs/week3-thursday.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    policy = RecoveryPolicy.from_dict(json.loads(args.policy.read_text(encoding='utf-8')))
    output = args.output or ROOT / 'output/week3-thursday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    result, _ = run_recovery_simulation(output, args.scenario, policy)
    print(json.dumps({'status': result['status'], 'error': result.get('error'), 'environment': 'simulation',
                      'replans': result['replans_used'], 'summary': str(output / 'session/summary.json')}, ensure_ascii=True))
    return 0 if result['success'] else 1


if __name__ == '__main__': raise SystemExit(main())
