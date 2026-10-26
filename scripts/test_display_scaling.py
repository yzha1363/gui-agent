"""Run one real Windows display-scale case without changing system settings.

Set the requested scale in Windows Display Settings first. This launcher
rejects a DPI mismatch rather than substituting resized images for OS scaling.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scale', type=int, choices=[100, 125, 200], required=True)
    parser.add_argument('--scenario', choices=['normal', 'duplicate', 'missing', 'no_change', 'false_ocr'], default='normal')
    args = parser.parse_args()
    directory = ROOT / 'output/revision/scaling' / f'{args.scale}-{args.scenario}' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    directory.mkdir(parents=True)
    print('Focus GUI Agent Friday Demo, then click BEGIN RUN. The launcher does not change resolution or scaling.', flush=True)
    process = subprocess.run([sys.executable, str(ROOT / 'friday_demo.py'), '--scenario', args.scenario,
                              '--output', str(directory), '--expected-scale', str(args.scale)], cwd=ROOT)
    return process.returncode


if __name__ == '__main__':
    raise SystemExit(main())
