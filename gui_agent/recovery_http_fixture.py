"""Real loopback HTTP transport with fixture responses, never a real model."""

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from gui_agent import ChatClient, GuiPlanner, ModelConfig
from gui_agent.recovery import RecoverySession
from gui_agent.simulation import FIELDS, INSTRUCTION, SimulatedApp, SimulatedObserver
from gui_workflow.recovery import RecoveryPolicy

HTTP_SCENARIOS = ('normal', 'planning_change_once', 'rate_limited', 'stop_during_model')


def run_http_recovery(output, scenario='normal', policy=None):
    if scenario not in HTTP_SCENARIOS: raise ValueError('Unknown HTTP fixture scenario.')
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    app = SimulatedApp(); observer = SimulatedObserver(app, output / 'observations'); requests = []
    reply = json.loads((root / 'docs/agent-plan.example.json').read_text(encoding='utf-8'))
    for subtask in reply['subtasks']:
        for step in subtask['steps']: step['timeout'] = .2
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            if scenario == 'planning_change_once' and len(requests) == 1: app.values['status'] = 'HTTP CHANGED'
            if scenario == 'stop_during_model': app.stopped = True
            code = 429 if scenario == 'rate_limited' else 200
            value = {'error': 'fixture rate limit'} if code != 200 else {
                'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': json.dumps(reply, ensure_ascii=False)}}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            body = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); thread = Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        client = ChatClient(ModelConfig(f'http://127.0.0.1:{server.server_port}/v1', 'loopback-recovery-fixture', timeout=2))
        goal = json.loads((root / 'docs/agent-goal.example.json').read_text(encoding='utf-8'))
        summary = RecoverySession(app, observer, GuiPlanner(client), output / 'session', policy or RecoveryPolicy(), environment='simulation',
                                  clock=lambda: app.time, sleep=app.sleep).run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal, execute=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
    (output / 'simulated_actions.json').write_text(json.dumps(app.calls, ensure_ascii=False, indent=2), encoding='utf-8')
    # Store only allowlisted text context, not base64 copies or authorization headers.
    contexts = [json.loads(r['messages'][1]['content'][2]['text'].split('\n', 1)[1])['live_observation'] for r in requests]
    (output / 'fixture.json').write_text(json.dumps({'kind': 'loopback_http_fixture_not_real_model', 'scenario': scenario,
        'request_count': len(requests), 'contexts': contexts, 'final_state': deepcopy(app.values)}, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary, app, contexts
