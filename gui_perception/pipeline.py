"""Capture physical screen pixels and retain coordinates for later desktop control."""

import ctypes
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent


def enable_dpi_awareness():
    """Avoid Windows DPI virtualization; screenshot coordinates are physical pixels."""
    if sys.platform != 'win32':
        return 'not-applicable'
    user32 = ctypes.windll.user32
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return 'per-monitor-v2'
    except AttributeError:
        pass
    # MSS also enables per-monitor awareness when it creates its capture context.
    return 'managed-by-mss'


def list_monitors():
    enable_dpi_awareness()
    import mss
    with mss.mss() as capture:
        return [dict(index=i, **monitor) for i, monitor in enumerate(capture.monitors)]


def capture_screen(monitor=1, region=None):
    """Capture a monitor or (left, top, width, height) physical-pixel region.

    Monitor 0 is the virtual desktop. Returned metadata retains the desktop
    origin so OCR coordinates can later be mapped back to a screen position.
    """
    dpi_mode = enable_dpi_awareness()
    import mss
    start = time.perf_counter()
    with mss.mss() as capture:
        monitors = list(capture.monitors)
        if region is None:
            if not 0 <= monitor < len(monitors):
                raise ValueError(f'Monitor {monitor} does not exist; use --list-monitors.')
            area = dict(monitors[monitor])
        else:
            left, top, width, height = region
            if width <= 0 or height <= 0:
                raise ValueError('Region width and height must be positive.')
            area = dict(left=left, top=top, width=width, height=height)
            virtual = monitors[0]
            if (left < virtual['left'] or top < virtual['top'] or
                left + width > virtual['left'] + virtual['width'] or
                top + height > virtual['top'] + virtual['height']):
                raise ValueError('Region must fit inside the virtual desktop bounds.')
        shot = capture.grab(area)
        image = Image.frombytes('RGB', shot.size, shot.rgb)
    return image, {
        'kind': 'screen', 'region': area, 'monitors': monitors,
        'dpi_mode': dpi_mode, 'coordinate_space': 'physical-desktop-pixels',
        'capture_ms': round((time.perf_counter() - start) * 1000, 2),
    }


def create_reader(device='auto', model_dir=None):
    import easyocr
    import torch
    if device == 'cuda' and not torch.cuda.is_available():
        raise RuntimeError('CUDA was requested but is unavailable. Use the project .venv.')
    use_gpu = torch.cuda.is_available() if device == 'auto' else device == 'cuda'
    models = Path(model_dir) if model_dir else ROOT / 'models' / 'easyocr'
    models.mkdir(parents=True, exist_ok=True)
    # Keep downloads and user networks in the workspace instead of the user profile.
    networks = models / 'user_network'
    networks.mkdir(exist_ok=True)
    reader = easyocr.Reader(
        ['ch_sim', 'en'], gpu=use_gpu, model_storage_directory=str(models),
        user_network_directory=str(networks), verbose=False,
    )
    return reader, 'cuda' if use_gpu else 'cpu'


def normalize_results(raw_results, origin=None, min_confidence=0.2):
    """Keep image-relative geometry; add desktop geometry only for live captures."""
    results = []
    for polygon, text, confidence in raw_results:
        confidence = float(confidence)
        if confidence < min_confidence:
            continue
        points = [[round(float(x), 2), round(float(y), 2)] for x, y in polygon]
        xs, ys = zip(*points)
        bbox = [min(xs), min(ys), max(xs), max(ys)]
        center = [round((bbox[0] + bbox[2]) / 2, 2), round((bbox[1] + bbox[3]) / 2, 2)]
        desktop_polygon = None if origin is None else [[x + origin[0], y + origin[1]] for x, y in points]
        results.append({
            'id': len(results) + 1, 'text': str(text), 'confidence': confidence,
            'polygon': points, 'bbox': bbox, 'center': center,
            'desktop_polygon': desktop_polygon,
            'desktop_bbox': None if origin is None else [bbox[0] + origin[0], bbox[1] + origin[1], bbox[2] + origin[0], bbox[3] + origin[1]],
            'desktop_center': None if origin is None else [center[0] + origin[0], center[1] + origin[1]],
        })
    return results


def annotation_font(size=16):
    for name in ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simhei.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def annotate(image, results):
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    font = annotation_font()
    for result in results:
        points = [tuple(point) for point in result['polygon']]
        draw.line(points + [points[0]], fill='#16a34a', width=2)
        label = f"{result['id']} {result['text']} {result['confidence']:.2f}"
        # Trim only the preview label, never the JSON text.
        while len(label) > 8 and draw.textbbox((0, 0), label, font=font)[2] > image.width - 4:
            label = label[:-2]
        bounds = draw.textbbox((0, 0), label, font=font)
        width, height = bounds[2] - bounds[0] + 6, bounds[3] - bounds[1] + 6
        x = max(0, min(int(result['bbox'][0]), image.width - width))
        y = max(0, min(int(result['bbox'][1]) - height, image.height - height))
        draw.rectangle((x, y, x + width, y + height), fill='#14532d')
        draw.text((x + 3, y + 3 - bounds[1]), label, fill='white', font=font)
    return annotated


def save_run(image, source, output_dir, reader=None, device=None,
             min_confidence=0.2, canvas_size=1600):
    """Write one immutable observation directory; reader=None means capture only."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    image.save(output_dir / 'screenshot.png')
    start = time.perf_counter()
    raw = [] if reader is None else reader.readtext(
        np.asarray(image), detail=1, paragraph=False, canvas_size=canvas_size,
        batch_size=1, workers=0,
    )
    origin = None
    if source['kind'] == 'screen':
        origin = (source['region']['left'], source['region']['top'])
    results = normalize_results(raw, origin, min_confidence)
    report = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'image_size': {'width': image.width, 'height': image.height},
        'source': source, 'languages': ['ch_sim', 'en'],
        'device': device, 'ocr_enabled': reader is not None,
        'min_confidence': min_confidence, 'canvas_size': canvas_size,
        'ocr_ms': round((time.perf_counter() - start) * 1000, 2),
        'result_count': len(results), 'results': results,
    }
    annotate(image, results).save(output_dir / 'annotated.png')
    (output_dir / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output_dir / 'text.txt').write_text('\n'.join(item['text'] for item in results), encoding='utf-8')
    return report
