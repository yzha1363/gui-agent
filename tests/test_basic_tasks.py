"""Task-specific effects and independent acceptance; never access the desktop."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gui_tasks.basic import (CASES, FIELDS, MESSAGE, SAMPLE, LocalTaskState, audit_result,
                             reply_for, run_synthetic, task_spec)
from gui_tasks.browser import LocalWebApp, OwnedBrowser
from gui_agent.planner import validate_task_plan


class BasicTaskStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def app(self, case, fault='none', lifecycle=None):
        return LocalTaskState(case, self.temp.name, fault, lifecycle)

    def test_file_hash_is_computed_from_actual_bytes(self):
        app = self.app('open_file')
        app.sample.write_bytes(b'changed sample')
        app.event('finish')
        self.assertEqual(app.values['file_sha256'], hashlib.sha256(b'changed sample').hexdigest())
        self.assertNotEqual(app.values['file_sha256'], task_spec('open_file').goal['state']['file_sha256'])

    def test_permission_denial_does_not_claim_file_open(self):
        app = self.app('open_file', 'permission_denied')
        app.event('finish')
        self.assertFalse(app.values['file_open'])
        self.assertEqual(app.values['app_error'], 'permission_denied')

    def test_real_permission_error_is_exported(self):
        app = self.app('open_file')
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('test')):
            app.event('finish')
        self.assertEqual(app.values['status'], 'ACCESS DENIED')

    def test_unknown_search_does_not_forge_a_match(self):
        app = self.app('search')
        app.event('focus'); app.event('input', 'absent'); app.event('finish')
        self.assertEqual(app.values['result'], 'NOT FOUND')

    def test_input_without_focus_is_rejected(self):
        with self.assertRaises(ValueError):
            self.app('search').event('input', 'GUI Agent')

    def test_message_retry_keeps_one_inbox_item(self):
        app = self.app('send_message')
        app.event('focus'); app.event('input', MESSAGE)
        app.event('finish'); app.event('finish')
        self.assertEqual(app.values['message_count'], 1)
        self.assertEqual(json.loads((app.directory / 'inbox.json').read_text())[0]['recipient'], 'local-test-inbox')

    def test_reused_message_id_with_changed_text_is_rejected(self):
        app = self.app('send_message')
        app.event('focus'); app.event('input', MESSAGE); app.event('finish')
        app.event('input', 'changed')
        with self.assertRaises(ValueError): app.event('finish')
        self.assertEqual(len(app.inbox), 1)

    def test_failed_lifecycle_cannot_set_success_state(self):
        def fail(case): raise RuntimeError('Owned window refused transition')
        app = self.app('open_browser', lifecycle=fail)
        with self.assertRaises(RuntimeError): app.event('finish')
        self.assertFalse(app.values['browser_open'])
        self.assertEqual(app.values['status'], 'READY')

    def test_arbitrary_file_path_or_event_is_not_supported(self):
        app = self.app('open_file')
        for action in ('open_path', 'shell', 'send_email'):
            with self.subTest(action=action), self.assertRaises(ValueError): app.event(action, 'secret.txt')

    def test_snapshot_cannot_mutate_backend_state(self):
        app = self.app('open_file')
        app.snapshot()['file_open'] = True
        self.assertFalse(app.values['file_open'])


class BasicTaskFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def run_case(self, case, fault='none'):
        directory = Path(self.temp.name) / (case + fault)
        summary, app = run_synthetic(case, directory, fault)
        return summary, app, audit_result(summary, app.snapshot(), case, fault)

    def test_all_five_tasks_compile_and_verify_independent_goals(self):
        for case in CASES:
            with self.subTest(case=case):
                spec = task_spec(case)
                validate_task_plan(json.dumps(reply_for(spec)), 'Test', FIELDS)
                summary, app, audit = self.run_case(case)
                self.assertTrue(audit['passed'], audit)
                self.assertFalse(summary['desktop_input_sent'])
                self.assertEqual(summary['request_attempts'], 1)

    def test_false_ocr_cannot_open_file(self):
        summary, app, audit = self.run_case('open_file', 'false_ocr')
        self.assertTrue(audit['passed'], audit)
        self.assertFalse(app.values['file_open'])
        self.assertEqual(summary['input_dispatches_started'], 1)

    def test_duplicate_focus_never_types_or_sends(self):
        summary, app, audit = self.run_case('send_message', 'duplicate')
        self.assertTrue(audit['passed'], audit)
        self.assertEqual(app.inbox, [])
        self.assertEqual(summary['input_dispatches_started'], 0)

    def test_missing_focus_never_types(self):
        summary, app, audit = self.run_case('search', 'missing')
        self.assertTrue(audit['passed'], audit)
        self.assertEqual(app.values['draft'], '')

    def test_no_change_does_not_replay_open(self):
        summary, app, audit = self.run_case('open_file', 'no_change')
        self.assertTrue(audit['passed'], audit)
        self.assertEqual(app.events, [{'action': 'finish', 'length': 0}])

    def test_permission_cause_is_separate_from_verification_timeout(self):
        summary, app, audit = self.run_case('open_file', 'permission_denied')
        self.assertTrue(audit['passed'], audit)
        self.assertEqual(audit['failure_category'], 'verification_failure')
        self.assertEqual(audit['application_cause'], 'permission_denied')

    def test_success_status_without_goal_evidence_is_not_accepted(self):
        summary, app, _ = self.run_case('open_file')
        forged = deepcopy(summary)
        forged['goal_verification']['state']['met'] = False
        self.assertFalse(audit_result(forged, app.snapshot(), 'open_file', 'none')['passed'])


class LocalWebAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = LocalTaskState('open_file', self.temp.name)
        self.web = LocalWebApp(self.app, 'Unique test title')
        self.web.__enter__()
        self.addCleanup(self.web.__exit__, None, None, None)

    def post(self, data, origin=True, path=None):
        headers = {'Content-Type': 'application/json'}
        if origin: headers['Origin'] = self.web.origin
        return urlopen(Request(path or self.web.url + '/event', data=json.dumps(data).encode(), headers=headers), timeout=2)

    def test_page_and_event_really_cross_loopback_http(self):
        with urlopen(self.web.url, timeout=2) as response:
            self.assertIn(b'Unique test title', response.read())
        with self.post({'action': 'finish', 'value': ''}) as response:
            state = json.load(response)
        self.assertEqual(state['file_sha256'], hashlib.sha256(SAMPLE.encode()).hexdigest())
        self.assertTrue(self.web.loaded.is_set())

    def test_other_origins_are_rejected_without_effects(self):
        with self.assertRaises(HTTPError) as error: self.post({'action': 'finish', 'value': ''}, origin=False)
        self.assertEqual(error.exception.code, 403)
        self.assertFalse(self.app.values['file_open'])

    def test_arbitrary_body_fields_cannot_choose_a_file(self):
        with self.assertRaises(HTTPError) as error: self.post({'action': 'finish', 'value': '', 'path': 'other.txt'})
        self.assertEqual(error.exception.code, 400)
        self.assertFalse(self.app.values['file_open'])

    def test_unknown_routes_do_not_expose_local_files(self):
        with self.assertRaises(HTTPError) as error: urlopen(self.web.origin + '/sample.txt', timeout=2)
        self.assertEqual(error.exception.code, 404)


class OwnedBrowserBoundaryTests(unittest.TestCase):
    def browser(self, live=True, pid=101, title='Owned test'):
        browser = OwnedBrowser.__new__(OwnedBrowser)
        browser.handle, browser.pid, browser.title = 123, 101, 'Owned test'
        state = {'live': live, 'pid': pid, 'title': title, 'messages': []}
        def post(*args):
            state['messages'].append(args)
            state['live'] = False
        browser.user32 = SimpleNamespace(IsWindow=lambda _: state['live'], PostMessageW=post)
        browser.process_id = lambda _: state['pid']
        browser.name = lambda _: state['title']
        return browser, state

    def test_reused_hwnd_with_different_pid_is_never_closed(self):
        browser, state = self.browser(pid=202)
        browser.close()
        self.assertEqual(state['messages'], [])

    def test_changed_window_title_is_never_closed(self):
        browser, state = self.browser(title='Personal browser')
        with self.assertRaises(RuntimeError): browser.close()
        self.assertEqual(state['messages'], [])

    def test_title_change_does_not_count_as_closed(self):
        browser, state = self.browser()
        def changed(*args):
            state['messages'].append(args)
            state['title'] = 'Changing title while still open'
        browser.user32.PostMessageW = changed
        with patch('gui_tasks.browser.time.monotonic', side_effect=[0, 9]), self.assertRaises(TimeoutError):
            browser.close()
        self.assertTrue(browser.window_exists())
        self.assertEqual(len(state['messages']), 1)

    def test_owned_window_gets_one_graceful_close(self):
        browser, state = self.browser()
        browser.close()
        self.assertEqual(state['messages'], [(123, 0x0010, 0, 0)])
        browser.close()
        self.assertEqual(len(state['messages']), 1)

    def test_unowned_window_cannot_be_focused(self):
        browser, state = self.browser(pid=202)
        with self.assertRaises(RuntimeError): browser.focus()


if __name__ == '__main__':
    unittest.main()
