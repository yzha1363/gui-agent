"""System v2 robustness fixtures with real geometry hit testing and labelled synthetic OCR."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from gui_agent import GuiPlanner, ReplayClient
from gui_agent.recovery import RecoverySession
from gui_agent.planner import PLANNER_KEYS
from gui_control.controller import StopRequested
from gui_perception.pipeline import annotation_font
from gui_workflow.recovery import RecoveryPolicy
from gui_workflow.verification import StateUnavailable

TEXT = 'Hello GUI 123 中文测试'
FIELDS = ('input_focused', 'status', 'entry_text', 'submitted', 'submit_enabled', 'focus_target', 'submit_count')
SCENARIOS = ('normal', 'layout_shifted', 'layout_moves', 'disappears_before_click', 'duplicate_appears',
             'duplicate', 'duplicate_second', 'overlap_second', 'missing', 'reappears', 'wrong_text',
             'ocr_false_positive', 'state_false_positive', 'state_type_changed', 'stale_state', 'wrong_hwnd',
             'partial_input', 'submit_uncertain', 'stop_after_focus', 'permission_denied')
EXPECTED = {name: ('completed', None, 3) for name in ('normal', 'layout_shifted', 'layout_moves', 'duplicate_second', 'reappears')}
EXPECTED.update({
    'disappears_before_click': ('failed', 'target_unavailable', 0),
    'duplicate_appears': ('failed', 'AmbiguousTarget', 0), 'duplicate': ('failed', 'AmbiguousTarget', 0),
    'overlap_second': ('failed', 'AmbiguousTarget', 0), 'missing': ('failed', 'target_unavailable', 0),
    'wrong_text': ('failed', 'verification_timeout', 2), 'ocr_false_positive': ('failed', 'verification_timeout', 1),
    'state_false_positive': ('failed', 'verification_timeout', 1), 'state_type_changed': ('failed', 'verification_timeout', 1),
    'stale_state': ('failed', 'StateUnavailable', 0), 'wrong_hwnd': ('failed', 'StateUnavailable', 0),
    'partial_input': ('failed', 'RuntimeError', 2), 'submit_uncertain': ('failed', 'RuntimeError', 3),
    'stop_after_focus': ('aborted', 'StopRequested', 1), 'permission_denied': ('failed', 'PermissionError', 0),
})


def contains(box, x, y): return box[0] <= x < box[2] and box[1] <= y < box[3]


class RobustnessApp:
    def __init__(self, scenario='normal'):
        if scenario not in SCENARIOS: raise ValueError('Unknown robustness scenario.')
        self.scenario = scenario; self.time = 0.; self.stopped = False; self.identity = 701
        self.guard = SimpleNamespace(active=lambda: self.identity)
        self.backend = SimpleNamespace(KEYBOARD_KEYS=PLANNER_KEYS)
        self.calls = []; self.controls = {'primary': [30, 100, 270, 150], 'submit': [430, 100, 620, 150]}
        self.values = dict(input_focused=False, status='WAITING', entry_text='', submitted='', submit_enabled=True,
                           focus_target='', submit_count=0)
        if scenario == 'layout_shifted': self.controls.update(primary=[380, 150, 620, 200], submit=[30, 100, 220, 150])
        if scenario in ('duplicate', 'duplicate_second', 'overlap_second'):
            self.controls['secondary'] = [30, 170, 270, 210] if scenario != 'overlap_second' else list(self.controls['primary'])
        if scenario in ('missing', 'reappears'): self.controls.pop('primary')
        self.changed = False

    def check(self):
        if self.stopped: raise StopRequested('Robustness fixture stop requested.')
    def sleep(self, seconds): self.time += seconds

    def click(self, x, y):
        self.check()
        hit = next((name for name, box in self.controls.items() if contains(box, x, y)), None)
        self.calls.append({'action': 'click', 'point': [x, y], 'hit': hit})
        if hit in ('primary', 'secondary'):
            if self.scenario != 'ocr_false_positive':
                focused = 'true' if self.scenario == 'state_type_changed' else True
                self.values.update(input_focused=focused, focus_target=hit, status='INPUT READY')
            if self.scenario == 'stop_after_focus': self.stopped = True
        elif hit == 'submit':
            self.values['submit_count'] += 1
            self.values['submitted'] = 'SUBMITTED ' + self.values['entry_text']
            if self.scenario == 'submit_uncertain': raise RuntimeError('Injected loss of submit acknowledgement.')

    def type_text(self, text):
        self.check(); self.calls.append({'action': 'type_text', 'length': len(text)})
        if self.values['input_focused'] is not True: raise RuntimeError('Input was not focused.')
        self.values['entry_text'] += text[:3] if self.scenario == 'partial_input' else text + ('!' if self.scenario == 'wrong_text' else '')
        if self.scenario == 'partial_input': raise RuntimeError('Injected partial text delivery.')

    def press(self, key): self.check(); self.calls.append({'action': 'press', 'key': key})
    def hotkey(self, *keys): self.check(); self.calls.append({'action': 'hotkey', 'keys': list(keys)})

    def mutate_for_observation(self, label):
        if self.scenario in ('layout_moves', 'disappears_before_click', 'duplicate_appears') and 'step01_predispatch' in label and not self.changed:
            self.changed = True
            if self.scenario == 'layout_moves': self.controls['primary'] = [30, 170, 270, 210]
            elif self.scenario == 'disappears_before_click': self.controls.pop('primary', None)
            else: self.controls['secondary'] = [30, 170, 270, 210]
        if self.scenario == 'reappears' and 'step01_before2' in label: self.controls['primary'] = [30, 100, 270, 150]


class RobustnessObserver:
    def __init__(self, app, output): self.app, self.output, self.counter = app, Path(output), 0
    def observe(self, label):
        self.app.check(); self.app.mutate_for_observation(label)
        if self.app.scenario == 'stale_state': raise StateUnavailable('Injected stale independent state export.')
        if self.app.scenario == 'wrong_hwnd': raise StateUnavailable('Injected export from another HWND.', retryable=False)
        if self.app.scenario == 'permission_denied': raise PermissionError('Injected state export access denial.')
        self.counter += 1; directory = self.output / f'{self.counter:04d}_{label}'; directory.mkdir(parents=True, exist_ok=False)
        rows = [('SUBMIT' if name == 'submit' else 'INPUT FIELD', box) for name, box in self.app.controls.items()]
        values = self.app.values
        status = 'INPUT READY' if self.app.scenario == 'ocr_false_positive' and self.app.calls else values['status']
        if self.app.scenario == 'state_false_positive' and self.app.calls: status = 'WRONG STATUS'
        rows.append((status, [30, 25, 320, 60]))
        if values['entry_text']: rows.append((values['entry_text'], [30, 240, 620, 270]))
        if values['submitted']: rows.append((values['submitted'], [30, 300, 630, 335]))
        results = [{'id': i, 'text': text, 'confidence': .99, 'bbox': box} for i, (text, box) in enumerate(rows, 1)]
        report = {'source': {'kind': 'screen', 'coordinate_space': 'physical-desktop-pixels', 'simulation': True,
                             'region': {'left': 0, 'top': 0, 'width': 640, 'height': 360}},
                  'synthetic_ocr': True, 'fault_injection': self.app.scenario, 'results': results}
        image = Image.new('RGB', (640, 360), 'white'); draw = ImageDraw.Draw(image); font = annotation_font(17)
        for text, box in rows: draw.text((box[0], box[1]), text, fill='black', font=font)
        image.save(directory / 'screenshot.png')
        state = {'source': 'synthetic-widget-events-not-ocr', 'values': deepcopy(values)}
        for name, data in [('results.json', report), ('state.json', state)]:
            (directory / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return SimpleNamespace(report=report, state=state, image=image, directory=directory, captured_at=self.app.time)
    def ensure_fresh(self, snapshot, target):
        self.app.check()
        # Deliberately inject a weak image-difference result to test the independent OCR reconfirmation.
        return {'simulation': True, 'fault_injection': 'patch comparator accepts; do not rely on it alone', 'patch_mean_difference': 0}


def contracts(scenario='normal', timeout=.2):
    root = Path(__file__).resolve().parents[1]
    reply = json.loads((root / 'docs/agent-plan.example.json').read_text(encoding='utf-8'))
    desired = 'secondary' if scenario in ('duplicate_second', 'overlap_second') else 'primary'
    focus = reply['subtasks'][0]['steps'][0]; focus['expect']['state']['focus_target'] = desired
    if scenario in ('duplicate_second', 'overlap_second'): focus['occurrence'] = 2
    for subtask in reply['subtasks']:
        for step in subtask['steps']: step['timeout'] = timeout
    reply['subtasks'][-1]['steps'][0]['expect']['state']['submit_count'] = 1
    goal = {'text': 'SUBMITTED ' + TEXT, 'min_confidence': .3,
            'state': {'submitted': 'SUBMITTED ' + TEXT, 'submit_count': 1, 'focus_target': desired}}
    instruction = ('Use the second INPUT FIELD from top to bottom. ' if desired == 'secondary' else '') + \
                  'Focus INPUT FIELD, type exactly ' + TEXT + ', then click SUBMIT once.'
    return reply, goal, instruction


def run_robustness(output, scenario='normal', policy=None):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    app = RobustnessApp(scenario); observer = RobustnessObserver(app, output / 'observations')
    reply, goal, instruction = contracts(scenario)
    result = RecoverySession(app, observer, GuiPlanner(ReplayClient(json.dumps(reply, ensure_ascii=False))), output / 'session',
                             policy or RecoveryPolicy(), environment='simulation', clock=lambda: app.time, sleep=app.sleep).run(
                                 instruction, 'GUI Agent V2 Test', FIELDS, goal, execute=True)
    fixture = {'kind': 'simulated_geometry_and_injected_ocr_not_desktop', 'scenario': scenario, 'final_state': app.values,
               'controls': app.controls, 'actions': app.calls, 'goal': goal}
    (output / 'fixture.json').write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf-8')
    return result, app
