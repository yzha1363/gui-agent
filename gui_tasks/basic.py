"""Five basic task contracts and independent local application state."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

from gui_agent.client import ReplayClient
from gui_agent.planner import GuiPlanner, PLANNER_KEYS
from gui_agent.session import AgentSession
from gui_control.controller import StopRequested
from gui_workflow.runner import WorkflowRunner, evaluate_observation

CASES = ('open_browser', 'search', 'open_file', 'send_message', 'close_app')
FAULTS = ('none', 'missing', 'duplicate', 'no_change', 'false_ocr', 'permission_denied')
SAMPLE = 'GUI Agent sample file. Read only.\n'
MESSAGE = 'Hello local inbox'
QUERY = 'GUI Agent'
FIELDS = ('status', 'focused', 'draft', 'result', 'file_open', 'file_sha256',
          'message_count', 'last_message', 'browser_open', 'app_closed', 'app_error')


@dataclass(frozen=True)
class TaskSpec:
    case: str
    instruction: str
    steps: tuple
    goal: dict


def task_spec(case):
    if case not in CASES:
        raise ValueError('Unknown basic task.')
    final = {
        'open_browser': ('BROWSER OPEN', {'browser_open': True}),
        'search': ('FOUND GUI Agent', {'result': 'FOUND GUI Agent', 'draft': QUERY}),
        'open_file': ('FILE OPEN', {'file_open': True, 'file_sha256': hashlib.sha256(SAMPLE.encode()).hexdigest()}),
        'send_message': ('MESSAGE SENT', {'message_count': 1, 'last_message': MESSAGE}),
        'close_app': ('APP CLOSED', {'app_closed': True, 'browser_open': False}),
    }
    text, state = final[case]
    goal = {'text': text, 'min_confidence': .3, 'state': state}
    def click(name, target, expect):
        return {'name': name, 'action': 'click_text', 'text': target, 'min_confidence': .5,
                'expect': expect, 'timeout': 5, 'lookup_attempts': 2}
    steps = []
    if case in ('search', 'send_message'):
        value = QUERY if case == 'search' else MESSAGE
        steps.extend([
            click('focus', 'FOCUS INPUT', {'text': 'INPUT READY', 'state': {'focused': True}, 'min_confidence': .3}),
            {'name': 'type', 'action': 'type_text', 'text': value, 'timeout': 5,
             'expect': {'text': value, 'state': {'draft': value}, 'min_confidence': .3}},
        ])
    targets = {'open_browser': 'OPEN BROWSER', 'search': 'SEARCH NOW', 'open_file': 'OPEN FILE',
               'send_message': 'SEND LOCAL', 'close_app': 'CLOSE APP'}
    steps.append(click('finish', targets[case], deepcopy(goal)))
    instructions = {
        'open_browser': 'Click OPEN BROWSER to launch the isolated local test browser.',
        'search': 'Focus the input, type GUI Agent, and search the local catalog.',
        'open_file': 'Open the provided read-only sample file.',
        'send_message': 'Focus the input, type Hello local inbox, and send to the local test inbox once.',
        'close_app': 'Close only the isolated browser created for this test using CLOSE APP.',
    }
    return TaskSpec(case, instructions[case], tuple(steps), goal)


def reply_for(spec, timeout=5):
    steps = deepcopy(list(spec.steps))
    for step in steps:
        step['timeout'] = timeout
    return {'status': 'plan', 'reason': 'Fixed test replay; not a real model response.', 'subtasks': [
        {'id': 'basic_task', 'goal': spec.instruction, 'depends_on': [], 'steps': steps}]}


class LocalTaskState:
    """State from application events/files/inbox, independent of OCR results.

    This cooperative fixture is not an accessibility adapter for arbitrary apps.
    Only a pre-created sample path is readable; the model cannot choose a path.
    """
    def __init__(self, case, directory, fault='none', lifecycle=None):
        if case not in CASES or fault not in FAULTS:
            raise ValueError('Unknown case or fault.')
        if fault == 'permission_denied' and case != 'open_file':
            raise ValueError('permission_denied is an open_file fault.')
        if fault in ('no_change', 'false_ocr') and case != 'open_file':
            raise ValueError('no_change/false_ocr use the one-action open_file fixture.')
        if fault in ('missing', 'duplicate') and case not in ('search', 'send_message'):
            raise ValueError('missing/duplicate require an input task.')
        self.case, self.fault = case, fault
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.sample = self.directory / 'sample.txt'
        self.sample.write_text(SAMPLE, encoding='utf-8', newline='')
        self.lifecycle = lifecycle
        self.events, self.inbox, self.sent_ids = [], [], {}
        self.values = dict(status='READY', focused=False, draft='', result='', file_open=False,
                           file_sha256='', message_count=0, last_message='', browser_open=case == 'close_app',
                           app_closed=False, app_error='')

    def snapshot(self):
        return deepcopy(self.values)

    def event(self, action, value='', operation_id='local-message-1'):
        if action not in ('focus', 'input', 'finish') or not isinstance(value, str) or len(value) > 200:
            raise ValueError('Unsupported local event.')
        self.events.append({'action': action, 'length': len(value)})
        if action == 'focus':
            self.values.update(focused=True, status='INPUT READY')
        elif action == 'input':
            if not self.values['focused']:
                raise ValueError('Input is not focused.')
            self.values['draft'] = value
        elif self.fault not in ('no_change', 'false_ocr'):
            self.finish(operation_id)
        return self.snapshot()

    def finish(self, operation_id):
        v = self.values
        if self.case in ('open_browser', 'close_app'):
            if self.lifecycle:
                self.lifecycle(self.case)
            v.update(browser_open=self.case == 'open_browser', app_closed=self.case == 'close_app')
        elif self.case == 'search':
            # Local catalog lookup, not a public search engine or live web result.
            catalog = ['GUI Agent', 'OCR cold start', 'Window state verification']
            match = next((item for item in catalog if item.casefold() == v['draft'].casefold()), None)
            v['result'] = 'FOUND ' + match if match else 'NOT FOUND'
            v['status'] = v['result']
            return
        elif self.case == 'open_file':
            try:
                if self.fault == 'permission_denied':
                    raise PermissionError('Injected denial of the test sample file.')
                content = self.sample.read_bytes()
                # The actual bytes, rather than the expected hash, establish observed state.
                v.update(file_open=True, file_sha256=hashlib.sha256(content).hexdigest(), result=content.decode('utf-8').strip())
            except PermissionError:
                v.update(status='ACCESS DENIED', app_error='permission_denied')
                return
        elif self.case == 'send_message':
            if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 100:
                raise ValueError('A bounded operation ID is required.')
            if operation_id in self.sent_ids:
                if self.sent_ids[operation_id] != v['draft']:
                    raise ValueError('Operation ID reused with a different message.')
            else:
                if not v['draft'].strip():
                    raise ValueError('Empty local message.')
                self.sent_ids[operation_id] = v['draft']
                self.inbox.append({'id': operation_id, 'text': v['draft'], 'recipient': 'local-test-inbox'})
                (self.directory / 'inbox.json').write_text(json.dumps(self.inbox, indent=2), encoding='utf-8')
            v.update(message_count=len(self.inbox), last_message=v['draft'])
        v['status'] = task_spec(self.case).goal['text']


def display_rows(app):
    spec = task_spec(app.case)
    target = spec.steps[-1]['text']
    rows = [(app.values['status'], [30, 75, 900, 110]), (target, [520, 160, 900, 210])]
    if app.case in ('search', 'send_message'):
        if app.fault != 'missing':
            rows.append(('FOCUS INPUT', [30, 160, 300, 210]))
        if app.fault == 'duplicate':
            rows.append(('FOCUS INPUT', [30, 240, 300, 290]))
    if app.values['draft']:
        rows.append((app.values['draft'], [30, 330, 950, 380]))
    if app.values['result']:
        rows.append((app.values['result'], [30, 430, 950, 480]))
    if app.values['last_message']:
        rows.append((app.values['last_message'], [30, 510, 950, 550]))
    return rows


class SyntheticControl:
    def __init__(self, app, title):
        self.app, self.calls, self.time, self.stopped = app, [], 0, False
        self.guard = SimpleNamespace(title=title, active=lambda: 201)
        self.backend = SimpleNamespace(KEYBOARD_KEYS=PLANNER_KEYS)

    def check(self):
        if self.stopped:
            raise StopRequested('Synthetic stop.')

    def click(self, x, y):
        self.check()
        self.calls.append({'action': 'click'})
        self.app.event('focus' if x < 400 else 'finish')

    def type_text(self, text):
        self.check()
        self.calls.append({'action': 'type_text', 'length': len(text)})
        self.app.event('input', self.app.values['draft'] + text)

    def sleep(self, seconds):
        self.time += seconds


class SyntheticObserver:
    def __init__(self, control, output):
        self.control, self.output, self.count = control, Path(output), 0

    def observe(self, label):
        self.control.check()
        self.count += 1
        directory = self.output / f'{self.count:04d}_{label}'
        directory.mkdir(parents=True, exist_ok=False)
        rows = display_rows(self.control.app)
        frame = Image.new('RGB', (1000, 650), 'white')
        draw = ImageDraw.Draw(frame)
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 24)
        for text, box in rows:
            draw.text((box[0], box[1]), text, fill='black', font=font)
        results = [{'id': i, 'text': text, 'confidence': .99, 'bbox': box} for i, (text, box) in enumerate(rows, 1)]
        if self.control.app.fault == 'false_ocr' and self.control.calls:
            results.append({'id': 999, 'text': task_spec(self.control.app.case).goal['text'], 'confidence': .99})
        report = {'source': {'kind': 'screen', 'simulation': True, 'coordinate_space': 'physical-desktop-pixels',
                             'region': {'left': 0, 'top': 0, 'width': 1000, 'height': 650}},
                  'results': results, 'synthetic_ocr': True}
        state = {'source': 'synthetic-local-app-state', 'values': self.control.app.snapshot()}
        frame.save(directory / 'screenshot.png')
        for name, value in [('results.json', report), ('state.json', state)]:
            (directory / name).write_text(json.dumps(value, indent=2), encoding='utf-8')
        return SimpleNamespace(image=frame, report=report, state=state, directory=directory)

    def ensure_fresh(self, *args):
        self.control.check()
        return {'simulation': True}


def run_synthetic(case, directory, fault='none'):
    directory = Path(directory)
    spec = task_spec(case)
    app = LocalTaskState(case, directory / 'fixture', fault)
    title = 'GUI Basic Task Simulation'
    control = SyntheticControl(app, title)
    observer = SyntheticObserver(control, directory / 'observations')
    planner = GuiPlanner(ReplayClient(json.dumps(reply_for(spec, .2))))
    factory = lambda c, o, p: WorkflowRunner(c, o, p, poll_interval=.1, clock=lambda: control.time, sleep=control.sleep)
    summary = AgentSession(control, observer, planner, directory / 'session', 'simulation', factory).run(
        spec.instruction, title, FIELDS, spec.goal, execute=True)
    return summary, app


def audit_result(summary, app_values, case, fault):
    """Keep task completion, expected fault rejection, and cause distinct."""
    code = summary.get('error', {}).get('code')
    expected = {'none': None, 'missing': 'target_unavailable', 'duplicate': 'AmbiguousTarget',
                'no_change': 'verification_timeout', 'false_ocr': 'verification_timeout',
                'permission_denied': 'verification_timeout'}[fault]
    workflow_path = summary.get('workflow_summary')
    workflow = json.loads(Path(workflow_path).read_text(encoding='utf-8')) if workflow_path else {}
    steps = workflow.get('steps', [])
    assertions = {}
    last = steps[-1] if steps else {}
    evidence_directory = last.get('verification', {}).get('directory') or (last.get('observations') or summary.get('observations') or [None])[-1]
    if fault == 'none':
        evidence_directory = (summary.get('observations') or [None])[-1]
    evidence_path = Path(evidence_directory) if evidence_directory else None
    assertions['last_observation_assets_exist'] = bool(evidence_path) and all(
        (evidence_path / name).is_file() for name in ('screenshot.png', 'results.json', 'state.json'))
    if fault == 'none':
        assertions['step_count'] = len(steps) == len(task_spec(case).steps)
        assertions['steps_double_checked'] = bool(steps) and all(
            s.get('verified') and s.get('verification', {}).get('ocr', {}).get('met') and
            s.get('verification', {}).get('state', {}).get('met') for s in steps)
        goal = summary.get('goal_verification', {})
        assertions['goal_double_checked'] = bool(goal.get('met') and goal.get('ocr', {}).get('met') and goal.get('state', {}).get('met'))
        spec = task_spec(case)
        checks = [(s.get('verification', {}).get('directory'), expected['expect']) for s, expected in zip(steps, spec.steps)]
        checks.append(((summary.get('observations') or [None])[-1], spec.goal))
        observed_checks = []
        for location, condition in checks:
            path = Path(location) if location else None
            if not path or not all((path / name).is_file() for name in ('screenshot.png', 'results.json', 'state.json')):
                observed_checks.append(False)
                continue
            actual = SimpleNamespace(report=json.loads((path / 'results.json').read_text(encoding='utf-8')),
                                     state=json.loads((path / 'state.json').read_text(encoding='utf-8')))
            observed_checks.append(evaluate_observation(actual, {'contains': False, 'present': True,
                'min_confidence': .5, **condition})['met'])
        assertions['saved_evidence_rechecked'] = bool(observed_checks) and all(observed_checks)
    elif fault in ('missing', 'duplicate'):
        assertions['no_dispatch'] = summary['input_dispatches_started'] == 0
    else:
        assertions['one_dispatch'] = summary['input_dispatches_started'] == 1
        if fault == 'false_ocr':
            evidence = steps[-1].get('verification', {}) if steps else {}
            assertions['false_positive_rejected'] = bool(evidence.get('ocr', {}).get('met') and
                evidence.get('state', {}).get('met') is False and evidence.get('met') is False)
            if assertions['last_observation_assets_exist']:
                saved = SimpleNamespace(report=json.loads((evidence_path / 'results.json').read_text(encoding='utf-8')),
                                        state=json.loads((evidence_path / 'state.json').read_text(encoding='utf-8')))
                checked = evaluate_observation(saved, {'contains': False, 'present': True, **task_spec(case).goal})
                assertions['saved_false_positive_rechecked'] = checked['ocr']['met'] and not checked['state']['met'] and not checked['met']
        if fault == 'permission_denied':
            assertions['explicit_denial_observed'] = app_values['app_error'] == 'permission_denied'
    if summary['environment'] == 'desktop' and workflow_path:
        actions = Path(workflow_path).parents[2] / 'actions.jsonl'
        lines = [json.loads(line) for line in actions.read_text(encoding='utf-8').splitlines()] if actions.exists() else []
        assertions['input_log_matches_dispatches'] = len(lines) == summary['input_dispatches_started']
    status_ok = summary['status'] == ('completed' if fault == 'none' else 'failed')
    passed = status_ok and code == expected and all(assertions.values())
    return {'case': case, 'fault': fault, 'environment': summary['environment'], 'client_kind': summary['client_kind'],
            'status': summary['status'], 'task_verified': summary['task_verified'], 'passed': passed,
            'assertions': assertions, 'dispatches': summary['input_dispatches_started'],
            'error_code': code, 'failure_category': summary.get('error', {}).get('category'),
            'application_cause': app_values.get('app_error') or None,
            'evidence_directory': str(evidence_directory) if evidence_directory else None,
            'elapsed_seconds': summary['elapsed_seconds']}
