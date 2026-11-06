"""Week-two Friday: reproducible basic tasks, simulation by default."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from gui_tasks.basic import CASES, FAULTS, audit_result, run_synthetic

ROOT = Path(__file__).resolve().parent
PROTECTION_CASES = [('search', 'missing'), ('search', 'duplicate'), ('open_file', 'no_change'),
                    ('open_file', 'false_ocr'), ('open_file', 'permission_denied')]


def run_suite(directory, desktop=False, case=None, fault='none', device='cuda', protection=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    matrix = [(case, fault)] if case else [(name, 'none') for name in CASES]
    if not case and protection:
        matrix += PROTECTION_CASES
    if desktop:
        from gui_tasks.browser import run_desktop
        run = lambda c, p, f: run_desktop(c, p, f, device)
    else:
        run = run_synthetic
    rows = []
    for name, injected in matrix:
        path = directory / (name + '-' + injected)
        print(f'{name} / {injected}: running ({"desktop" if desktop else "simulation"})', flush=True)
        try:
            summary, app = run(name, path, injected)
            row = audit_result(summary, app.snapshot(), name, injected)
            row['summary'] = str(path / 'session/summary.json')
            (path / 'application-final.json').write_text(json.dumps(app.snapshot(), indent=2), encoding='utf-8')
        except (Exception, KeyboardInterrupt) as exc:
            from gui_workflow.verification import failure_category
            row = {'case': name, 'fault': injected, 'environment': 'desktop' if desktop else 'simulation',
                   'status': 'initialization_failed', 'task_verified': False, 'passed': False,
                   'error_code': type(exc).__name__, 'failure_category': failure_category(exc), 'message': str(exc)}
        rows.append(row)
        print(f'{name} / {injected}: {"PASS" if row["passed"] else "FAIL"} ({row["status"]})', flush=True)
        if row.get('error_code') in ('KeyboardInterrupt', 'StopRequested', 'FailSafeException') or row['status'] == 'aborted':
            break
    normal = [row for row in rows if row['fault'] == 'none']
    failures = Counter(row.get('application_cause') or row.get('failure_category') or 'unclassified'
                       for row in rows if row['status'] != 'completed')
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'environment': 'desktop' if desktop else 'simulation',
              'client_kind': 'replay', 'real_model_validation': 'pending', 'cases': rows,
              'test_count': len(rows), 'test_passed_count': sum(row['passed'] for row in rows),
              'normal_task_count': len(normal), 'normal_tasks_verified': sum(row['task_verified'] for row in normal),
              'normal_task_success_rate': sum(row['task_verified'] for row in normal) / len(normal) if normal else None,
              'failure_categories_including_injections': dict(failures),
              'all_passed': len(rows) == len(matrix) and all(row['passed'] for row in rows),
              'scope': 'Controlled local pages/files/inbox and one owned browser; no external message delivery.'}
    (directory / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true', help='Use real OCR and input in owned test windows')
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--fault', choices=FAULTS, default='none')
    parser.add_argument('--with-protection', action='store_true', help='Also run five expected failure/protection cases')
    parser.add_argument('--device', choices=['auto', 'cpu', 'cuda'], default='cuda')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.fault != 'none' and not args.case:
        parser.error('--fault requires --case')
    if args.fault in ('missing', 'duplicate') and args.case not in ('search', 'send_message'):
        parser.error('missing/duplicate require an input task')
    if args.fault in ('permission_denied', 'no_change', 'false_ocr') and args.case != 'open_file':
        parser.error('permission_denied/no_change/false_ocr require open_file')
    if args.case and args.with_protection:
        parser.error('--with-protection is for the full suite')
    directory = args.output or ROOT / 'output/week2-friday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    report = run_suite(directory, args.desktop, args.case, args.fault, args.device, args.with_protection)
    print(json.dumps({'all_passed': report['all_passed'], 'normal_tasks_verified': report['normal_tasks_verified'],
                      'test_passed_count': report['test_passed_count'], 'test_count': report['test_count'],
                      'summary': str(directory / 'summary.json')}, ensure_ascii=True))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
