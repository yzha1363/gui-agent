import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gui_perception.pipeline import normalize_results, save_run


class CoordinateTests(unittest.TestCase):
    def test_negative_monitor_origin_and_confidence_filter(self):
        raw = [([[10, 20], [110, 20], [110, 60], [10, 60]], '搜索', 0.9),
               ([[0, 0], [1, 0], [1, 1], [0, 1]], 'noise', 0.1)]
        results = normalize_results(raw, origin=(-1920, 200), min_confidence=0.5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['center'], [60, 40])
        self.assertEqual(results[0]['desktop_center'], [-1860, 240])
        self.assertEqual(results[0]['desktop_bbox'], [-1910, 220, -1810, 260])

    def test_image_has_no_desktop_coordinates(self):
        result = normalize_results([([[0, 0], [10, 0], [10, 10], [0, 10]], 'text', 1)])[0]
        self.assertIsNone(result['desktop_center'])

    def test_blank_capture_preserves_dimensions_and_output_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / '中文路径'
            report = save_run(Image.new('RGB', (640, 480), 'white'), {'kind': 'image'}, output)
            self.assertEqual(report['result_count'], 0)
            self.assertFalse(report['ocr_enabled'])
            for name in ['screenshot.png', 'annotated.png']:
                with Image.open(output / name) as image:
                    self.assertEqual(image.size, (640, 480))
            self.assertTrue((output / 'results.json').exists())
            self.assertEqual((output / 'text.txt').read_text(encoding='utf-8'), '')


if __name__ == '__main__':
    unittest.main()
