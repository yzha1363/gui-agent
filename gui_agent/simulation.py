"""Explicit synthetic application and OCR for offline closed-loop demonstrations."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont

from gui_agent.client import ReplayClient
from gui_agent.planner import GuiPlanner, PLANNER_KEYS
from gui_agent.session import AgentSession
from gui_control.controller import FocusLost, StopRequested
from gui_workflow.runner import WorkflowRunner

FIELDS = ('input_focused', 'status', 'entry_text', 'submitted', 'submit_enabled')
INSTRUCTION = 'Focus INPUT FIELD, type exactly Hello GUI 123 中文测试, then click SUBMIT.'


class SimulatedApp:
    def __init__(self, scenario='normal'):
        self.scenario = scenario
        self.values = {'input_focused': False, 'status': 'WAITING', 'entry_text': '', 'submitted': '', 'submit_enabled': True}
        self.calls = []
        self.stopped = False
        self.identity = 101
        self.guard = SimpleNamespace(active=lambda: self.identity)
        self.backend = SimpleNamespace(KEYBOARD_KEYS=PLANNER_KEYS)
        self.time = 0

    def check(self):
        if self.stopped:
            raise StopRequested('Synthetic stop requested.')
        if self.identity != 101:
            raise FocusLost('Synthetic foreground window identity changed.')

    def click(self, x, y):
        self.check()
        self.calls.append({'action': 'click', 'point': [x, y]})
        if x < 350:
            if self.scenario not in ('no_change', 'false_ocr'):
                self.values.update(input_focused=True, status='INPUT READY')
        else:
            self.values['submitted'] = 'SUBMITTED ' + self.values['entry_text']

    def type_text(self, text):
        self.check()
        self.calls.append({'action': 'type_text', 'length': len(text)})
        if not self.values['input_focused']:
            raise RuntimeError('Synthetic entry is not focused.')
        if self.scenario == 'partial_input':
            self.values['entry_text'] += text[:3]
            raise RuntimeError('Synthetic partial input failure.')
        self.values['entry_text'] += text

    def press(self, key):
        self.check(); self.calls.append({'action': 'press', 'key': key})

    def hotkey(self, *keys):
        self.check(); self.calls.append({'action': 'hotkey', 'keys': list(keys)})

    def sleep(self, seconds):
        self.time += seconds


class SimulatedObserver:
    def __init__(self, app, output):
        self.app, self.output = app, Path(output)
        self.counter = 0

    def observe(self, label):
        self.app.check()
        self.counter += 1
        directory = self.output / f'{self.counter:04d}_{label}'
        directory.mkdir(parents=True, exist_ok=False)
        v = self.app.values
        rows = [('SUBMIT', [420, 90, 590, 140]), (v['status'], [30, 25, 300, 60])]
        if self.app.scenario != 'missing':
            rows.append(('INPUT FIELD', [30, 90, 270, 140]))
        if self.app.scenario == 'duplicate':
            rows.append(('INPUT FIELD', [30, 150, 270, 190]))
        if v['entry_text']:
            rows.append((v['entry_text'], [30, 210, 600, 245]))
        if v['submitted']:
            rows.append((v['submitted'], [30, 280, 620, 320]))
        if self.app.scenario == 'false_ocr' and self.app.calls:
            rows.append(('INPUT READY', [30, 25, 300, 60]))
        results = [{'id': i, 'text': text, 'confidence': .99, 'bbox': box} for i, (text, box) in enumerate(rows, 1)]
        report = {'source': {'kind': 'screen', 'coordinate_space': 'physical-desktop-pixels', 'simulation': True,
                             'region': {'left': 0, 'top': 0, 'width': 640, 'height': 360}},
                  'results': results, 'synthetic_ocr': True}
        image = Image.new('RGB', (640, 360), 'white')
        draw = ImageDraw.Draw(image)
        try:
            font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)
        except OSError:
            font = ImageFont.load_default()
        for text, box in rows:
            draw.text((box[0], box[1]), text, fill='black', font=font)
        image.save(directory / 'screenshot.png')
        state = {'source': 'simulated-widget-state', 'values': deepcopy(v)}
        for name, value in [('results.json', report), ('state.json', state)]:
            (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        return SimpleNamespace(report=report, image=image, state=state, directory=directory, captured_at=self.app.time)

    def ensure_fresh(self, observation, target):
        self.app.check()
        return {'simulation': True, 'patch_mean_difference': 0}


def run_simulation(output, scenario='normal', execute=True, reply=None, goal=None, after_planning=None):
    root = Path(__file__).resolve().parents[1]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    app = SimulatedApp(scenario)
    observer = SimulatedObserver(app, output / 'observations')
    value = json.loads((root / 'docs/agent-plan.example.json').read_text(encoding='utf-8')) if reply is None else deepcopy(reply)
    # Short synthetic timeouts use a fake clock, never wall-clock sleeps.
    for item in value.get('subtasks', []):
        for step in item['steps']:
            step['timeout'] = .2
    client = ReplayClient(json.dumps(value, ensure_ascii=False))
    if after_planning:
        original = client.invoke
        def invoke(messages):
            result = original(messages)
            after_planning(app)
            return result
        client.invoke = invoke
    target = json.loads((root / 'docs/agent-goal.example.json').read_text(encoding='utf-8')) if goal is None else goal
    runner = lambda control, observe, out: WorkflowRunner(control, observe, out, poll_interval=.1,
                                                        clock=lambda: app.time, sleep=app.sleep)
    summary = AgentSession(app, observer, GuiPlanner(client), output / 'session', environment='simulation',
                           runner_factory=runner).run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, target, execute)
    (output / 'simulated_actions.json').write_text(json.dumps(app.calls, indent=2), encoding='utf-8')
    return summary, app
