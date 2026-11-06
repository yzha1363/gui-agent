"""Record offline planning tests, candidate checks and loopback HTTP integration."""

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from gui_agent import AgentInput, ChatClient, GuiPlanner, ModelConfig, ReplayClient
from gui_agent.contracts import load_dataset_input
from gui_workflow.plan import validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--project-check', type=Path)
    args = parser.parse_args()
    directory = ROOT / 'output/week2-wednesday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    directory.mkdir(parents=True, exist_ok=False)

    class Results(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.cases = []
        def addSuccess(self, test):
            super().addSuccess(test)
            self.cases.append({'case': test.id(), 'passed': True})
        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.cases.append({'case': test.id(), 'passed': False, 'kind': 'failure'})
        def addError(self, test, err):
            super().addError(test, err)
            self.cases.append({'case': test.id(), 'passed': False, 'kind': 'error'})

    stream = StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Results).run(
        unittest.defaultTestLoader.loadTestsFromName('tests.test_planner'))
    (directory / 'planner_tests.log').write_text(stream.getvalue(), encoding='utf-8')
    image = Image.new('RGB', (640, 360), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((24, 24), 'Synthetic task-planning fixture', fill='black')
    draw.rectangle((24, 90, 610, 150), outline='black', width=2)
    draw.text((40, 112), 'INPUT FIELD', fill='black')
    draw.rectangle((24, 205, 180, 250), outline='black', width=2)
    draw.text((60, 220), 'SUBMIT', fill='black')
    image.save(directory / 'fixture.png')
    fields = ('input_focused', 'status', 'entry_text', 'submitted', 'submit_enabled')
    task = AgentInput('Focus INPUT FIELD, type exactly Hello GUI 123 中文测试, then click SUBMIT.',
                      directory / 'fixture.png', 'GUI Agent Friday Demo', state_fields=fields)
    good = json.loads((ROOT / 'docs/agent-plan.example.json').read_text(encoding='utf-8'))
    cycle = deepcopy(good); cycle['subtasks'][0]['depends_on'] = ['submit']
    bad = deepcopy(good); bad['subtasks'][-1]['steps'][0]['action'] = 'shell'
    unknown = deepcopy(good); unknown['subtasks'][-1]['steps'][0]['expect']['state'] = {'invented': True}
    clarify = {'status': 'clarify', 'reason': 'Offline screenshot has no live state adapter.', 'subtasks': []}
    waiting = {'status': 'plan', 'reason': 'Observe until the declared state is ready.', 'subtasks': [
        {'id': 'wait', 'goal': 'Wait for readiness', 'depends_on': [], 'steps': [
            {'name': 'wait_ready', 'action': 'wait_text', 'expect': {'text': 'READY', 'state': {'status': 'READY'}}}]}]}
    cases = []

    def run_case(name, client, expected_status, expected_error=None, max_steps=12, input_task=task, kind='replay'):
        summary = GuiPlanner(client, max_steps).run(input_task, directory / name)
        passed = summary['status'] == expected_status and summary.get('error', {}).get('code') == expected_error
        candidate = directory / name / 'workflow.candidate.json'
        if expected_status == 'plan_ready':
            passed = passed and candidate.exists()
            if candidate.exists():
                workflow = json.loads(candidate.read_text(encoding='utf-8'))
                passed = passed and bool(validate_plan(workflow)['steps'])
        else:
            passed = passed and not candidate.exists()
        row = {'case': name, 'kind': kind, 'passed': passed, 'expected_status': expected_status,
               'actual_status': summary['status'], 'expected_error': expected_error,
               'actual_error': summary.get('error', {}).get('code'), 'request_attempts': summary['request_attempts'],
               'candidate_exported': candidate.exists(), 'summary': (directory / name / 'summary.json').relative_to(ROOT).as_posix()}
        cases.append(row)
        return row

    for name, value, status, error, limit in [
        ('three_step_plan', good, 'plan_ready', None, 12),
        ('wait_only_plan', waiting, 'plan_ready', None, 12),
        ('dependency_cycle', cycle, 'failed', 'invalid_dependency', 12),
        ('invalid_last_action', bad, 'failed', 'invalid_task_plan', 12),
        ('unknown_state_field', unknown, 'failed', 'unsupported_state_field', 12),
        ('step_limit', good, 'failed', 'plan_limit_exceeded', 2),
        ('clarify', clarify, 'clarify', None, 12),
    ]:
        run_case(name, ReplayClient(json.dumps(value, ensure_ascii=False)), status, error, limit)

    candidate = directory / 'three_step_plan/workflow.candidate.json'
    process = subprocess.run([sys.executable, 'run_workflow.py', '--plan', str(candidate), '--validate-only'],
                             cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    (directory / 'workflow_validation.log').write_text(process.stdout + process.stderr, encoding='utf-8')
    cases.append({'case': 'workflow_cli_validation', 'kind': 'existing_workflow_cli_structure_only',
                  'passed': process.returncode == 0, 'return_code': process.returncode})
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            parts = request['messages'][1]['content']
            data_url = next(p['image_url']['url'] for p in parts if p['type'] == 'image_url')
            route = self.path.split('/')[1]
            calls.append({'route': route,
                          'image_bytes_match': base64.b64decode(data_url.split(',')[1]) == (directory / 'fixture.png').read_bytes(),
                          'task_text_present': '中文测试' in parts[0]['text'],
                          'planning_prompt_present': 'Maximum total action count: 12' in request['messages'][0]['content'],
                          'authorization_matches': self.headers.get('Authorization') == 'Bearer fixture-only-secret'})
            value = good if route == 'ok' else cycle
            body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value, ensure_ascii=False)}}]}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        try:
            for route, status, error in [('ok', 'plan_ready', None), ('cycle', 'failed', 'invalid_dependency')]:
                client = ChatClient(ModelConfig(f'http://127.0.0.1:{server.server_port}/{route}/v1',
                                               'fixture-vlm-not-real-model', 'fixture-only-secret', timeout=3))
                row = run_case('http_' + route, client, status, error, kind='loopback_http_fixture')
                row['transport'] = calls[-1]
                row['passed'] = row['passed'] and client.attempts == 1 and all(calls[-1][k] for k in (
                    'image_bytes_match', 'task_text_present', 'planning_prompt_present', 'authorization_matches'))
        finally:
            server.shutdown()
            thread.join(timeout=2)

    if args.dataset:
        dataset_task = load_dataset_input(args.dataset, 0, 'Offline dataset sample')
        run_case('monday_sample', ReplayClient(json.dumps(clarify)), 'clarify', input_task=dataset_task,
                 kind='real_public_image_with_replay_reply')

    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'directory': directory.relative_to(ROOT).as_posix(),
              'unit_test_count': result.testsRun, 'unit_tests_passed': result.wasSuccessful(), 'unit_cases': result.cases,
              'integration_cases': cases, 'desktop_input_sent': False, 'real_model_validation': 'pending',
              'backend': 'replay and synthetic loopback HTTP service, not model inference'}
    if args.project_check:
        project = json.loads(args.project_check.read_text(encoding='utf-8'))
        report['project_check'] = args.project_check.as_posix()
        report['full_project_tests'] = next(c['test_count'] for c in project['checks'] if c['name'] == 'unit_tests')
        report['full_project_passed'] = project['all_passed']
    report['all_passed'] = result.wasSuccessful() and all(c['passed'] for c in cases) and report.get('full_project_passed', True)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (directory / 'verification.json').write_text(encoded, encoding='utf-8')
    (ROOT / 'output/week2-wednesday/verification.json').write_text(encoded, encoding='utf-8')
    print(json.dumps({'all_passed': report['all_passed'], 'unit_tests': result.testsRun,
                      'integration_cases': len(cases), 'report': str(directory / 'verification.json')}, ensure_ascii=True))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
