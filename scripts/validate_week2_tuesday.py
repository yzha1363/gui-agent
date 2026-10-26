"""Validate the agent against replay and a loopback HTTP fixture, never a real model."""

import argparse
import base64
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from io import StringIO
import json
from pathlib import Path
import sys
from threading import Thread
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw

from agent_cli import DEMO_RESPONSE
from gui_agent import AgentInput, ChatClient, GuiAgent, ModelConfig, ReplayClient
from gui_agent.contracts import load_dataset_input


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path)
    parser.add_argument('--project-check', type=Path)
    args = parser.parse_args()
    directory = ROOT / 'output/week2-tuesday' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    directory.mkdir(parents=True, exist_ok=False)

    class Results(unittest.TextTestResult):
        cases = []
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
        unittest.defaultTestLoader.loadTestsFromName('tests.test_agent'))
    (directory / 'agent_tests.log').write_text(stream.getvalue(), encoding='utf-8')
    image = Image.new('RGB', (640, 360), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((24, 24), 'Synthetic HTTP interface fixture', fill='black')
    draw.rectangle((24, 90, 610, 150), outline='black', width=2)
    draw.text((40, 112), 'INPUT FIELD', fill='black')
    image.save(directory / 'fixture.png')
    task = AgentInput('Focus INPUT FIELD; 中文接口测试', directory / 'fixture.png', 'Test window',
                      state_fields=('input_focused', 'status'))
    cases = []
    replay = GuiAgent(ReplayClient(json.dumps(DEMO_RESPONSE))).run(task, directory / 'replay')
    cases.append({'case': 'replay_chain', 'kind': 'replay', 'passed': replay['status'] == 'proposal_ready',
                  'summary': str((directory / 'replay/summary.json').relative_to(ROOT))})
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            user = request['messages'][1]['content']
            data_url = next(p['image_url']['url'] for p in user if p['type'] == 'image_url')
            input_matches = base64.b64decode(data_url.split(',')[1]) == (directory / 'fixture.png').read_bytes()
            route = self.path.split('/')[1]
            calls.append({'route': route, 'image_bytes_match': input_matches,
                          'instruction_present': '中文接口测试' in user[0]['text'],
                          'authorization_matches': self.headers.get('Authorization') == 'Bearer fixture-only-secret'})
            status = {'auth': 401, 'rate': 429}.get(route, 200)
            content = '{invalid' if route == 'badjson' else json.dumps(DEMO_RESPONSE)
            response = {'choices': [{'finish_reason': 'length' if route == 'truncated' else 'stop',
                                     'message': {'content': content}}]}
            body = json.dumps(response).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        try:
            for route, expected in [('ok', None), ('auth', 'authentication_failed'), ('rate', 'rate_limited'),
                                    ('badjson', 'invalid_json'), ('truncated', 'incomplete_response')]:
                client = ChatClient(ModelConfig(f'http://127.0.0.1:{server.server_port}/{route}/v1',
                                               'fixture-vlm-not-real-model', 'fixture-only-secret', timeout=3))
                actual = GuiAgent(client).run(task, directory / route)
                passed = (actual['status'] == 'proposal_ready' if expected is None else
                          actual['status'] == 'failed' and actual['error']['code'] == expected)
                transport = calls[-1]
                passed = passed and client.attempts == 1 and all(transport[k] for k in (
                    'image_bytes_match', 'instruction_present', 'authorization_matches'))
                cases.append({'case': 'http_' + route, 'kind': 'loopback_http_fixture', 'passed': passed,
                              'expected_error': expected, 'actual_error': actual.get('error', {}).get('code'),
                              'requests': client.attempts, **transport,
                              'summary': str((directory / route / 'summary.json').relative_to(ROOT))})
        finally:
            server.shutdown()
            thread.join(timeout=2)

    if args.dataset:
        task = load_dataset_input(args.dataset, 0, 'Offline dataset sample')
        clarification = json.dumps({'status': 'clarify', 'reason': 'Offline sample has no live state adapter.', 'step': None})
        actual = GuiAgent(ReplayClient(clarification)).run(task, directory / 'monday-sample')
        cases.append({'case': 'monday_sample_input', 'kind': 'real_public_image_with_replay_reply',
                      'passed': actual['status'] == 'clarify',
                      'summary': str((directory / 'monday-sample/summary.json').relative_to(ROOT))})

    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'langchain_core': version('langchain-core'),
              'unit_test_count': result.testsRun, 'unit_tests_passed': result.wasSuccessful(),
              'unit_cases': result.cases, 'integration_cases': cases, 'desktop_input_sent': False,
              'real_model_validation': 'pending', 'integration_backend': 'synthetic loopback HTTP fixture, not a model',
              'directory': directory.relative_to(ROOT).as_posix()}
    if args.project_check:
        project = json.loads(args.project_check.read_text(encoding='utf-8'))
        report['project_check'] = args.project_check.as_posix()
        report['full_project_tests'] = next(c['test_count'] for c in project['checks'] if c['name'] == 'unit_tests')
        report['full_project_passed'] = project['all_passed']
    report['all_passed'] = result.wasSuccessful() and all(c['passed'] for c in cases) and report.get('full_project_passed', True)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    (directory / 'verification.json').write_text(encoded, encoding='utf-8')
    (ROOT / 'output/week2-tuesday/verification.json').write_text(encoded, encoding='utf-8')
    print(json.dumps({'all_passed': report['all_passed'], 'unit_tests': result.testsRun,
                      'integration_cases': len(cases), 'report': str(directory / 'verification.json')}, ensure_ascii=True))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
