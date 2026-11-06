"""Task planning contracts and LangChain compilation; no live model or desktop."""

from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from gui_agent import AgentError, AgentInput, GuiPlanner, ReplayClient
from gui_agent.planner import validate_task_plan
from gui_workflow.plan import validate_plan

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('input_focused', 'status', 'entry_text', 'submitted', 'submit_enabled')


def example():
    return json.loads((ROOT / 'docs/agent-plan.example.json').read_text(encoding='utf-8'))


def compile_plan(value, fields=FIELDS, max_steps=12):
    return validate_task_plan(json.dumps(value, ensure_ascii=False), 'Trusted window', fields, max_steps)


class PlanningContractTests(unittest.TestCase):
    def test_three_subtasks_compile_to_ordered_actions_and_workflow(self):
        result = compile_plan(example())
        self.assertEqual([x['subtask_id'] for x in result['action_sequence']], ['focus', 'enter', 'submit'])
        self.assertEqual([x['action'] for x in result['action_sequence']], ['click_text', 'type_text', 'click_text'])
        self.assertEqual(result['workflow']['steps'][1]['text'], 'Hello GUI 123 中文测试')
        self.assertEqual(result['workflow']['window_title'], 'Trusted window')
        self.assertFalse(result['execution_eligible'])
        self.assertFalse(result['task_verified'])

    def test_compiled_workflow_matches_existing_demo_contract(self):
        expected = json.loads((ROOT / 'plans/friday_demo.json').read_text(encoding='utf-8'))
        expected['window_title'] = 'Trusted window'
        self.assertEqual(compile_plan(example())['workflow'], validate_plan(expected))

    def test_default_step_names_are_global_not_restarted_per_subtask(self):
        value = example()
        for item in value['subtasks']:
            del item['steps'][0]['name']
        result = compile_plan(value)
        self.assertEqual([x['name'] for x in result['workflow']['steps']], ['step_1', 'step_2', 'step_3'])

    def test_duplicate_subtask_ids_are_rejected(self):
        value = example(); value['subtasks'][1]['id'] = 'focus'
        with self.assertRaises(AgentError):
            compile_plan(value)

    def test_cycles_forward_self_and_unknown_dependencies_are_rejected(self):
        for dependencies in [['submit'], ['focus'], ['missing']]:
            value = example(); value['subtasks'][0]['depends_on'] = dependencies
            with self.assertRaises(AgentError) as error:
                compile_plan(value)
            self.assertEqual(error.exception.code, 'invalid_dependency')

    def test_duplicate_dependencies_are_rejected(self):
        value = example(); value['subtasks'][1]['depends_on'] = ['focus', 'focus']
        with self.assertRaises(AgentError) as error:
            compile_plan(value)
        self.assertEqual(error.exception.code, 'invalid_dependency')

    def test_non_string_dependency_does_not_escape_classified_error(self):
        value = example(); value['subtasks'][1]['depends_on'] = [{}]
        with self.assertRaises(AgentError) as error:
            compile_plan(value)
        self.assertEqual(error.exception.code, 'invalid_dependency')

    def test_empty_task_or_subtask_is_rejected(self):
        for whole in (True, False):
            value = example()
            if whole:
                value['subtasks'] = []
            else:
                value['subtasks'][1]['steps'] = []
            with self.assertRaises(AgentError):
                compile_plan(value)

    def test_later_invalid_action_rejects_the_entire_plan(self):
        value = example(); value['subtasks'][-1]['steps'][0]['action'] = 'shell'
        with self.assertRaises(AgentError):
            compile_plan(value)

    def test_missing_independent_postcondition_is_rejected(self):
        value = example(); del value['subtasks'][-1]['steps'][0]['expect']['state']
        with self.assertRaises(AgentError):
            compile_plan(value)

    def test_later_unknown_state_field_is_rejected(self):
        value = example(); value['subtasks'][-1]['steps'][0]['expect']['state']['invented_success'] = True
        with self.assertRaises(AgentError) as error:
            compile_plan(value)
        self.assertEqual(error.exception.code, 'unsupported_state_field')

    def test_duplicate_step_names_across_subtasks_are_rejected(self):
        value = example(); value['subtasks'][2]['steps'][0]['name'] = 'focus_input'
        with self.assertRaises(AgentError):
            compile_plan(value)

    def test_step_limit_is_applied_to_the_whole_plan(self):
        with self.assertRaises(AgentError) as error:
            compile_plan(example(), max_steps=2)
        self.assertEqual(error.exception.code, 'plan_limit_exceeded')

    def test_invalid_step_limits_fail_before_request(self):
        for limit in [0, 33, True]:
            client = ReplayClient('{}')
            with self.assertRaises(AgentError):
                GuiPlanner(client, limit)
            self.assertEqual(client.attempts, 0)

    def test_fields_for_a_different_action_are_rejected(self):
        value = example(); value['subtasks'][0]['steps'][0]['keys'] = ['ctrl', 'a']
        with self.assertRaises(AgentError):
            compile_plan(value)

    def test_unknown_key_and_single_key_hotkey_are_rejected(self):
        for action in [{'action': 'press', 'key': 'not_a_key'}, {'action': 'hotkey', 'keys': ['ctrl']}]:
            value = example()
            old = value['subtasks'][0]['steps'][0]
            value['subtasks'][0]['steps'][0] = {**action, 'expect': old['expect']}
            with self.assertRaises(AgentError):
                compile_plan(value)

    def test_wait_press_hotkey_and_typing_have_explicit_arguments(self):
        value = example()
        condition = {'text': 'READY', 'state': {'status': 'READY'}}
        value['subtasks'] = [{'id': 'keys', 'goal': 'Exercise supported actions', 'depends_on': [], 'steps': [
            {'action': 'wait_text', 'expect': deepcopy(condition)},
            {'action': 'press', 'key': 'enter', 'expect': deepcopy(condition)},
            {'action': 'hotkey', 'keys': ['ctrl', 'a'], 'expect': deepcopy(condition)},
            {'action': 'type_text', 'text': ' 中文 123 ', 'expect': deepcopy(condition)}]}]
        result = compile_plan(value)
        self.assertEqual([x['arguments'] for x in result['action_sequence']],
                         [{}, {'key': 'enter'}, {'keys': ['ctrl', 'a']}, {'text': ' 中文 123 '}])

    def test_model_cannot_disable_action_transition(self):
        value = example(); value['subtasks'][1]['steps'][0]['expect']['transition'] = False
        with self.assertRaises(AgentError):
            compile_plan(value)

    def test_window_override_and_raw_coordinates_are_rejected(self):
        value = example(); value['window_title'] = 'Other window'
        with self.assertRaises(AgentError):
            compile_plan(value)
        value = example(); value['subtasks'][0]['steps'][0]['point'] = [100, 100]
        with self.assertRaises(AgentError):
            compile_plan(value)

    def test_clarify_and_done_do_not_contain_hidden_work(self):
        for status in ['clarify', 'done']:
            result = compile_plan({'status': status, 'reason': 'Observation only', 'subtasks': []}, fields=())
            self.assertIsNone(result['workflow'])
            self.assertFalse(result['task_verified'])
            value = example(); value['status'] = status
            with self.assertRaises(AgentError):
                compile_plan(value)

    def test_no_state_adapter_cannot_produce_action_plan(self):
        with self.assertRaises(AgentError) as error:
            compile_plan(example(), fields=())
        self.assertEqual(error.exception.code, 'unsupported_state_field')

    def test_strict_json_rejects_fences_duplicates_and_nonfinite_values(self):
        for content in ['```json\n{}\n```', '{"status":"done","status":"plan"}', '{"status":NaN}', '{"status":1e999}']:
            with self.assertRaises(AgentError):
                validate_task_plan(content, 'window', FIELDS)

    def test_subtask_count_is_bounded(self):
        value = example(); value['subtasks'] = [deepcopy(value['subtasks'][0]) for _ in range(9)]
        with self.assertRaises(AgentError):
            compile_plan(value)


class PlanningPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / 'image.png'
        Image.new('RGB', (640, 360), 'white').save(self.image)
        self.task = AgentInput('Focus input, enter Hello GUI 123 中文测试, then submit.', self.image,
                               'Trusted window', ('past action only',), FIELDS)

    def tearDown(self):
        self.temp.cleanup()

    def test_langchain_exports_task_actions_and_workflow_without_execution(self):
        summary = GuiPlanner(ReplayClient(json.dumps(example()))).run(self.task, self.root / 'run')
        self.assertEqual(summary['status'], 'plan_ready')
        self.assertEqual(summary['mode'], 'plan')
        self.assertEqual(summary['request_attempts'], 1)
        self.assertFalse(summary['desktop_input_sent'])
        self.assertEqual(set(p.name for p in (self.root / 'run').iterdir()),
                         {'input.png', 'request.json', 'response.json', 'task_plan.json',
                          'action_sequence.json', 'workflow.candidate.json', 'summary.json'})
        workflow = json.loads((self.root / 'run/workflow.candidate.json').read_text(encoding='utf-8'))
        self.assertEqual(len(validate_plan(workflow)['steps']), 3)

    def test_planning_prompt_has_limit_history_and_caller_state_contract(self):
        client = ReplayClient(json.dumps(example()))
        client.invoke = Mock(wraps=client.invoke)
        GuiPlanner(client, 5).run(self.task, self.root / 'run')
        messages = client.invoke.call_args.args[0]
        self.assertIn('Maximum total action count: 5', messages[0]['content'])
        self.assertIn('past action only', messages[1]['content'][2]['text'])
        self.assertIn('available_state_fields', messages[1]['content'][1]['text'])

    def test_invalid_last_step_never_exports_a_partial_workflow(self):
        value = example(); value['subtasks'][-1]['steps'][0]['action'] = 'shell'
        summary = GuiPlanner(ReplayClient(json.dumps(value))).run(self.task, self.root / 'run')
        self.assertEqual(summary['status'], 'failed')
        self.assertTrue((self.root / 'run/response.json').exists())
        self.assertFalse((self.root / 'run/workflow.candidate.json').exists())
        self.assertFalse((self.root / 'run/task_plan.json').exists())

    def test_clarification_exports_no_workflow(self):
        value = {'status': 'clarify', 'reason': 'Which duplicate control?', 'subtasks': []}
        summary = GuiPlanner(ReplayClient(json.dumps(value))).run(self.task, self.root / 'run')
        self.assertEqual(summary['status'], 'clarify')
        self.assertEqual(json.loads((self.root / 'run/action_sequence.json').read_text()), [])
        self.assertFalse((self.root / 'run/workflow.candidate.json').exists())

    def test_interrupted_planning_records_abort_and_no_candidate(self):
        client = ReplayClient('{}')
        client.invoke = Mock(side_effect=KeyboardInterrupt())
        summary = GuiPlanner(client).run(self.task, self.root / 'run')
        self.assertEqual(summary['status'], 'aborted')
        self.assertEqual(summary['error']['code'], 'user_aborted')
        self.assertFalse((self.root / 'run/workflow.candidate.json').exists())

    def test_planning_cli_demo_returns_plan_ready(self):
        from agent_cli import main
        output = self.root / 'cli'
        with patch('sys.argv', ['agent_cli.py', '--mode', 'plan', '--demo', '--output', str(output)]), redirect_stdout(StringIO()):
            self.assertEqual(main(), 0)
        summary = json.loads((output / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['status'], 'plan_ready')
        self.assertEqual(summary['client_kind'], 'replay')

    def test_planning_cli_reports_too_small_step_limit(self):
        from agent_cli import main
        with patch('sys.argv', ['agent_cli.py', '--mode', 'plan', '--demo', '--max-steps', '2',
                                '--output', str(self.root / 'cli')]), redirect_stdout(StringIO()):
            self.assertEqual(main(), 1)
        summary = json.loads((self.root / 'cli/summary.json').read_text(encoding='utf-8'))
        self.assertEqual(summary['error']['code'], 'plan_limit_exceeded')
