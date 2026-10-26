"""Offline workflow tests using synthetic observations and a mock input backend."""

from collections import deque
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock

from gui_control.controller import ControlError, StopRequested
from gui_workflow.observer import StaleTarget
from gui_workflow.plan import validate_plan
from gui_workflow.runner import WorkflowRunner


def observation(*texts):
    report = {'source': {'kind': 'screen', 'coordinate_space': 'physical-desktop-pixels',
                         'region': {'left': 100, 'top': 100, 'width': 800, 'height': 600}},
              'results': [{'id': i + 1, 'text': text, 'confidence': 0.95,
                           'bbox': [20, 30 + i * 40, 220, 60 + i * 40]}
                          for i, text in enumerate(texts)]}
    return SimpleNamespace(report=report, directory=Path('fake-observation'),
                           state={'source': 'mock-widget-adapter', 'values': {'status': 'DONE' if 'DONE' in texts else 'WAITING'}})


def click_plan():
    return {'window_title': 'Test window', 'steps': [{
        'action': 'click_text', 'text': 'START', 'timeout': 0.2,
        'expect': {'text': 'DONE', 'state': {'status': 'DONE'}},
    }]}


class FakeObserver:
    def __init__(self, snapshots):
        self.snapshots = deque(snapshots)
        self.last = None
        self.ensure_fresh = Mock(return_value={'patch_mean_difference': 0})
        self.count = 0

    def observe(self, label):
        self.count += 1
        if self.snapshots:
            self.last = self.snapshots.popleft()
        if self.last is None:
            raise AssertionError('Unexpected observation')
        return self.last


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.control = Mock()
        self.control.backend = SimpleNamespace(KEYBOARD_KEYS=['enter', 'ctrl', 'a'])
        self.time = 0

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, snapshots):
        self.observer = FakeObserver(snapshots)
        def advance(seconds):
            self.time += seconds
        return WorkflowRunner(self.control, self.observer, self.temp.name, poll_interval=0.1,
                              clock=lambda: self.time, sleep=advance)

    def test_click_is_sent_once_then_delayed_result_is_verified(self):
        result = self.runner([observation('START'), observation('START'), observation('DONE')]).run(click_plan())
        self.assertTrue(result['success'])
        self.control.click.assert_called_once_with(220, 145)
        self.assertEqual(self.observer.count, 3)

    def test_missing_target_reobserves_without_clicking(self):
        result = self.runner([observation()]).run(click_plan())
        self.assertEqual(result['error']['code'], 'target_unavailable')
        self.assertEqual(self.observer.count, 3)
        self.control.click.assert_not_called()

    def test_duplicate_targets_stop_without_choosing_arbitrarily(self):
        result = self.runner([observation('START', 'START')]).run(click_plan())
        self.assertEqual(result['error']['code'], 'AmbiguousTarget')
        self.control.click.assert_not_called()
        self.assertEqual(self.observer.count, 1)

    def test_explicit_occurrence_selects_second_match(self):
        plan = click_plan()
        plan['steps'][0]['occurrence'] = 2
        result = self.runner([observation('START', 'START'), observation('DONE')]).run(plan)
        self.assertTrue(result['success'])
        self.control.click.assert_called_once_with(220, 185)

    def test_verification_timeout_does_not_repeat_input(self):
        result = self.runner([observation('START')]).run(click_plan())
        self.assertEqual(result['error']['code'], 'verification_timeout')
        self.control.click.assert_called_once()
        self.assertTrue(result['steps'][0]['action_sent'])
        self.assertFalse(result['steps'][0]['verified'])

    def test_preexisting_condition_cannot_claim_action_success(self):
        result = self.runner([observation('START', 'DONE')]).run(click_plan())
        self.assertEqual(result['error']['code'], 'condition_already_satisfied')
        self.control.click.assert_not_called()

    def test_changed_target_reobserves_before_dispatch(self):
        runner = self.runner([observation('START'), observation('START'), observation('DONE')])
        self.observer.ensure_fresh.side_effect = [StaleTarget('moved'), {}]
        result = runner.run(click_plan())
        self.assertTrue(result['success'])
        self.control.click.assert_called_once()
        self.assertEqual(self.observer.ensure_fresh.call_count, 2)

    def test_partial_dispatch_failure_is_not_retried(self):
        self.control.click.side_effect = RuntimeError('partial input')
        result = self.runner([observation('START')]).run(click_plan())
        self.assertFalse(result['success'])
        self.assertTrue(result['steps'][0]['dispatch_started'])
        self.control.click.assert_called_once()

    def test_focus_loss_stops_before_action(self):
        self.control.check.side_effect = ControlError('focus lost')
        result = self.runner([]).run(click_plan())
        self.assertEqual(result['status'], 'failed')
        self.control.click.assert_not_called()

    def test_escape_is_recorded_as_aborted(self):
        self.control.check.side_effect = StopRequested('Escape')
        result = self.runner([]).run(click_plan())
        self.assertEqual(result['status'], 'aborted')
        self.control.click.assert_not_called()

    def test_invalid_later_step_prevents_entire_plan(self):
        plan = click_plan()
        plan['steps'].append({'action': 'unsupported'})
        with self.assertRaises(ValueError):
            self.runner([]).run(plan)
        self.control.click.assert_not_called()

    def test_typing_is_verified_from_a_new_screenshot(self):
        plan = {'window_title': 'Test window', 'steps': [{'action': 'type_text', 'text': '中文123',
                                                        'expect': {'text': '中文123', 'state': {'status': 'WAITING'}}}]}
        result = self.runner([observation('INPUT READY'), observation('中文123')]).run(plan)
        self.assertTrue(result['success'])
        self.control.type_text.assert_called_once_with('中文123')

    def test_text_disappearance_can_be_a_postcondition(self):
        plan = click_plan()
        plan['steps'][0]['expect'] = {'text': 'START', 'present': False, 'state': {'status': 'DONE'}}
        result = self.runner([observation('START'), observation('DONE')]).run(plan)
        self.assertTrue(result['success'])

    def test_wait_step_never_sends_input(self):
        plan = {'window_title': 'Test window', 'steps': [{'action': 'wait_text', 'expect': {'text': 'READY', 'state': {'status': 'WAITING'}}}]}
        result = self.runner([observation('READY')]).run(plan)
        self.assertTrue(result['success'])
        self.assertFalse(result['steps'][0]['action_sent'])
        self.control.click.assert_not_called()

    def test_final_summary_is_persisted_on_failure(self):
        self.runner([observation()]).run(click_plan())
        summary = json.loads((Path(self.temp.name) / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['status'], 'failed')
        self.assertTrue((Path(self.temp.name) / 'events.jsonl').is_file())

    def test_validation_does_not_mutate_the_callers_plan(self):
        plan = click_plan()
        original = deepcopy(plan)
        validate_plan(plan)
        self.assertEqual(plan, original)


if __name__ == '__main__':
    unittest.main()
