"""Fault injection and independent state regression cases; no desktop input."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from gui_control.controller import FocusLost, StopRequested
from gui_control.targets import AmbiguousTarget, TargetError, select_text
from gui_workflow.plan import validate_plan
from gui_workflow.runner import WorkflowError, evaluate_observation
from gui_workflow.verification import StateFile, StateUnavailable, evaluate_state, failure_category, publish_state
from tests import test_workflow as fixtures
from tests.test_workflow import observation, click_plan


class DoubleCheckTests(unittest.TestCase):
    def test_ocr_false_positive_is_not_success(self):
        snapshot = observation('DONE')
        snapshot.state['values']['status'] = 'WRONG'
        evidence = evaluate_observation(snapshot, validate_plan(click_plan())['steps'][0]['expect'])
        self.assertTrue(evidence['ocr']['met'])
        self.assertFalse(evidence['state']['met'])
        self.assertFalse(evidence['met'])

    def test_correct_state_with_wrong_ocr_is_not_success(self):
        snapshot = observation('WRONG')
        snapshot.state['values']['status'] = 'DONE'
        self.assertFalse(evaluate_observation(snapshot, validate_plan(click_plan())['steps'][0]['expect'])['met'])

    def test_unavailable_state_is_not_success(self):
        snapshot = observation('DONE')
        snapshot.state = None
        self.assertFalse(evaluate_observation(snapshot, validate_plan(click_plan())['steps'][0]['expect'])['met'])

    def test_exact_control_content_not_ocr_normalization(self):
        self.assertFalse(evaluate_state({'values': {'text': 'hello'}}, {'text': 'Hello'})['met'])

    def test_boolean_is_not_integer(self):
        self.assertFalse(evaluate_state({'values': {'enabled': 1}}, {'enabled': True})['met'])

    def test_all_independent_fields_are_required(self):
        self.assertFalse(evaluate_state({'values': {'focus': True}}, {'focus': True, 'text': '123'})['met'])

    def test_plan_without_independent_check_rejected(self):
        plan = click_plan()
        del plan['steps'][0]['expect']['state']
        with self.assertRaises(ValueError):
            validate_plan(plan)


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.json'
        self.control = Mock()
        self.control.guard.active.return_value = 123

    def tearDown(self):
        self.temp.cleanup()

    def write(self, hwnd=123, age=0):
        self.path.write_text(json.dumps({'hwnd': hwnd, 'timestamp': time.time() - age,
                                         'values': {'text': 'Hello'}}), encoding='utf-8')

    def test_fresh_state_from_same_window(self):
        self.write()
        self.assertEqual(StateFile(self.path, self.control)()['values']['text'], 'Hello')

    def test_stale_state_rejected(self):
        self.write(age=10)
        with self.assertRaises(StateUnavailable):
            StateFile(self.path, self.control)()

    def test_wrong_window_rejected(self):
        self.write(hwnd=456)
        with self.assertRaises(StateUnavailable):
            StateFile(self.path, self.control)()

    def test_missing_file_rejected(self):
        with self.assertRaises(StateUnavailable):
            StateFile(self.path, self.control)()


class StatePublisherTests(unittest.TestCase):
    def test_transient_denial_recovers_on_next_tick(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.object(Path, 'replace', side_effect=PermissionError('simulated sharing conflict')):
                self.assertFalse(publish_state(path, {'timestamp': 1}))
            self.assertFalse(path.exists())
            self.assertTrue(publish_state(path, {'timestamp': 2}))
            self.assertEqual(json.loads(path.read_text())['timestamp'], 2)

    def test_denial_does_not_freshen_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            publish_state(path, {'timestamp': 1, 'value': 'old'})
            with patch.object(Path, 'replace', side_effect=PermissionError('simulated access denied')):
                self.assertFalse(publish_state(path, {'timestamp': 2, 'value': 'new'}))
            self.assertEqual(json.loads(path.read_text()), {'timestamp': 1, 'value': 'old'})

    def test_unexpected_disk_error_is_not_silenced(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(Path, 'write_text', side_effect=OSError('simulated disk full')):
                with self.assertRaises(OSError):
                    publish_state(Path(directory) / 'state.json', {})


class FailureCategoryTests(unittest.TestCase):
    def test_permission_denied(self):
        self.assertEqual(failure_category(PermissionError('injected denial')), 'permission_denied')

    def test_localization_failure(self):
        self.assertEqual(failure_category(AmbiguousTarget('two targets')), 'localization_failure')

    def test_execution_failure(self):
        self.assertEqual(failure_category(RuntimeError('partial input')), 'execution_failure')

    def test_verification_failure(self):
        self.assertEqual(failure_category(WorkflowError('verification_timeout', 'mismatch')), 'verification_failure')

    def test_user_abort(self):
        self.assertEqual(failure_category(StopRequested('Esc')), 'user_aborted')

    def test_window_guard(self):
        self.assertEqual(failure_category(FocusLost('lost')), 'window_guard_failure')


class DuplicateEdgeTests(unittest.TestCase):
    def test_boolean_occurrence_rejected(self):
        with self.assertRaises(TargetError):
            select_text(observation('START').report, 'START', occurrence=True)

    def test_zero_occurrence_rejected(self):
        with self.assertRaises(TargetError):
            select_text(observation('START').report, 'START', occurrence=0)

    def test_out_of_range_occurrence_rejected(self):
        with self.assertRaises(TargetError):
            select_text(observation('START').report, 'START', occurrence=2)

    def test_overlap_does_not_become_two_clickable_targets(self):
        report = observation('START', 'START').report
        report['results'][1]['bbox'] = report['results'][0]['bbox']
        with self.assertRaises(AmbiguousTarget):
            select_text(report, 'START', occurrence=1)

    def test_confidence_does_not_resolve_ambiguity(self):
        report = observation('START', 'START').report
        report['results'][1]['confidence'] = 0.51
        with self.assertRaises(AmbiguousTarget):
            select_text(report, 'START')


class DoubleCheckRunnerTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    tearDown = fixtures.WorkflowTests.tearDown
    runner = fixtures.WorkflowTests.runner

    def test_forged_ocr_times_out_without_repeating_input(self):
        wrong = observation('DONE')
        wrong.state['values']['status'] = 'WRONG'
        result = self.runner([observation('START'), wrong]).run(click_plan())
        self.assertFalse(result['success'])
        self.assertEqual(result['error']['category'], 'verification_failure')
        self.control.click.assert_called_once()
        self.assertTrue(result['steps'][0]['verification']['ocr']['met'])
        self.assertFalse(result['steps'][0]['verification']['state']['met'])

    def test_permission_failure_records_category_and_does_not_retry(self):
        self.control.click.side_effect = PermissionError('simulated access denied')
        result = self.runner([observation('START')]).run(click_plan())
        self.assertEqual(result['error']['category'], 'permission_denied')
        self.assertTrue(result['steps'][0]['dispatch_started'])
        self.assertFalse(result['steps'][0]['action_sent'])
        self.control.click.assert_called_once()

    def test_execution_failure_records_category_and_does_not_retry(self):
        self.control.click.side_effect = RuntimeError('simulated partial input')
        result = self.runner([observation('START')]).run(click_plan())
        self.assertEqual(result['error']['category'], 'execution_failure')
        self.control.click.assert_called_once()

    def test_focus_guard_failure_has_own_category(self):
        self.control.check.side_effect = FocusLost('simulated focus loss')
        result = self.runner([]).run(click_plan())
        self.assertEqual(result['error']['category'], 'window_guard_failure')
        self.control.click.assert_not_called()

    def test_missing_adapter_never_claims_success(self):
        ready = observation('DONE')
        ready.state = None
        result = self.runner([observation('START'), ready]).run(click_plan())
        self.assertEqual(result['error']['category'], 'verification_failure')
        self.assertFalse(result['success'])
