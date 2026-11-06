"""Validate the Friday task suite and audit saved desktop evidence."""

import argparse
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gui_tasks.basic import audit_result
from run_basic_tasks import run_suite


def audit_desktop(path):
    report = json.loads(path.read_text(encoding='utf-8'))
    rows = []
    if report.get('environment') != 'desktop':
        raise ValueError('Desktop audit requires a real desktop report.')
    for row in report['cases']:
        checked = dict(row)
        summary_path = row.get('summary')
        if summary_path:
            summary_path = Path(summary_path)
            summary = json.loads(summary_path.read_text(encoding='utf-8'))
            values = json.loads((summary_path.parents[1] / 'application-final.json').read_text(encoding='utf-8'))
            checked = {**audit_result(summary, values, row['case'], row['fault']), 'summary': str(summary_path)}
            browser_path = summary_path.parents[1] / 'browser.json'
            browser = json.loads(browser_path.read_text(encoding='utf-8')) if browser_path.exists() else {}
            checked['assertions']['browser_identity_recorded'] = bool(browser.get('hwnd') and browser.get('pid') and browser.get('page_loaded'))
            if row['case'] in ('open_browser', 'close_app') and row['fault'] == 'none':
                checked['assertions']['owned_browser_lifecycle'] = browser.get('window_open_after_task') is (row['case'] == 'open_browser')
            checked['passed'] = checked['passed'] and all(checked['assertions'].values())
        rows.append(checked)
    return {'report': str(path), 'cases': rows, 'all_passed': bool(rows) and all(row['passed'] for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop-report', type=Path, action='append', default=[])
    parser.add_argument('--project-check', type=Path)
    args = parser.parse_args()
    directory = ROOT / 'output/week2-friday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    directory.mkdir(parents=True)
    stream = StringIO()
    class Results(unittest.TextTestResult):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.cases = []
        def addSuccess(self, test):
            super().addSuccess(test); self.cases.append({'case': test.id(), 'passed': True})
        def addFailure(self, test, err):
            super().addFailure(test, err); self.cases.append({'case': test.id(), 'passed': False})
        def addError(self, test, err):
            super().addError(test, err); self.cases.append({'case': test.id(), 'passed': False})
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(
        unittest.defaultTestLoader.loadTestsFromName('tests.test_basic_tasks'))
    (directory / 'unit_tests.log').write_text(stream.getvalue(), encoding='utf-8')
    simulated = run_suite(directory / 'simulation', protection=True)
    attempts = [audit_desktop(path) for path in args.desktop_report]
    # Latest supplied occurrence of each case/fault is current; retain every attempt.
    latest = {}
    for attempt in attempts:
        for row in attempt['cases']:
            latest[(row['case'], row['fault'])] = row
    desktop = list(latest.values())
    normal_names = {row['case'] for row in desktop if row['fault'] == 'none' and row['passed']}
    full_desktop = len(normal_names) == 5
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'directory': directory.relative_to(ROOT).as_posix(),
              'unit_test_count': result.testsRun, 'unit_cases': result.cases,
              'simulation_report': str(directory / 'simulation/summary.json'), 'simulation_cases': simulated['cases'],
              'offline_all_passed': result.wasSuccessful() and simulated['all_passed'],
              'desktop_attempts': attempts, 'desktop_cases': desktop,
              'desktop_validation': 'failed' if any(not row['passed'] for row in desktop) else 'passed' if full_desktop else 'pending',
              'desktop_normal_verified': len(normal_names), 'real_model_validation': 'pending'}
    if args.project_check:
        project = json.loads(args.project_check.read_text(encoding='utf-8'))
        report.update(project_check=str(args.project_check), full_project_passed=project['all_passed'],
                      full_project_tests=next(row['test_count'] for row in project['checks'] if row['name'] == 'unit_tests'))
    report['all_passed'] = report['offline_all_passed'] and report.get('full_project_passed', True) and report['desktop_validation'] == 'passed'
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (directory / 'verification.json').write_text(encoded, encoding='utf-8')
    (ROOT / 'output/week2-friday/verification.json').write_text(encoded, encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('all_passed', 'unit_test_count', 'desktop_validation', 'desktop_normal_verified', 'directory')}))
    return 0 if report['offline_all_passed'] and report.get('full_project_passed', True) and report['desktop_validation'] != 'failed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
