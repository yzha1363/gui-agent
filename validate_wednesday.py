"""Reproducible bilingual OCR checks at several image sizes; no UI changes."""

import json
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from gui_perception.pipeline import create_reader, save_run


def main():
    root = Path('output/wednesday-validation') / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    root.mkdir(parents=True, exist_ok=True)
    reader, device = create_reader('cuda')
    cases = []
    for width, height, scale in [(1280, 720, 1), (1920, 1080, 1.25), (2560, 1440, 1.5)]:
        image = Image.new('RGB', (width, height), '#f1f5f9')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', int(36 * scale))
        for index, text in enumerate(['GUI Agent Test', '屏幕截图与文字识别', '搜索文件', 'Hello World 12345']):
            draw.text((int(60 * scale), int((60 + index * 100) * scale)), text, font=font, fill='#111827')
        directory = root / f'{width}x{height}'
        report = save_run(image, {'kind': 'image', 'coordinate_space': 'image-pixels'}, directory, reader, device)
        texts = [r['text'] for r in report['results']]
        joined = ''.join(texts).replace(' ', '')
        checks = {'english': 'GUIAgentTest' in joined,
                  'chinese': '屏幕截图与文字识别' in joined,
                  'numbers': '12345' in joined,
                  'short_label': '搜索文件' in joined,
                  'bounds': all(0 <= r['center'][0] < width and 0 <= r['center'][1] < height for r in report['results'])}
        cases.append({'size': [width, height], 'simulated_text_scale': scale, 'checks': checks,
                      'recognized_texts': texts, 'ocr_ms': report['ocr_ms']})
    # Check that a blank image does not create spurious detections.
    blank = save_run(Image.new('RGB', (800, 600), 'white'), {'kind': 'image'}, root / 'blank', reader, device)
    summary = {'device': device, 'cases': cases, 'blank_result_count': blank['result_count'],
               'passed': all(all(case['checks'].values()) for case in cases) and blank['result_count'] == 0}
    (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
