"""Offline end-to-end scenarios plus optional audit of saved real GUI runs."""

import argparse
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO, BytesIO
import json
from pathlib import Path
import sys
from threading import Thread
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from gui_agent import ChatClient, GuiPlanner, ModelConfig
from gui_agent.session import AgentSession
from gui_agent.simulation import FIELDS, INSTRUCTION, SimulatedApp, SimulatedObserver, run_simulation
from gui_workflow.runner import WorkflowRunner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-check', type=Path)
    parser.add_argument('--desktop-root', type=Path, action='append', default=[])
    args = parser.parse_args()
    directory = ROOT / 'output/week2-thursday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    directory.mkdir(parents=True, exist_ok=False)

    class Results(unittest.TextTestResult):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw); self.cases = []
        def addSuccess(self, test):
            super().addSuccess(test); self.cases.append({'case': test.id(), 'passed': True})
        def addFailure(self, test, err):
            super().addFailure(test, err); self.cases.append({'case': test.id(), 'passed': False})
        def addError(self, test, err):
            super().addError(test, err); self.cases.append({'case': test.id(), 'passed': False})

    stream = StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(
        unittest.defaultTestLoader.loadTestsFromName('tests.test_session'))
    (directory / 'session_tests.log').write_text(stream.getvalue(), encoding='utf-8')
    cases = []
    for scenario, status, code, dispatches in [
        ('normal', 'completed', None, 3), ('duplicate', 'failed', 'AmbiguousTarget', 0),
        ('missing', 'failed', 'target_unavailable', 0), ('no_change', 'failed', 'verification_timeout', 1),
        ('false_ocr', 'failed', 'verification_timeout', 1), ('partial_input', 'failed', 'RuntimeError', 2),
    ]:
        summary, app = run_simulation(directory / scenario, scenario)
        cases.append({'case': scenario, 'kind': 'simulated_app_and_ocr', 'status': summary['status'],
                      'error_code': summary.get('error', {}).get('code'), 'dispatches': summary['input_dispatches_started'],
                      'passed': summary['status'] == status and summary.get('error', {}).get('code') == code and
                                summary['input_dispatches_started'] == dispatches and not summary['desktop_input_sent'],
                      'summary': (directory / scenario / 'session/summary.json').relative_to(ROOT).as_posix()})
    goal = json.loads((ROOT / 'docs/agent-goal.example.json').read_text(encoding='utf-8'))
    other = json.loads(json.dumps(goal)); other['text'] = 'OTHER RESULT'; other['state']['submitted'] = 'OTHER RESULT'
    for name, options, expected in [
        ('preview', {'execute': False}, 'planned'),
        ('state_changed', {'after_planning': lambda a: a.values.update(entry_text='external')}, 'failed'),
        ('stopped', {'after_planning': lambda a: setattr(a, 'stopped', True)}, 'aborted'),
        ('model_done', {'reply': {'status': 'done', 'reason': 'Model opinion only', 'subtasks': []}}, 'failed'),
        ('clarify', {'reply': {'status': 'clarify', 'reason': 'Which target?', 'subtasks': []}}, 'needs_clarification'),
        ('goal_mismatch', {'goal': other}, 'failed'),
    ]:
        summary, app = run_simulation(directory / name, **options)
        cases.append({'case': name, 'kind': 'simulated_app_and_ocr', 'status': summary['status'],
                      'error_code': summary.get('error', {}).get('code'), 'dispatches': summary['input_dispatches_started'],
                      'passed': summary['status'] == expected and not summary['desktop_input_sent'] and
                                (len(app.calls) == 3 if name == 'goal_mismatch' else not app.calls),
                      'summary': (directory / name / 'session/summary.json').relative_to(ROOT).as_posix()})
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            parts = request['messages'][1]['content']
            context = json.loads(parts[2]['text'].split('\n', 1)[1])['live_observation']
            image = next(x['image_url']['url'] for x in parts if x['type'] == 'image_url')
            with Image.open(BytesIO(base64.b64decode(image.split(',')[1]))) as frame:
                size_ok = frame.size == (640, 360)
            calls.append({'has_live_ocr': bool(context['ocr']), 'has_actual_state': context['independent_state']['status'] == 'WAITING',
                          'has_caller_goal': context['goal']['text'] == goal['text'], 'image_size_ok': size_ok})
            value = json.loads((ROOT / 'docs/agent-plan.example.json').read_text(encoding='utf-8'))
            if '/bad/' in self.path:
                value['subtasks'][-1]['steps'][0]['action'] = 'shell'
            body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True); thread.start()
        try:
            for route in ['ok', 'bad']:
                folder = directory / ('http_' + route)
                app = SimulatedApp(); observer = SimulatedObserver(app, folder / 'observations')
                client = ChatClient(ModelConfig(f'http://127.0.0.1:{server.server_port}/{route}/v1', 'fixture-not-real-model', timeout=3))
                runner = lambda c, o, p: WorkflowRunner(c, o, p, poll_interval=.1, clock=lambda: app.time, sleep=app.sleep)
                summary = AgentSession(app, observer, GuiPlanner(client), folder / 'session', 'simulation', runner).run(
                    INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal, execute=True)
                expected = 'completed' if route == 'ok' else 'failed'
                cases.append({'case': 'http_' + route, 'kind': 'loopback_http_and_simulated_execution',
                              'status': summary['status'], 'transport': calls[-1],
                              'passed': summary['status'] == expected and client.attempts == 1 and all(calls[-1].values()) and
                                        (len(app.calls) == 3 if route == 'ok' else not app.calls),
                              'summary': (folder / 'session/summary.json').relative_to(ROOT).as_posix()})
        finally:
            server.shutdown(); thread.join(timeout=2)
    desktop_attempts, desktop_cases = [], []
    for root in args.desktop_root:
        attempts = []
        for path in sorted(root.glob('*/summary.json')):
            summary = json.loads(path.read_text(encoding='utf-8'))
            row = {'summary': path.as_posix(), 'status': summary['status'], 'environment': summary.get('environment'),
                   'success': summary.get('success'), 'task_verified': summary.get('task_verified'),
                   'error': summary.get('error'), 'display': summary.get('display'),
                   'actions_completed': summary.get('actions_completed'), 'client_kind': summary.get('client_kind')}
            row['scenario'] = summary.get('scenario')
            attempts.append(row)
        desktop_attempts.extend(attempts)
        if attempts:
            row = attempts[-1]
            latest = json.loads(Path(row['summary']).read_text(encoding='utf-8'))
            expected = {'normal': ('completed', None, 3), 'false_ocr': ('failed', 'verification_timeout', 1),
                        'no_change': ('failed', 'verification_timeout', 1), 'duplicate': ('failed', 'AmbiguousTarget', 0),
                        'missing': ('failed', 'target_unavailable', 0)}.get(row['scenario'])
            passed = bool(expected) and row['environment'] == 'desktop' and row['status'] == expected[0] and \
                     (row.get('error') or {}).get('code') == expected[1] and row['actions_completed'] == expected[2]
            if row['scenario'] == 'normal':
                passed = passed and row['success'] is True and row['task_verified'] is True
            assertions = {}
            workflow_path = latest.get('workflow_summary')
            workflow = json.loads(Path(workflow_path).read_text(encoding='utf-8')) if workflow_path and Path(workflow_path).exists() else {}
            steps = workflow.get('steps', [])
            if row['scenario'] == 'normal':
                assertions['three_steps_double_checked'] = len(steps) == 3 and all(
                    step.get('verified') and step.get('verification', {}).get('ocr', {}).get('met') and
                    step.get('verification', {}).get('state', {}).get('met') for step in steps)
                goal = latest.get('goal_verification', {})
                assertions['caller_goal_double_checked'] = bool(goal.get('met') and goal.get('ocr', {}).get('met') and goal.get('state', {}).get('met'))
                observation = latest.get('observations', [None])[-1]
            else:
                evidence = steps[-1].get('verification', {}) if steps else {}
                observation = evidence.get('directory')
                if row['scenario'] == 'false_ocr':
                    assertions['ocr_false_positive_rejected'] = bool(evidence.get('ocr', {}).get('met') and
                        evidence.get('state', {}).get('met') is False and evidence.get('met') is False)
                    assertions['one_step_only'] = len(steps) == 1 and not steps[0].get('verified')
            assertions['observation_assets_exist'] = bool(observation) and all(
                (Path(observation) / name).is_file() for name in ['screenshot.png', 'results.json', 'state.json'])
            passed = passed and all(assertions.values())
            desktop_cases.append({'case': root.name, 'passed': passed, 'assertions': assertions, **row})
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'directory': directory.relative_to(ROOT).as_posix(),
              'unit_test_count': result.testsRun, 'unit_cases': result.cases, 'integration_cases': cases,
              'offline_all_passed': result.wasSuccessful() and all(c['passed'] for c in cases),
              'desktop_attempts': desktop_attempts, 'desktop_cases': desktop_cases,
              'desktop_validation': 'pending' if not desktop_cases else 'passed' if all(c['passed'] for c in desktop_cases) else 'failed',
              'real_model_validation': 'pending'}
    if args.project_check:
        project = json.loads(args.project_check.read_text(encoding='utf-8'))
        report['project_check'] = args.project_check.as_posix()
        report['full_project_tests'] = next(c['test_count'] for c in project['checks'] if c['name'] == 'unit_tests')
        report['full_project_passed'] = project['all_passed']
    report['all_passed'] = report['offline_all_passed'] and report.get('full_project_passed', True) and report['desktop_validation'] != 'failed'
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (directory / 'verification.json').write_text(encoded, encoding='utf-8')
    (ROOT / 'output/week2-thursday/verification.json').write_text(encoded, encoding='utf-8')
    print(json.dumps({'all_passed': report['all_passed'], 'unit_tests': result.testsRun, 'integration_cases': len(cases),
                      'desktop_validation': report['desktop_validation'], 'report': str(directory / 'verification.json')}, ensure_ascii=True))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
