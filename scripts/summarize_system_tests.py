"""Validate actual GUI run artifacts, separating task failure from test failure."""

from datetime import datetime, timezone
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED = {'duplicate': ('AmbiguousTarget', 0), 'missing': ('target_unavailable', 0),
            'no_change': ('verification_timeout', 1), 'false_ocr': ('verification_timeout', 1)}


def main():
    latest, blocked, attempts = {}, [], []
    for path in sorted((ROOT / 'output/revision/system').rglob('summary.json')):
        run = json.loads(path.read_text(encoding='utf-8'))
        if not run.get('display') or not run.get('scenario'):
            blocked.append({'summary': path.relative_to(ROOT).as_posix(), 'status': run['status'],
                            'error': run.get('error')})
            continue
        key = (run['display']['scale_percent'], run['scenario'])
        attempts.append({'summary': path.relative_to(ROOT).as_posix(), 'scale_percent': key[0],
                         'scenario': key[1], 'workflow_status': run['status'],
                         'finished_at_utc': run.get('finished_at_utc'), 'error': run.get('error'),
                         'state_write_denials': run.get('state_write_denials')})
        stamp = run.get('finished_at_utc', '')
        if key not in latest or stamp > latest[key][0]:
            latest[key] = (stamp, path, run)
    cases = []
    for (scale, scenario), (_, path, run) in sorted(latest.items()):
        actions = sum(bool(step['action_sent']) for step in run.get('steps', []))
        assertions = {'dpi_matches_scale': run['display']['dpi'] == round(scale / 100 * 96),
                      'same_resolution': run['display']['screen_physical_pixels'] == [1920, 1080]}
        if scenario == 'normal':
            assertions['completed'] = run['status'] == 'completed' and run['success']
            assertions['three_actions'] = actions == 3
            assertions['all_three_double_checked'] = len(run['steps']) == 3 and all(
                step.get('verified') and step.get('verification', {}).get('ocr', {}).get('met') is True
                and step.get('verification', {}).get('state', {}).get('met') is True for step in run['steps'])
        else:
            code, count = EXPECTED[scenario]
            assertions['expected_failure'] = run['status'] == 'failed' and run.get('error', {}).get('code') == code
            assertions['action_count'] = actions == count
            if scenario == 'false_ocr':
                evidence = run['steps'][-1].get('verification', {})
                assertions['false_positive_rejected'] = (evidence.get('ocr', {}).get('met') is True
                                                         and evidence.get('state', {}).get('met') is False
                                                         and not evidence.get('met'))
        observations = [Path(directory) for step in run['steps'] for directory in step['observations']]
        assertions['independent_evidence_exists'] = bool(observations) and all(
            (directory / 'screenshot.png').is_file() and (directory / 'state.json').is_file()
            for directory in observations)
        cases.append({'scale_percent': scale, 'scenario': scenario, 'passed': all(assertions.values()),
                      'assertions': assertions, 'workflow_status': run['status'], 'actions': actions,
                      'error': run.get('error'), 'summary': path.relative_to(ROOT).as_posix(),
                      'last_observation': str(observations[-1]) if observations else None})
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'cases': cases,
              'tests': len(cases), 'passed': sum(case['passed'] for case in cases),
              'all_executed_cases_passed': bool(cases) and all(case['passed'] for case in cases),
              'run_attempts': attempts,
              'normal_scaling': {str(scale): next((case for case in cases if case['scenario'] == 'normal'
                                                   and case['scale_percent'] == scale), None)
                                 for scale in [100, 125, 200]}, 'blocked_initialization_attempts': blocked}
    out = ROOT / 'output/revision/system-summary.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'tests': report['tests'], 'passed': report['passed'], 'summary': str(out)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
