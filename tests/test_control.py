import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from gui_control.controller import ControlError, DesktopController, StopRequested
from gui_control.targets import TargetError, select_text


def report(*texts):
    return {'source': {'kind': 'screen', 'coordinate_space': 'physical-desktop-pixels',
                       'region': {'left': -1600, 'top': 150, 'width': 1600, 'height': 900}},
            'results': [{'id': i + 1, 'text': text, 'confidence': 0.9,
                         'bbox': [10, 20 + i * 100, 110, 60 + i * 100]}
                        for i, text in enumerate(texts)]}


class TargetTests(unittest.TestCase):
    def test_negative_origin_and_normalized_exact_text(self):
        target = select_text(report('ＣＬＩＣＫ  TARGET'), 'click target')
        self.assertEqual(target['desktop_center'], [-1540, 190])

    def test_duplicate_requires_explicit_occurrence(self):
        with self.assertRaises(TargetError):
            select_text(report('搜索', '搜索'), '搜索')
        self.assertEqual(select_text(report('搜索', '搜索'), '搜索', occurrence=2)['id'], 2)

    def test_low_confidence_and_missing_target(self):
        data = report('搜索')
        data['results'][0]['confidence'] = 0.1
        with self.assertRaises(TargetError):
            select_text(data, '搜索')
        with self.assertRaises(TargetError):
            select_text(report('搜索'), '保存')

    def test_image_and_invalid_boxes_are_rejected(self):
        data = report('搜索')
        data['source']['kind'] = 'image'
        with self.assertRaises(TargetError):
            select_text(data, '搜索')
        data = report('搜索')
        data['results'][0]['bbox'][0] = float('nan')
        with self.assertRaises(TargetError):
            select_text(data, '搜索')

    def test_substring_is_opt_in(self):
        with self.assertRaises(TargetError):
            select_text(report('搜索文件'), '搜索')
        self.assertEqual(select_text(report('搜索文件'), '搜索', contains=True)['id'], 1)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = Mock()
        self.backend.size.return_value = (1920, 1080)
        self.backend.KEYBOARD_KEYS = ['ctrl', 'a', 'end']
        self.guard = Mock()
        self.control = DesktopController('test', backend=self.backend, guard=self.guard,
                                         log_path=Path(self.temp.name) / 'actions.jsonl', listen=False)

    def tearDown(self):
        self.control.close()
        self.temp.cleanup()

    def test_focus_loss_sends_no_click_and_logs_failure(self):
        self.guard.check.side_effect = ControlError('focus lost')
        with self.assertRaises(ControlError):
            self.control.click(100, 200)
        self.backend.click.assert_not_called()
        row = json.loads(self.control.log_path.read_text())
        self.assertEqual(row['status'], 'failed')

    def test_bounds_and_stop_prevent_input(self):
        with self.assertRaises(ControlError):
            self.control.click(-1, 200)
        self.control.stop()
        with self.assertRaises(StopRequested):
            self.control.click(100, 200)
        self.backend.click.assert_not_called()

    def test_drag_releases_button_when_stopped(self):
        original = self.control.point
        count = 0
        def stop_mid_drag(x, y):
            nonlocal count
            count += 1
            if count == 3:
                self.control.stop()
            return original(x, y)
        self.control.point = stop_mid_drag
        with self.assertRaises(StopRequested):
            self.control.drag(100, 100, 300, 300, duration=0.2)
        self.backend.mouseUp.assert_called_once()

    def test_shortcut_releases_pressed_keys_on_failure(self):
        self.backend.keyDown.side_effect = [None, RuntimeError('input failed')]
        with self.assertRaises(RuntimeError):
            self.control.hotkey('ctrl', 'a')
        self.backend.keyUp.assert_called_once_with('ctrl', _pause=False)

    def test_windows_scroll_uses_full_wheel_delta(self):
        with unittest.mock.patch('gui_control.controller.sys.platform', 'win32'):
            self.control.scroll(-1, 100, 200)
        self.backend.scroll.assert_called_once_with(-120, x=100, y=200)

    def test_cleanup_restores_fail_safe_even_when_release_raises(self):
        self.backend.mouseUp.side_effect = RuntimeError('release failed')
        with self.assertRaises(RuntimeError):
            self.control.release(self.backend.mouseUp)
        self.assertTrue(self.backend.FAILSAFE)


if __name__ == '__main__':
    unittest.main()
