"""Closed-loop integration using a synthetic app, separate OCR/state and fake time."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from gui_agent import AgentError, GuiPlanner, ReplayClient
from gui_agent.session import AgentSession
from gui_agent.simulation import FIELDS, INSTRUCTION, SimulatedApp, SimulatedObserver, run_simulation
from gui_workflow.runner import WorkflowRunner

ROOT = Path(__file__).resolve().parents[1]


def goal():
    return json.loads((ROOT / 'docs/agent-goal.example.json').read_text(encoding='utf-8'))


def reply():
    value = json.loads((ROOT / 'docs/agent-plan.example.json').read_text(encoding='utf-8'))
    for item in value['subtasks']:
        for step in item['steps']:
            step['timeout'] = .2
    return value


class SessionFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def build(self, value=None, environment='simulation'):
        app = SimulatedApp()
        observer = SimulatedObserver(app, self.root / 'observations')
        planner = GuiPlanner(ReplayClient(json.dumps(reply() if value is None else value, ensure_ascii=False)))
        factory = lambda c, o, p: WorkflowRunner(c, o, p, poll_interval=.1, clock=lambda: app.time, sleep=app.sleep)
        session = AgentSession(app, observer, planner, self.root / 'session', environment, factory)
        return session, app, observer, planner

    def test_full_loop_verifies_three_steps_and_caller_goal(self):
        summary, app = run_simulation(self.root / 'demo')
        self.assertEqual(summary['status'], 'completed')
        self.assertTrue(summary['task_verified'])
        self.assertEqual(summary['actions_completed'], 3)
        self.assertEqual(summary['request_attempts'], 1)
        self.assertFalse(summary['desktop_input_sent'])
        self.assertTrue(summary['goal_verification']['ocr']['met'])
        self.assertTrue(summary['goal_verification']['state']['met'])
        self.assertEqual([x['action'] for x in app.calls], ['click', 'type_text', 'click'])
        feedback = json.loads((self.root / 'demo/session/feedback.json').read_text(encoding='utf-8'))
        self.assertEqual(len(feedback['workflow_steps']), 3)
        self.assertTrue(all(row['verification']['ocr']['met'] and row['verification']['state']['met'] for row in feedback['workflow_steps']))

    def test_preview_plans_without_dispatching(self):
        summary, app = run_simulation(self.root / 'demo', execute=False)
        self.assertEqual(summary['status'], 'planned')
        self.assertFalse(summary['task_verified'])
        self.assertFalse(summary['success'])
        self.assertEqual(app.calls, [])

    def test_state_change_during_model_wait_prevents_input(self):
        summary, app = run_simulation(self.root / 'demo', after_planning=lambda a: a.values.update(entry_text='external change'))
        self.assertEqual(summary['error']['code'], 'planning_context_changed')
        self.assertEqual(app.calls, [])

    def test_window_change_during_planning_is_guard_failure(self):
        summary, app = run_simulation(self.root / 'demo', after_planning=lambda a: setattr(a, 'identity', 202))
        self.assertEqual(summary['error']['category'], 'window_guard_failure')
        self.assertEqual(app.calls, [])

    def test_stop_during_planning_is_checked_before_input(self):
        summary, app = run_simulation(self.root / 'demo', after_planning=lambda a: setattr(a, 'stopped', True))
        self.assertEqual(summary['status'], 'aborted')
        self.assertEqual(summary['error']['category'], 'user_aborted')
        self.assertEqual(app.calls, [])

    def test_missing_goal_rejected_before_model_or_input(self):
        session, app, _, planner = self.build()
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, execute=True)
        self.assertEqual(summary['error']['code'], 'goal_required')
        self.assertEqual(planner.client.attempts, 0)
        self.assertEqual(app.calls, [])

    def test_model_clarification_is_returned_without_execution(self):
        summary, app = run_simulation(self.root / 'demo', reply={'status': 'clarify', 'reason': 'Which target?', 'subtasks': []})
        self.assertEqual(summary['status'], 'needs_clarification')
        self.assertEqual(app.calls, [])

    def test_model_done_cannot_forge_task_completion(self):
        summary, app = run_simulation(self.root / 'demo', reply={'status': 'done', 'reason': 'Claimed success', 'subtasks': []})
        self.assertEqual(summary['error']['code'], 'model_done_unverified')
        self.assertFalse(summary['task_verified'])
        self.assertEqual(app.calls, [])

    def test_goal_already_met_requires_both_channels_and_no_model(self):
        session, app, _, planner = self.build()
        app.values['submitted'] = goal()['state']['submitted']
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal(), execute=True)
        self.assertEqual(summary['status'], 'already_satisfied')
        self.assertTrue(summary['task_verified'])
        self.assertEqual(planner.client.attempts, 0)
        self.assertEqual(app.calls, [])

    def test_goal_ocr_without_state_is_rejected(self):
        session, app, _, planner = self.build()
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, {'text': 'DONE'}, execute=True)
        self.assertEqual(summary['error']['code'], 'invalid_goal')
        self.assertEqual(planner.client.attempts, 0)

    def test_actual_adapter_must_export_declared_fields(self):
        session, app, _, planner = self.build()
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS + ('not_available',), goal(), execute=True)
        self.assertEqual(summary['error']['code'], 'unsupported_state_field')
        self.assertEqual(planner.client.attempts, 0)

    def test_simulated_capture_cannot_enter_desktop_session(self):
        session, app, _, planner = self.build(environment='desktop')
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal(), execute=True)
        self.assertEqual(summary['error']['code'], 'invalid_observation')
        self.assertFalse(summary['desktop_input_sent'])
        self.assertEqual(planner.client.attempts, 0)

    def test_missing_independent_state_stops_before_model(self):
        session, app, observer, planner = self.build()
        original = observer.observe
        def no_state(label):
            snapshot = original(label); snapshot.state = None
            return snapshot
        observer.observe = no_state
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal(), execute=True)
        self.assertEqual(summary['error']['category'], 'state_unavailable')
        self.assertEqual(planner.client.attempts, 0)

    def test_later_invalid_model_action_blocks_all_execution(self):
        value = reply(); value['subtasks'][-1]['steps'][0]['action'] = 'shell'
        summary, app = run_simulation(self.root / 'demo', reply=value)
        self.assertEqual(summary['error']['category'], 'planning_failure')
        self.assertEqual(app.calls, [])

    def test_failed_model_request_is_not_retried(self):
        session, app, _, planner = self.build()
        original = planner.client.invoke
        def timeout(messages):
            original(messages)
            raise AgentError('model_timeout', 'Synthetic timeout')
        planner.client.invoke = timeout
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal(), execute=True)
        self.assertEqual(summary['error']['code'], 'model_timeout')
        self.assertEqual(summary['request_attempts'], 1)
        self.assertEqual(app.calls, [])

    def test_live_context_contains_actual_ocr_state_and_trusted_goal(self):
        session, app, _, planner = self.build()
        planner.client.invoke = Mock(wraps=planner.client.invoke)
        session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal(), execute=False)
        request = planner.client.invoke.call_args.args[0]
        context = json.loads(request[1]['content'][2]['text'].split('\n', 1)[1])['live_observation']
        self.assertFalse(context['independent_state']['input_focused'])
        self.assertEqual(context['goal']['text'], goal()['text'])
        self.assertTrue(any(x['text'] == 'INPUT FIELD' for x in context['ocr']))

    def test_stop_requested_during_model_timeout_takes_precedence(self):
        session, app, _, planner = self.build()
        def timeout(messages):
            planner.client.attempts += 1
            app.stopped = True
            raise AgentError('model_timeout', 'Synthetic timeout')
        planner.client.invoke = timeout
        summary = session.run(INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal(), execute=True)
        self.assertEqual(summary['status'], 'aborted')
        self.assertEqual(summary['error']['category'], 'user_aborted')
        self.assertEqual(app.calls, [])


class SessionFailureTests(unittest.TestCase):
    setUp = SessionFlowTests.setUp
    tearDown = SessionFlowTests.tearDown

    def test_duplicate_target_stops_without_input(self):
        summary, app = run_simulation(self.root / 'demo', scenario='duplicate')
        self.assertEqual(summary['error']['category'], 'localization_failure')
        self.assertEqual(summary['input_dispatches_started'], 0)
        self.assertEqual(app.calls, [])

    def test_missing_target_reobserves_without_dispatch(self):
        summary, app = run_simulation(self.root / 'demo', scenario='missing')
        self.assertEqual(summary['error']['code'], 'target_unavailable')
        self.assertEqual(app.calls, [])

    def test_no_change_sends_input_once(self):
        summary, app = run_simulation(self.root / 'demo', scenario='no_change')
        self.assertEqual(summary['error']['code'], 'verification_timeout')
        self.assertEqual(len(app.calls), 1)

    def test_forged_ocr_is_blocked_by_independent_state(self):
        summary, app = run_simulation(self.root / 'demo', scenario='false_ocr')
        feedback = json.loads((self.root / 'demo/session/feedback.json').read_text(encoding='utf-8'))
        evidence = feedback['workflow_steps'][0]['verification']
        self.assertTrue(evidence['ocr']['met'])
        self.assertFalse(evidence['state']['met'])
        self.assertFalse(summary['task_verified'])
        self.assertEqual(len(app.calls), 1)

    def test_partial_input_failure_is_not_replayed(self):
        summary, app = run_simulation(self.root / 'demo', scenario='partial_input')
        self.assertEqual(summary['input_dispatches_started'], 2)
        self.assertEqual(summary['actions_completed'], 1)
        self.assertEqual(app.values['entry_text'], 'Hel')
        self.assertEqual(len(app.calls), 2)

    def test_valid_steps_cannot_override_a_different_caller_goal(self):
        target = goal(); target['text'] = 'OTHER RESULT'; target['state']['submitted'] = 'OTHER RESULT'
        summary, app = run_simulation(self.root / 'demo', goal=target)
        self.assertEqual(summary['error']['code'], 'goal_not_met')
        self.assertEqual(summary['actions_completed'], 3)
        self.assertFalse(summary['task_verified'])

    def test_cli_demo_runs_the_simulated_closed_loop(self):
        from run_agent import main
        with patch('sys.argv', ['run_agent.py', '--demo', '--output', str(self.root / 'cli')]), redirect_stdout(StringIO()):
            self.assertEqual(main(), 0)
        summary = json.loads((self.root / 'cli/session/summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['environment'], 'simulation')
        self.assertFalse(summary['desktop_input_sent'])

    def test_cli_execution_requires_trusted_goal_before_desktop_access(self):
        from run_agent import main
        with patch('sys.argv', ['run_agent.py', '--execute', '--instruction', 'task', '--window-title', 'window',
                                '--state-file', 'unused.json', '--state-fields', 'status']), \
             patch('run_agent.DesktopController') as controller, patch('sys.stderr', StringIO()):
            with self.assertRaises(SystemExit) as error:
                main()
        self.assertEqual(error.exception.code, 2)
        controller.assert_not_called()

    def test_cli_cannot_claim_no_input_after_uncertain_session_io_failure(self):
        from run_agent import main
        output = self.root / 'io-failure'
        with patch('sys.argv', ['run_agent.py', '--execute', '--instruction', 'task', '--window-title', 'window',
                                '--state-file', 'unused.json', '--state-fields', 'submitted', 'submit_enabled',
                                '--goal-file', str(ROOT / 'docs/agent-goal.example.json'), '--replay', str(ROOT / 'docs/agent-plan.example.json'),
                                '--countdown', '0', '--output', str(output)]), \
             patch('run_agent.DesktopController'), patch('run_agent.ScreenObserver'), \
             patch('run_agent.AgentSession') as session, redirect_stdout(StringIO()):
            session.return_value.run.side_effect = OSError('Synthetic persistence failure')
            self.assertEqual(main(), 2)
        saved = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
        self.assertTrue(saved['desktop_input_uncertain'])
        self.assertTrue(saved['desktop_input_sent'])


class CheckedMouseTests(unittest.TestCase):
    def test_successful_sendinput_contains_down_then_up(self):
        from gui_control.unicode_input import click_button
        seen = []
        def sender(count, batch, size):
            self.assertEqual(count, 1)
            seen.extend(batch[i].value.mouse.flags for i in range(count))
            return count
        click_button('left', sender)
        self.assertEqual(seen, [0x0002, 0x0004])

    def test_rejected_click_does_not_retry(self):
        from gui_control.unicode_input import click_button
        sender = Mock(return_value=0)
        with self.assertRaises(OSError):
            click_button('left', sender)
        sender.assert_called_once()

    def test_failed_event_preserves_native_error_code_without_replaying(self):
        from gui_control.unicode_input import InputSendError, click_button
        sender = Mock(return_value=0)
        sender._gui_agent_native = True
        with patch('gui_control.unicode_input.ctypes.set_last_error', create=True) as clear, \
             patch('gui_control.unicode_input.ctypes.get_last_error', return_value=5, create=True):
            with self.assertRaises(InputSendError) as error:
                click_button('left', sender)
        clear.assert_called_once_with(0)
        self.assertEqual(error.exception.winerror, 5)
        self.assertEqual(error.exception.accepted, 0)
        sender.assert_called_once()

    def test_partial_click_only_releases_without_second_down(self):
        from gui_control.unicode_input import click_button
        calls = []
        def sender(count, batch, size):
            calls.append([batch[i].value.mouse.flags for i in range(count)])
            return 0 if len(calls) == 2 else 1
        with self.assertRaises(OSError):
            click_button('left', sender)
        self.assertEqual(calls, [[0x0002], [0x0004], [0x0004]])

    def test_controller_checked_sender_does_not_also_call_legacy_click(self):
        from gui_control.controller import DesktopController
        with tempfile.TemporaryDirectory() as folder:
            backend = Mock(); backend.size.return_value = (1920, 1080)
            backend.position.return_value = (100, 200)
            sender = Mock()
            with DesktopController('test', backend=backend, guard=Mock(), listen=False,
                                   log_path=Path(folder) / 'actions.jsonl', click_sender=sender) as control:
                control.click(100, 200)
            sender.assert_called_once_with('left')
            backend.click.assert_not_called()

    def test_checked_sender_stops_when_pointer_move_did_not_take_effect(self):
        from gui_control.controller import ControlError, DesktopController
        with tempfile.TemporaryDirectory() as folder:
            backend = Mock(); backend.size.return_value = (1920, 1080)
            backend.position.return_value = (500, 500)
            sender = Mock()
            with DesktopController('test', backend=backend, guard=Mock(), listen=False,
                                   log_path=Path(folder) / 'actions.jsonl', click_sender=sender) as control:
                with self.assertRaises(ControlError):
                    control.click(100, 200)
            sender.assert_not_called()


class CheckedUnicodeTests(unittest.TestCase):
    def test_chinese_character_is_sent_as_individual_down_and_up(self):
        from gui_control.unicode_input import type_character
        calls = []
        def sender(count, batch, size):
            self.assertEqual(count, 1)
            event = batch[0]
            calls.append((event.type, event.value.keyboard.scan, event.value.keyboard.flags))
            return 1
        type_character('\u4e2d', sender)
        self.assertEqual(calls, [(1, 0x4e2d, 4), (1, 0x4e2d, 6)])

    def test_non_bmp_character_preserves_utf16_surrogate_pair(self):
        from gui_control.unicode_input import type_character
        scans = []
        def sender(count, batch, size):
            scans.append(batch[0].value.keyboard.scan)
            return 1
        type_character('\U0001f600', sender)
        self.assertEqual(scans, [0xd83d, 0xd83d, 0xde00, 0xde00])

    def test_failed_unicode_key_up_only_releases_then_stops(self):
        from gui_control.unicode_input import type_character
        flags = []
        def sender(count, batch, size):
            flags.append(batch[0].value.keyboard.flags)
            return 0 if len(flags) == 2 else 1
        with self.assertRaises(OSError):
            type_character('A', sender)
        self.assertEqual(flags, [4, 6, 6])


class InputDiagnosticTests(unittest.TestCase):
    def test_probe_requests_zero_events_without_mouse_or_keyboard_payload(self):
        from scripts.diagnose_input import probe_input_api
        sender = Mock(return_value=0)
        result = probe_input_api(sender)
        self.assertEqual(sender.call_args.args[0:2], (0, None))
        self.assertTrue(result['return_is_zero'])
        self.assertFalse(result['actual_input_delivery_tested'])

    def test_probe_reports_unexpected_nonzero_without_treating_it_as_success(self):
        from scripts.diagnose_input import probe_input_api
        result = probe_input_api(Mock(return_value=123))
        self.assertFalse(result['return_is_zero'])
        self.assertEqual(result['diagnostic_status'], 'inconclusive')
        self.assertEqual(result['zero_event_return'], 123)
        self.assertEqual(result['events_requested'], 0)
