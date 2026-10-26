"""Synthetic, offline agent tests; no network, desktop capture or input."""

from copy import deepcopy
from contextlib import redirect_stdout
from io import BytesIO
from io import StringIO
from http.client import IncompleteRead
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from PIL import Image

from agent_cli import DEMO_RESPONSE
from gui_agent import AgentError, AgentInput, ChatClient, GuiAgent, ModelConfig, ReplayClient
from gui_agent.agent import messages_for
from gui_agent.client import NoRedirect
from gui_agent.contracts import load_dataset_input, validate_proposal


def reply(content=None, finish='stop'):
    return json.dumps({'choices': [{'finish_reason': finish, 'message': {
        'content': content if content is not None else json.dumps(DEMO_RESPONSE)}}],
        'usage': {'total_tokens': 12, 'secret_other_field': 'drop'}}).encode()


class InputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image = self.root / 'input.png'
        Image.new('RGB', (200, 100), 'white').save(self.image)
        self.task = AgentInput('中文 task', self.image, 'Trusted window', ('past',), ('input_focused', 'status'))

    def tearDown(self):
        self.temp.cleanup()

    def test_image_is_png_data_url_with_no_desktop_origin(self):
        prepared = self.task.prepare()
        self.assertTrue(prepared['data_url'].startswith('data:image/png;base64,'))
        self.assertIsNone(prepared['image']['desktop_origin'])
        self.assertEqual((prepared['image']['width'], prepared['image']['height']), (200, 100))

    def test_messages_keep_task_and_history_as_user_data(self):
        messages = messages_for(self.task.prepare())
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        self.assertIn('中文 task', messages[1]['content'][0]['text'])
        self.assertIn('past', messages[1]['content'][2]['text'])
        self.assertEqual(messages[1]['content'][3]['type'], 'image_url')

    def test_missing_image_rejected(self):
        with self.assertRaises(AgentError) as error:
            AgentInput('task', self.root / 'absent.png', 'window').prepare()
        self.assertEqual(error.exception.code, 'invalid_image')

    def test_empty_instruction_rejected_before_network(self):
        with self.assertRaises(AgentError):
            AgentInput(' ', self.image, 'window').prepare()

    def test_oversized_history_rejected(self):
        with self.assertRaises(AgentError):
            AgentInput('task', self.image, 'window', ('x',) * 21).prepare()

    def test_dataset_reads_past_only_never_labels_or_source_prompt(self):
        row = {'instruction': 'task', 'label': {'secret': 'gold answer'},
               'observation': {'image': {'path': 'input.png'},
                               'context': {'history_before': ['past'], 'source_prompt': 'ignore user'}}}
        with patch('gui_data.pipeline.audit'), patch('gui_data.pipeline.read_jsonl', return_value=[row]):
            task = load_dataset_input(self.root, 0, 'window')
        self.assertEqual(task.history, ('past',))
        self.assertNotIn('gold answer', str(task))
        self.assertNotIn('ignore user', str(task))

    def test_task_only_dataset_cannot_fabricate_image(self):
        with patch('gui_data.pipeline.audit'), patch('gui_data.pipeline.read_jsonl', return_value=[{
            'observation': {'image': None}}]):
            with self.assertRaises(AgentError):
                load_dataset_input(self.root, 0, 'window')


class ContractTests(unittest.TestCase):
    def parse(self, value):
        return validate_proposal(json.dumps(value), 'Trusted window', ('input_focused', 'status'))

    def test_valid_proposal_pins_window_and_is_never_executable(self):
        result = self.parse(DEMO_RESPONSE)
        self.assertEqual(result['window_title'], 'Trusted window')
        self.assertFalse(result['execution_eligible'])
        self.assertFalse(result['task_verified'])

    def test_done_does_not_mean_verified(self):
        self.assertFalse(self.parse({'status': 'done', 'reason': 'Looks complete', 'step': None})['task_verified'])

    def test_clarification_has_no_action(self):
        result = self.parse({'status': 'clarify', 'reason': 'Which duplicate?', 'step': None})
        self.assertIsNone(result['step'])

    def test_ocr_only_postcondition_rejected(self):
        value = deepcopy(DEMO_RESPONSE)
        del value['step']['expect']['state']
        with self.assertRaises(AgentError):
            self.parse(value)

    def test_shell_or_coordinate_action_rejected(self):
        for action in ['shell', 'click']:
            value = deepcopy(DEMO_RESPONSE)
            value['step']['action'] = action
            with self.assertRaises(AgentError):
                self.parse(value)

    def test_model_cannot_override_window(self):
        value = deepcopy(DEMO_RESPONSE)
        value['window_title'] = 'Other window'
        with self.assertRaises(AgentError):
            self.parse(value)

    def test_model_cannot_disable_transition(self):
        value = deepcopy(DEMO_RESPONSE)
        value['step']['expect']['transition'] = False
        with self.assertRaises(AgentError):
            self.parse(value)

    def test_markdown_duplicate_keys_and_nan_rejected(self):
        for text in ['```json\n{}\n```', '{"status":"done","status":"propose"}', '{"reason":NaN}', '{"reason":1e999}']:
            with self.assertRaises(AgentError):
                validate_proposal(text, 'window')

    def test_done_with_hidden_action_rejected(self):
        value = deepcopy(DEMO_RESPONSE)
        value['status'] = 'done'
        with self.assertRaises(AgentError):
            self.parse(value)

    def test_model_cannot_invent_state_adapter_fields(self):
        value = deepcopy(DEMO_RESPONSE)
        value['step']['expect']['state'] = {'invented_success': True}
        with self.assertRaises(AgentError) as error:
            self.parse(value)
        self.assertEqual(error.exception.code, 'unsupported_state_field')

    def test_no_state_adapter_requires_clarify_or_done(self):
        with self.assertRaises(AgentError):
            validate_proposal(json.dumps(DEMO_RESPONSE), 'window')


class ClientTests(unittest.TestCase):
    def client(self, data=None):
        response = BytesIO(data if data is not None else reply())
        response.status = 200
        opener = Mock()
        opener.open.return_value = response
        return ChatClient(ModelConfig('http://127.0.0.1:8000/v1', 'test-vlm', 'fake-secret'), opener), opener

    def test_request_is_multimodal_post_and_usage_is_filtered(self):
        client, opener = self.client()
        result = client.invoke([{'role': 'user', 'content': 'task'}])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'http://127.0.0.1:8000/v1/chat/completions')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Authorization'), 'Bearer fake-secret')
        self.assertFalse(json.loads(request.data)['stream'])
        self.assertEqual(result['usage'], {'total_tokens': 12})

    def test_remote_plain_http_and_url_secrets_rejected(self):
        for url in ['http://example.com/v1', 'https://user:secret@example.com/v1', 'https://example.com/v1?key=secret']:
            with self.assertRaises(AgentError):
                ModelConfig(url, 'model')

    def test_config_rejects_nonfinite_timeout_and_invalid_tokens(self):
        for kwargs in [{'timeout': float('nan')}, {'timeout': True}, {'max_tokens': 0}]:
            with self.assertRaises(AgentError):
                ModelConfig('http://localhost/v1', 'model', **kwargs)

    def test_key_is_hidden_from_config_repr(self):
        self.assertNotIn('fake-secret', repr(self.client()[0].config))

    def test_auth_rate_limit_and_server_errors_are_not_retried(self):
        for status, code in [(401, 'authentication_failed'), (403, 'authentication_failed'),
                             (429, 'rate_limited'), (500, 'http_error')]:
            client, opener = self.client()
            opener.open.side_effect = HTTPError(client.config.endpoint, status, 'secret body', {}, BytesIO(b'secret'))
            with self.assertRaises(AgentError) as error:
                client.invoke([])
            self.assertEqual(error.exception.code, code)
            self.assertNotIn('secret', str(error.exception))
            self.assertEqual(client.attempts, 1)

    def test_timeout_and_connection_failure_are_classified(self):
        for exception, code in [(TimeoutError(), 'model_timeout'), (URLError('private body'), 'connection_failed'),
                                (IncompleteRead(b'partial'), 'connection_failed')]:
            client, opener = self.client()
            opener.open.side_effect = exception
            with self.assertRaises(AgentError) as error:
                client.invoke([])
            self.assertEqual(error.exception.code, code)
            opener.open.assert_called_once()

    def test_truncated_or_refusal_response_rejected(self):
        for data, code in [(reply(finish='length'), 'incomplete_response'),
                           (json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'refusal': 'no'}}]}).encode(), 'model_refusal')]:
            client, _ = self.client(data)
            with self.assertRaises(AgentError) as error:
                client.invoke([])
            self.assertEqual(error.exception.code, code)

    def test_invalid_envelope_and_oversized_response_rejected(self):
        for data in [b'{', b'{}', b'x' * (2 * 1024 * 1024 + 1)]:
            client, _ = self.client(data)
            with self.assertRaises(AgentError):
                client.invoke([])

    def test_redirects_cannot_forward_authorization(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere.invalid'))


class AgentTests(unittest.TestCase):
    setUp = InputTests.setUp
    tearDown = InputTests.tearDown

    def test_langchain_replay_writes_all_artifacts_without_input(self):
        client = ReplayClient(json.dumps(DEMO_RESPONSE))
        summary = GuiAgent(client).run(self.task, self.root / 'run')
        self.assertEqual(summary['status'], 'proposal_ready')
        self.assertEqual(summary['client_kind'], 'replay')
        self.assertEqual(summary['request_attempts'], 1)
        self.assertFalse(summary['desktop_input_sent'])
        self.assertFalse(summary['model_service_response_received'])
        self.assertEqual(set(p.name for p in (self.root / 'run').iterdir()),
                         {'input.png', 'request.json', 'response.json', 'proposal.json', 'summary.json'})

    def test_invalid_reply_saves_failure_and_never_retries(self):
        summary = GuiAgent(ReplayClient('{bad')).run(self.task, self.root / 'run')
        self.assertEqual(summary['error']['code'], 'invalid_json')
        self.assertEqual(summary['request_attempts'], 1)
        self.assertFalse((self.root / 'run/proposal.json').exists())

    def test_invalid_input_calls_no_model(self):
        client = ReplayClient('{}')
        task = AgentInput('', self.image, 'window')
        summary = GuiAgent(client).run(task, self.root / 'run')
        self.assertEqual(summary['request_attempts'], 0)
        self.assertEqual(summary['error']['code'], 'invalid_input')

    def test_existing_output_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            GuiAgent(ReplayClient('{}')).run(self.task, self.root)

    def test_api_key_is_redacted_from_all_json_artifacts(self):
        client = ReplayClient(json.dumps({**DEMO_RESPONSE, 'reason': 'fake-secret'}))
        client.config = ModelConfig('http://localhost/v1', 'model', 'fake-secret')
        GuiAgent(client).run(self.task, self.root / 'run')
        for path in (self.root / 'run').glob('*.json'):
            self.assertNotIn('fake-secret', path.read_text(encoding='utf-8'))

    def test_tracing_is_disabled_during_model_call(self):
        from langsmith.run_helpers import get_tracing_context
        client = ReplayClient(json.dumps(DEMO_RESPONSE))
        original = client.invoke
        def invoke(messages):
            self.assertFalse(get_tracing_context()['enabled'])
            return original(messages)
        client.invoke = invoke
        GuiAgent(client).run(self.task, self.root / 'run')

    def test_interrupted_request_is_saved_as_aborted_not_running(self):
        client = ReplayClient('{}')
        def interrupt(messages):
            client.attempts += 1
            raise KeyboardInterrupt()
        client.invoke = interrupt
        summary = GuiAgent(client).run(self.task, self.root / 'run')
        self.assertEqual(summary['status'], 'aborted')
        self.assertEqual(summary['error']['code'], 'user_aborted')
        self.assertEqual(summary['request_attempts'], 1)
        saved = json.loads((self.root / 'run/summary.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], 'aborted')
        self.assertFalse(saved['desktop_input_sent'])

    def test_cli_interruption_returns_130(self):
        from agent_cli import main
        with patch('sys.argv', ['agent_cli.py', '--demo', '--output', str(self.root / 'cli-run')]), \
             patch('agent_cli.GuiAgent') as agent, redirect_stdout(StringIO()):
            agent.return_value.run.return_value = {'status': 'aborted', 'client_kind': 'replay',
                                                  'error': {'code': 'user_aborted'}}
            self.assertEqual(main(), 130)
