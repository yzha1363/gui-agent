"""Recovery invariants with a synthetic app and monotonic fake time."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gui_agent.client import ChatClient, ModelConfig
from gui_agent.recovery import BudgetClient
from gui_agent.recovery_simulation import EXPECTED, SCENARIOS, run_recovery_simulation
from gui_agent.simulation import SimulatedApp, SimulatedObserver
from gui_workflow.recovery import (RecoveringObserver, RecoveringWorkflowRunner, RecoveryError, RecoveryPolicy,
                                    RunBudget, retryable_observation)
from gui_workflow.verification import StateFile, StateUnavailable, failure_category


class RecoveryScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def check_scenario(self, name):
        summary, app = run_recovery_simulation(self.root / name, name)
        status, code, requests, dispatches = EXPECTED[name]
        self.assertEqual(summary['status'], status)
        self.assertEqual(summary.get('error', {}).get('code'), code)
        self.assertEqual(summary['request_attempts'], requests)
        self.assertEqual(summary['input_dispatches_started'], dispatches)
        self.assertLessEqual(summary['replans_used'], summary['policy']['max_replans'])
        self.assertLessEqual(summary['observation_attempts_used'], summary['policy']['max_observations'])
        self.assertLessEqual(summary['controller_dispatch_entries'], summary['policy']['max_dispatches'])
        self.assertFalse(summary['desktop_input_sent'])
        self.assertEqual(summary['task_verified'], status == 'completed')
        if status == 'completed':
            self.assertTrue(summary['goal_verification']['ocr']['met']); self.assertTrue(summary['goal_verification']['state']['met'])
            self.assertEqual([x['action'] for x in app.calls], ['click', 'type_text', 'click'])
        if name == 'submitted_then_raise':
            self.assertTrue(summary['diagnostic']['goal']['met'])
            self.assertTrue(summary['input_delivery_uncertain']); self.assertFalse(summary['diagnostic']['can_mark_success'])
            self.assertEqual(len(app.calls), 3)
        if name == 'partial_input':
            self.assertEqual(len(app.calls), 2); self.assertEqual(app.values['entry_text'], 'Hel')
            self.assertFalse(summary['diagnostic']['goal']['met'])
        if name == 'second_step_state_failure':
            self.assertEqual([x['status'] for x in summary['cycles'][-1]['subtasks']], ['verified', 'failed', 'blocked'])
        if name in ('stop_after_input', 'dispatch_limit'):
            self.assertTrue(summary['diagnostic']['skipped'])
        saved = json.loads((self.root / name / 'session/summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary, saved)


def _case(name):
    def test(self): self.check_scenario(name)
    test.__name__ = 'test_' + name
    return test
for _name in SCENARIOS: setattr(RecoveryScenarioTests, 'test_' + _name, _case(_name))


class RecoveryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.app = SimulatedApp(); self.events = []
        self.budget = RunBudget(self.app, RecoveryPolicy(), lambda name, **fields: self.events.append((name, fields)),
                                clock=lambda: self.app.time, sleep=self.app.sleep)

    def test_policy_rejects_booleans_nonfinite_unknown_and_unbounded_values(self):
        for fields in [{'max_replans': True}, {'session_timeout': float('inf')}, {'max_observations': 201},
                       {'retry_delay': 0}, {'max_dispatches': 0}]:
            with self.subTest(fields=fields), self.assertRaises(ValueError): replace(RecoveryPolicy(), **fields)
        with self.assertRaises(ValueError): RecoveryPolicy.from_dict({'max_replans': 1})
        self.assertEqual(RecoveryPolicy.from_dict(RecoveryPolicy().to_dict()), RecoveryPolicy())

    def test_no_replans_policy_never_requests_a_second_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            result, app = run_recovery_simulation(Path(directory) / 'run', 'planning_change_always', replace(RecoveryPolicy(), max_replans=0))
        self.assertEqual(result['request_attempts'], 1); self.assertEqual(app.calls, [])

    def test_model_socket_timeout_is_clamped_and_restored_without_retry(self):
        client = ChatClient(ModelConfig('http://127.0.0.1:8000/v1', 'test', timeout=120))
        original = client.config; seen = []
        def invoke(messages):
            seen.append(client.config.timeout); client.attempts += 1
            return {'content': 'fixture'}
        with patch.object(client, 'invoke', side_effect=invoke):
            BudgetClient(client, self.budget).invoke([])
        self.assertEqual(seen, [60]); self.assertIs(client.config, original); self.assertEqual(client.attempts, 1)

    def test_failed_transport_restores_client_config_and_is_not_retried(self):
        client = ChatClient(ModelConfig('http://127.0.0.1:8000/v1', 'test', timeout=120))
        original = client.config
        with patch.object(client, 'invoke', side_effect=TimeoutError('injected')) as invoke:
            with self.assertRaises(TimeoutError): BudgetClient(client, self.budget).invoke([])
        self.assertIs(client.config, original); invoke.assert_called_once()

    def test_model_call_after_deadline_is_never_started(self):
        client = Mock(); self.app.time = 60
        with self.assertRaises(RecoveryError): BudgetClient(client, self.budget).invoke([])
        client.invoke.assert_not_called()

    def test_permission_cause_wrapped_as_state_error_is_permanent(self):
        exc = StateUnavailable('wrapped'); exc.__cause__ = PermissionError('denied')
        self.assertFalse(retryable_observation(exc)); self.assertEqual(failure_category(exc), 'permission_denied')

    def test_wrong_hwnd_state_file_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            path.write_text(json.dumps({'timestamp': 100, 'hwnd': 999, 'values': {}}))
            with patch('gui_workflow.verification.time.time', return_value=100):
                with self.assertRaises(StateUnavailable) as error: StateFile(path, self.app)()
        self.assertFalse(error.exception.retryable)

    def test_stale_state_file_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'; path.write_text(json.dumps({'timestamp': 90, 'hwnd': 101, 'values': {}}))
            with patch('gui_workflow.verification.time.time', return_value=100):
                with self.assertRaises(StateUnavailable) as error: StateFile(path, self.app)()
        self.assertTrue(error.exception.retryable)

    def test_observation_finishing_after_deadline_is_not_accepted(self):
        observer = Mock()
        def slow(label):
            self.app.time = 6
            return Mock(captured_at=6, state={'values': {}})
        observer.observe.side_effect = slow
        with self.assertRaises(RecoveryError) as error: RecoveringObserver(observer, self.budget).observe('slow')
        self.assertEqual(error.exception.code, 'observation_timeout'); observer.observe.assert_called_once()

    def test_enclosing_verification_deadline_keeps_its_own_error_code(self):
        observer=Mock()
        def slow(label): self.app.time=1; return Mock()
        observer.observe.side_effect=slow
        with self.assertRaises(RecoveryError) as error:
            RecoveringObserver(observer,self.budget).observe_until('verification',.5,'verification_timeout')
        self.assertEqual(error.exception.code,'verification_timeout')

    def test_enclosing_lookup_deadline_keeps_its_own_error_code(self):
        observer=Mock()
        def slow(label): self.app.time=1; return Mock()
        observer.observe.side_effect=slow
        with self.assertRaises(RecoveryError) as error:
            RecoveringObserver(observer,self.budget).observe_until('lookup',.5,'lookup_timeout')
        self.assertEqual(error.exception.code,'lookup_timeout')

    def test_persistence_failure_keeps_possible_dispatch_marker_and_no_replan(self):
        original = RecoveringWorkflowRunner.persist
        def persist(runner, summary):
            original(runner, summary)
            if any(r.get('dispatch_started') for r in summary['steps']): raise OSError('Injected checkpoint failure')
        with tempfile.TemporaryDirectory() as directory, patch.object(RecoveringWorkflowRunner, 'persist', persist):
            result, app = run_recovery_simulation(Path(directory) / 'run')
        self.assertEqual(result['status'], 'failed'); self.assertEqual(result['input_dispatches_started'], 1)
        self.assertTrue(result['input_delivery_uncertain']); self.assertEqual(result['request_attempts'], 1); self.assertEqual(app.calls, [])

    def test_desktop_observer_rejects_simulated_sources_on_every_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = SimulatedObserver(self.app, directory)
            with self.assertRaises(RecoveryError) as error: RecoveringObserver(observer, self.budget).observe('wrong_source')
        self.assertEqual(error.exception.code, 'invalid_observation'); self.assertEqual(self.budget.observations, 1)

    def test_missing_declared_state_fields_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            observer = SimulatedObserver(self.app, directory)
            watched = RecoveringObserver(observer, self.budget, 'simulation', ('missing_field',))
            with self.assertRaises(StateUnavailable): watched.observe('missing_state')
        self.assertEqual(self.budget.observations, 3); self.assertEqual(self.app.calls, [])

    def test_preview_replans_without_sending_any_input(self):
        with tempfile.TemporaryDirectory() as directory:
            result, app = run_recovery_simulation(Path(directory) / 'run', 'planning_change_once', execute=False)
        self.assertEqual(result['status'], 'planned'); self.assertEqual(result['request_attempts'], 2)
        self.assertFalse(result['task_verified']); self.assertEqual(app.calls, [])

    def test_invalid_cli_recovery_policy_is_rejected_before_controller_creation(self):
        from run_agent import main
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'policy.json'; path.write_text('{"max_replans":999}')
            with patch('sys.argv', ['run_agent.py', '--instruction', 'task', '--window-title', 'test', '--state-file', 'unused',
                                    '--state-fields', 'status', '--replay', str(Path(__file__).resolve().parents[1] / 'docs/agent-plan.example.json'),
                                    '--recovery-policy', str(path)]), patch('run_agent.DesktopController') as control:
                self.assertEqual(main(), 2)
            control.assert_not_called()

    def test_cli_recovery_flag_selects_new_session_without_real_input(self):
        from run_agent import main
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            with patch('sys.argv', ['run_agent.py', '--instruction', 'task', '--window-title', 'test', '--state-file', 'unused',
                                    '--state-fields', 'status', '--replay', str(root / 'docs/agent-plan.example.json'),
                                    '--recovery-policy', str(root / 'configs/week3-thursday.json'), '--countdown', '0', '--output', str(Path(directory) / 'run')]), \
                 patch('run_agent.DesktopController'), patch('run_agent.ScreenObserver'), patch('gui_agent.recovery.RecoverySession') as session:
                session.return_value.run.return_value = {'status': 'planned', 'success': False, 'environment': 'desktop', 'desktop_input_sent': False}
                self.assertEqual(main(), 0)
                self.assertIsInstance(session.call_args.kwargs['policy'], RecoveryPolicy)
                session.return_value.run.assert_called_once()


class RecoveryHttpTests(unittest.TestCase):
    def run_case(self, scenario):
        from gui_agent.recovery_http_fixture import run_http_recovery
        with tempfile.TemporaryDirectory() as directory:
            return run_http_recovery(Path(directory) / 'run', scenario)

    def test_loopback_http_normal_completes_both_goal_channels(self):
        result, app, _ = self.run_case('normal')
        self.assertTrue(result['task_verified']); self.assertEqual(len(app.calls), 3)
        self.assertEqual(result['request_attempts'], 1); self.assertEqual(result['real_model_validation'], 'pending')

    def test_replanning_http_request_contains_new_live_state(self):
        result, app, contexts = self.run_case('planning_change_once')
        self.assertEqual(result['request_attempts'], 2); self.assertEqual(result['replans_used'], 1)
        self.assertEqual(contexts[0]['independent_state']['status'], 'WAITING')
        self.assertEqual(contexts[1]['independent_state']['status'], 'HTTP CHANGED')
        self.assertTrue(result['task_verified']); self.assertEqual(len(app.calls), 3)

    def test_http_429_does_not_trigger_replanning_or_repeat_requests(self):
        result, app, contexts = self.run_case('rate_limited')
        self.assertEqual(result['error']['code'], 'rate_limited'); self.assertEqual(len(contexts), 1)
        self.assertEqual(result['request_attempts'], 1); self.assertEqual(app.calls, [])

    def test_stop_during_http_response_prevents_all_input(self):
        result, app, _ = self.run_case('stop_during_model')
        self.assertEqual(result['status'], 'aborted'); self.assertEqual(result['request_attempts'], 1)
        self.assertEqual(app.calls, [])


if __name__ == '__main__': unittest.main()
