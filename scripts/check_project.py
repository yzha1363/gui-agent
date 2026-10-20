"""Run offline project checks; never capture the desktop or inject input."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def verify_historical_evidence():
    """Check saved Friday summaries against their recorded expected outcomes.

    This verifies existing evidence files; it does not rerun desktop scenarios.
    """
    aggregate = ROOT / 'output/friday-test-summary.json'
    report = json.loads(aggregate.read_text(encoding='utf-8'))
    cases = report['desktop_cases']
    if not cases or report.get('all_passed') is not True:
        raise ValueError('Historical desktop summary is empty or not passing.')
    for case in cases:
        summary_path = (ROOT / case['summary']).resolve()
        if not summary_path.is_relative_to((ROOT / 'output').resolve()):
            raise ValueError('Evidence path must remain inside output/.')
        actual = json.loads(summary_path.read_text(encoding='utf-8'))
        if case.get('passed') is not True or actual['status'] != case['workflow_status']:
            raise ValueError(f'Unexpected historical outcome: {case["case"]}')
        if actual.get('error', {}).get('code') != case.get('error_code'):
            raise ValueError(f'Historical error mismatch: {case["case"]}')
        action_file = summary_path.parent / 'actions.jsonl'
        actions = [json.loads(line) for line in action_file.read_text(encoding='utf-8').splitlines()] if action_file.exists() else []
        if sum(action['status'] == 'sent' for action in actions) != case['sent_actions']:
            raise ValueError(f'Action-count mismatch: {case["case"]}')
        if actual['status'] == 'completed' and not (
            actual.get('success') is True and actual.get('steps') and all(step['verified'] for step in actual['steps'])
        ):
            raise ValueError(f'Unverified completed workflow: {case["case"]}')
    return len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-evidence', action='store_true', help='Also check existing Friday desktop records')
    args = parser.parse_args()
    directory = ROOT / 'output/project-checks' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    checks = [
        ('unit_tests', ['-m', 'unittest', 'discover', '-s', 'tests', '-v']),
        ('plan_validation', ['run_workflow.py', '--plan', 'plans/friday_demo.json', '--validate-only']),
        ('dependency_check', ['-m', 'pip', 'check']),
    ]
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
              'python_executable': sys.executable, 'desktop_input_sent': False, 'checks': []}
    environment = dict(os.environ, PYTHONIOENCODING='utf-8')
    for name, arguments in checks:
        start = time.monotonic()
        try:
            result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=environment,
                                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
            log = result.stdout + result.stderr
            row = {'name': name, 'passed': result.returncode == 0, 'return_code': result.returncode}
            if name == 'unit_tests':
                match = re.search(r'Ran (\d+) tests?', log)
                row['test_count'] = int(match.group(1)) if match else None
        except (OSError, subprocess.TimeoutExpired) as exc:
            log = str(exc)
            row = {'name': name, 'passed': False, 'error': log}
        row['seconds'] = round(time.monotonic() - start, 3)
        row['log'] = (directory / f'{name}.log').relative_to(ROOT).as_posix()
        (ROOT / row['log']).write_text(log, encoding='utf-8')
        report['checks'].append(row)
        print(f'{name}: {"PASS" if row["passed"] else "FAIL"}', flush=True)
    if args.with_evidence:
        try:
            count = verify_historical_evidence()
            row = {'name': 'historical_desktop_evidence', 'passed': True,
                   'verified_case_count': count, 'rerun': False}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            row = {'name': 'historical_desktop_evidence', 'passed': False, 'error': str(exc)}
        report['checks'].append(row)
        print(f'historical_desktop_evidence: {"PASS" if row["passed"] else "FAIL"}', flush=True)
    report['all_passed'] = all(row['passed'] for row in report['checks'])
    (directory / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Report: ' + str(directory / 'summary.json'))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
