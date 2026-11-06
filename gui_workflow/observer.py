"""Reuse one OCR reader and save every observation to a distinct directory."""

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np
from PIL import Image

from gui_control.controller import ControlError
from gui_perception.pipeline import capture_screen, create_reader, save_run
from gui_workflow.verification import StateUnavailable
import json


class StaleTarget(ControlError):
    pass


@dataclass
class Observation:
    report: dict
    image: Image.Image
    directory: Path
    captured_at: float
    state: dict | None = None


class ScreenObserver:
    def __init__(self, control, output, device='auto', canvas_size=1600, drift_threshold=12, state_provider=None):
        self.control = control
        self.output = Path(output)
        self.canvas_size = canvas_size
        self.drift_threshold = drift_threshold
        self.counter = 0
        self.state_provider = state_provider
        control.check()
        self.reader, self.device = create_reader(device)
        control.check()

    def observe(self, label):
        self.control.check()
        if self.state_provider is None:
            raise StateUnavailable('Configure an independent application state adapter before running.', retryable=False)
        state = self.state_provider()
        region = self.control.window_region()
        captured_at = time.monotonic()
        image, source = capture_screen(region=region)
        self.counter += 1
        directory = self.output / f'{self.counter:04d}_{label}'
        report = save_run(image, source, directory, self.reader, self.device, canvas_size=self.canvas_size)
        (directory / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        self.control.check()
        if region != self.control.window_region():
            raise StaleTarget('Window moved while observing; coordinates are stale.')
        if state != self.state_provider():
            raise StaleTarget('Application state changed while observing; recapture both channels.')
        return Observation(report, image, directory, captured_at, state)

    def ensure_fresh(self, observation, target):
        """Check the actual text patch again immediately before dispatching a click."""
        self.control.check()
        source = observation.report['source']['region']
        region = [source['left'], source['top'], source['width'], source['height']]
        if self.control.window_region() != region:
            raise StaleTarget('Window bounds changed before clicking.')
        fresh, _ = capture_screen(region=region)
        fresh.save(observation.directory / 'preclick.png')
        left, top, right, bottom = target['desktop_bbox']
        box = (max(0, int(left - source['left']) - 3), max(0, int(top - source['top']) - 3),
               min(fresh.width, int(right - source['left']) + 4), min(fresh.height, int(bottom - source['top']) + 4))
        old = np.asarray(observation.image.crop(box).convert('L'), dtype=np.float32)
        new = np.asarray(fresh.crop(box).convert('L'), dtype=np.float32)
        difference = float(np.mean(np.abs(new - old)))
        if difference > self.drift_threshold:
            raise StaleTarget(f'Target patch changed before clicking (mean pixel difference {difference:.2f}).')
        self.control.point(*target['desktop_center'])
        return {'patch_mean_difference': difference, 'age_seconds': round(time.monotonic() - observation.captured_at, 3)}
