"""Save fault-injected recovery evidence; no desktop input, OCR model or remote API."""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gui_agent.recovery_http_fixture import HTTP_SCENARIOS, run_http_recovery
from gui_agent.recovery_simulation import EXPECTED, SCENARIOS, run_recovery_simulation
from gui_data.schema import safe_path
from gui_workflow.recovery import RecoveryPolicy


def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit_case(directory, result, expected):
    saved = read(directory / 'session/summary.json')
    fixture = read(directory / 'fixture.json'); actions = read(directory / 'simulated_actions.json')
    events = [json.loads(line) for line in (directory / 'session/recovery-events.jsonl').read_text(encoding='utf-8').splitlines()]
    status, code, requests, dispatches = expected
    checks = {'summary_matches': saved == result, 'expected_outcome': result['status'] == status and result.get('error', {}).get('code') == code,
              'requests_bounded': result['request_attempts'] == requests and requests <= 1 + result['policy']['max_replans'],
              'possible_dispatches_expected': result['input_dispatches_started'] == dispatches,
              'actual_control_entries_match': result['controller_dispatch_entries'] == len(actions),
              'observations_bounded': result['observation_attempts_used'] <= result['policy']['max_observations'],
              'dispatches_bounded': len(actions) <= result['policy']['max_dispatches'],
              'replans_bounded': result['replans_used'] <= result['policy']['max_replans'],
              'no_repeated_text_or_submission': sum(a['action'] == 'type_text' for a in actions) <= 1 and
                  sum(a['action'] == 'click' and a['point'][0] >= 350 for a in actions) <= 1,
              'not_real_desktop': result['environment'] == 'simulation' and result['desktop_input_sent'] is False,
              'verification_not_forged': result['task_verified'] == (status == 'completed'),
              'real_model_pending': result['real_model_validation'] == 'pending'}
    dispatched = False
    for event in events:
        if event['event'] == 'dispatch_started': dispatched = True
        if event['event'] == 'recovery_decision' and event['decision'] == 'replan_before_any_input' and dispatched:
            raise ValueError('Replan after a possible input dispatch.')
    checks['replan_events_precede_all_input'] = True
    if status == 'completed':
        checks['two_goal_channels'] = result['goal_verification']['ocr']['met'] and result['goal_verification']['state']['met']
        checks['state_really_submitted'] = fixture['final_state']['submitted'] == 'SUBMITTED ' + fixture['final_state']['entry_text']
        checks['all_subtasks_verified'] = all(s['status'] == 'verified' for s in result['cycles'][-1]['subtasks'])
    if directory.name == 'submitted_then_raise':
        checks['uncertain_commit_never_reclassified_as_success'] = result['diagnostic']['goal']['met'] and not result['task_verified'] and result['input_delivery_uncertain']
    if directory.name == 'partial_input': checks['partial_text_not_repeated'] = fixture['final_state']['entry_text'] == 'Hel' and len(actions) == 2
    if directory.name == 'second_step_state_failure':
        checks['dependencies_blocked'] = [s['status'] for s in result['cycles'][-1]['subtasks']] == ['verified', 'failed', 'blocked']
    if directory.name == 'false_ocr':
        workflow = read(directory / 'session/cycle-01/workflow/summary.json')
        checks['false_ocr_blocked_by_state'] = workflow['steps'][0]['verification']['ocr']['met'] and not workflow['steps'][0]['verification']['state']['met']
    if fixture['kind'] == 'loopback_http_fixture_not_real_model' and directory.name == 'planning_change_once':
        checks['fresh_http_context'] = fixture['contexts'][0]['independent_state']['status'] == 'WAITING' and fixture['contexts'][1]['independent_state']['status'] == 'HTTP CHANGED'
    screenshots = sorted(directory.rglob('screenshot.png'))
    for path in screenshots:
        report = read(path.parent / 'results.json')
        if report.get('synthetic_ocr') is not True: raise ValueError('Fixture screenshot must be labelled synthetic.')
    return {'case': directory.name, 'kind': fixture['kind'], 'status': result['status'], 'error_code': result.get('error', {}).get('code'),
            'error_category': result.get('error', {}).get('category'), 'request_attempts': result['request_attempts'],
            'replans': result['replans_used'], 'possible_dispatches': dispatches, 'actual_input_calls': len(actions),
            'observations': result['observation_attempts_used'], 'screenshots': len(screenshots),
            'summary': (directory / 'session/summary.json').relative_to(ROOT).as_posix(),
            'checks': checks, 'passed': all(checks.values())}


def audit_saved(directory):
    directory = Path(directory).resolve()
    report = read(directory / 'verification.json'); inventory = read(directory / 'evidence-manifest.json')
    if any(sha(safe_path(directory, item['path'])) != item['sha256'] for item in inventory['files']):
        raise ValueError('Saved recovery evidence hash mismatch.')
    if any(sha(safe_path(ROOT, name)) != expected for name, expected in report['source_sha256'].items()):
        raise ValueError('Recovery source differs from the recorded experiment.')
    expected_keys = {('simulated_app_ocr_state_and_input', n) for n in SCENARIOS} | {('loopback_http_fixture_not_real_model', n) for n in HTTP_SCENARIOS}
    actual_keys = [(c['kind'], c['case']) for c in report['cases']]
    if len(actual_keys) != len(expected_keys) or set(actual_keys) != expected_keys: raise ValueError('Missing or duplicate recovery case.')
    for case in report['cases']:
        case_dir = (ROOT / case['summary']).resolve().parents[1]
        if not case_dir.is_relative_to(directory): raise ValueError('Case evidence escapes the run directory.')
        result = read(case_dir / 'session/summary.json')
        expected = EXPECTED[case['case']] if case['case'] != 'rate_limited' else ('failed', 'rate_limited', 1, 0)
        if audit_case(case_dir, result, expected) != case or not case['passed']:
            raise ValueError('Saved case no longer matches independently recomputed checks.')
    project = read(ROOT / report['project_check'])
    if not project['all_passed'] or not report['all_passed']: raise ValueError('Project or recovery verification was not passing.')
    return {'all_passed': True, 'cases': len(actual_keys), 'files_verified': len(inventory['files']),
            'desktop_rerun': False, 'model_requests_sent': 0, 'directory': directory.relative_to(ROOT).as_posix()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-check', type=Path)
    parser.add_argument('--audit-only', type=Path, help='Verify an existing run without rerunning fixtures')
    args = parser.parse_args()
    if args.audit_only:
        print(json.dumps(audit_saved(args.audit_only))); return 0
    if args.project_check is None: parser.error('--project-check is required when generating new fixture evidence')
    output = ROOT / 'output/week3-thursday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output.mkdir(parents=True, exist_ok=False)
    policy = RecoveryPolicy.from_dict(read(ROOT / 'configs/week3-thursday.json')); cases = []
    for scenario in SCENARIOS:
        directory = output / 'simulation' / scenario
        result, _ = run_recovery_simulation(directory, scenario, policy)
        cases.append(audit_case(directory, result, EXPECTED[scenario]))
    for scenario in HTTP_SCENARIOS:
        directory = output / 'http' / scenario
        result, _, _ = run_http_recovery(directory, scenario, policy)
        expected = EXPECTED[scenario] if scenario != 'rate_limited' else ('failed', 'rate_limited', 1, 0)
        cases.append(audit_case(directory, result, expected))
    project = read(args.project_check)
    unit = next(c for c in project['checks'] if c['name'] == 'unit_tests')
    recovery_count = (ROOT / unit['log']).read_text(encoding='utf-8').count('(test_recovery.')
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'directory': output.relative_to(ROOT).as_posix(),
              'kind': 'simulated_recovery_and_loopback_http_evidence', 'new_unit_tests': recovery_count,
              'full_project_tests': unit['test_count'], 'project_check': str(args.project_check),
              'simulated_scenarios': len(SCENARIOS), 'loopback_http_scenarios': len(HTTP_SCENARIOS), 'cases': cases,
              'status_counts': dict(Counter(c['status'] for c in cases)),
              'failure_categories': dict(Counter(c['error_category'] for c in cases if c['error_category'])),
              'screenshots': sum(c['screenshots'] for c in cases),
              'source_sha256': {p: sha(ROOT / p) for p in ('configs/week3-thursday.json', 'gui_workflow/recovery.py', 'gui_agent/recovery.py',
                  'gui_agent/recovery_simulation.py', 'gui_agent/recovery_http_fixture.py', 'gui_workflow/verification.py', 'gui_workflow/observer.py', 'run_agent.py')},
              'desktop_input_sent': False, 'new_real_desktop_scenarios': 0, 'real_model_validation': 'pending', 'nim_validation': 'pending',
              'all_passed': all(c['passed'] for c in cases) and project['all_passed'] and recovery_count == 45}
    with (output / 'cases.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        columns = ['case', 'kind', 'status', 'error_code', 'error_category', 'request_attempts', 'replans', 'possible_dispatches', 'actual_input_calls', 'observations', 'screenshots', 'passed', 'summary']
        writer = csv.DictWriter(stream, columns, extrasaction='ignore'); writer.writeheader(); writer.writerows(cases)
    inventory = {'files': [{'path': p.relative_to(output).as_posix(), 'sha256': sha(p)} for p in sorted(output.rglob('*')) if p.is_file()]}
    (output / 'evidence-manifest.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (ROOT / 'output/week3-thursday/verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'all_passed': report['all_passed'], 'cases': len(cases), 'new_tests': recovery_count,
                      'full_project_tests': unit['test_count'], 'directory': report['directory']}))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
