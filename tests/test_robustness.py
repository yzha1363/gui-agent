"""Geometry/label regressions and independent-state checks, with synthetic inputs."""

import json
from pathlib import Path
import tempfile
import unittest

from gui_tasks.robustness import EXPECTED, SCENARIOS, TEXT, contracts, run_robustness
from gui_workflow.plan import validate_plan
from gui_workflow.runner import evaluate_observation


class RobustnessTests(unittest.TestCase):
    def check_case(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'run'
            result, app = run_robustness(root, scenario)
            status, code, dispatches = EXPECTED[scenario]
            self.assertEqual(result['status'], status)
            self.assertEqual(result.get('error', {}).get('code'), code)
            self.assertEqual(result['input_dispatches_started'], dispatches)
            self.assertEqual(result['task_verified'], status == 'completed')
            self.assertFalse(result['desktop_input_sent'])
            self.assertLessEqual(app.values['submit_count'], 1)
            self.assertLessEqual(sum(c['action'] == 'type_text' for c in app.calls), 1)
            self.assertFalse(any(c['action'] == 'click' and c['hit'] is None for c in app.calls))
            if status == 'completed':
                self.assertTrue(result['goal_verification']['ocr']['met']); self.assertTrue(result['goal_verification']['state']['met'])
                self.assertEqual(app.values['entry_text'], TEXT); self.assertEqual(app.values['submit_count'], 1)
                workflow = json.loads((root / 'session/cycle-01/workflow/summary.json').read_text(encoding='utf-8'))
                self.assertTrue(all(r.get('target_reconfirmed') for r in workflow['steps'] if r['action'] == 'click_text'))
            if scenario == 'layout_moves':
                self.assertEqual(app.calls[0]['point'], [150, 190])
            if scenario == 'duplicate_second': self.assertEqual(app.values['focus_target'], 'secondary')
            if scenario in ('disappears_before_click', 'duplicate_appears'): self.assertEqual(app.calls, [])
            if scenario == 'partial_input': self.assertEqual(app.values['entry_text'], 'Hel')
            if scenario == 'submit_uncertain':
                self.assertTrue(result['diagnostic']['goal']['met']); self.assertTrue(result['input_delivery_uncertain'])
                self.assertFalse(result['task_verified'])


def case_test(name):
    def test(self): self.check_case(name)
    return test
for _name in SCENARIOS: setattr(RobustnessTests, 'test_' + _name, case_test(_name))


class RobustnessContractTests(unittest.TestCase):
    def test_second_occurrence_is_explicit_in_the_callers_instruction_and_expectations(self):
        reply, goal, instruction = contracts('duplicate_second')
        self.assertIn('second INPUT FIELD', instruction)
        self.assertEqual(reply['subtasks'][0]['steps'][0]['occurrence'], 2)
        self.assertEqual(goal['state']['focus_target'], 'secondary')

    def test_submission_requires_a_single_independent_commit(self):
        _, goal, _ = contracts()
        condition = validate_plan({'window_title': 'test', 'steps': [{'action': 'wait_text', 'expect': goal}]})['steps'][0]['expect']
        from types import SimpleNamespace
        report = {'results': [{'id': 1, 'text': goal['text'], 'confidence': .99}]}
        snapshot = SimpleNamespace(report=report, state={'values': dict(goal['state'], submit_count=2)})
        self.assertFalse(evaluate_observation(snapshot, condition)['met'])


if __name__ == '__main__': unittest.main()
