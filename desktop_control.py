"""Thursday controls; coordinates are physical desktop pixels."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import time

from gui_control.controller import DesktopController
from gui_control.targets import select_text
from gui_perception.pipeline import capture_screen, create_reader, save_run


def main():
    parser = argparse.ArgumentParser(description='Window-scoped desktop input and live OCR text clicks')
    parser.add_argument('--window-title', required=True, help='Exact title; target must be foreground')
    parser.add_argument('--countdown', type=float, default=3, help='Seconds to focus the target window')
    parser.add_argument('--output', type=Path, default=Path('output/thursday'))
    parser.add_argument('--dry-run', action='store_true', help='Validate/locate without sending input')
    sub = parser.add_subparsers(dest='command', required=True)
    click = sub.add_parser('click')
    click.add_argument('x', type=int)
    click.add_argument('y', type=int)
    click.add_argument('--clicks', type=int, choices=[1, 2], default=1)
    click.add_argument('--button', choices=['left', 'right', 'middle'], default='left')
    text = sub.add_parser('type')
    text.add_argument('text')
    key = sub.add_parser('press')
    key.add_argument('key')
    chord = sub.add_parser('hotkey')
    chord.add_argument('keys', nargs='+')
    scroll = sub.add_parser('scroll')
    scroll.add_argument('clicks', type=int)
    scroll.add_argument('x', type=int)
    scroll.add_argument('y', type=int)
    drag = sub.add_parser('drag')
    for name in ['x1', 'y1', 'x2', 'y2']:
        drag.add_argument(name, type=int)
    target = sub.add_parser('click-text')
    target.add_argument('text')
    target.add_argument('--contains', action='store_true')
    target.add_argument('--occurrence', type=int)
    target.add_argument('--min-confidence', type=float, default=0.5)
    target.add_argument('--region', type=int, nargs=4)
    target.add_argument('--device', choices=['auto', 'cuda', 'cpu'], default='auto')
    args = parser.parse_args()
    if args.countdown < 0:
        parser.error('countdown must be nonnegative')
    print(f'Focus {args.window_title!r} within {args.countdown}s. Escape/corner stops input.', flush=True)
    time.sleep(args.countdown)
    try:
        with DesktopController(args.window_title, log_path=args.output / 'actions.jsonl') as control:
            control.check()
            if args.command == 'click-text':
                reader, device = create_reader(args.device)
                control.check()
                image, source = capture_screen(region=args.region)
                directory = args.output / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                report = save_run(image, source, directory, reader, device)
                target = select_text(report, args.text, args.min_confidence, args.contains, args.occurrence)
                (directory / 'target.json').write_text(json.dumps(target, ensure_ascii=False, indent=2), encoding='utf-8')
                control.point(*target['desktop_center'])
                print(json.dumps(target, ensure_ascii=True), flush=True)
                if not args.dry_run:
                    control.click(*target['desktop_center'])
            elif args.dry_run:
                if args.command in ('click', 'scroll'):
                    control.point(args.x, args.y)
                elif args.command == 'drag':
                    control.point(args.x1, args.y1)
                    control.point(args.x2, args.y2)
                print('Dry run: no input sent.')
            elif args.command == 'click':
                control.click(args.x, args.y, args.clicks, args.button)
            elif args.command == 'type':
                control.type_text(args.text)
            elif args.command == 'press':
                control.press(args.key)
            elif args.command == 'hotkey':
                control.hotkey(*args.keys)
            elif args.command == 'scroll':
                control.scroll(args.clicks, args.x, args.y)
            elif args.command == 'drag':
                control.drag(args.x1, args.y1, args.x2, args.y2)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
