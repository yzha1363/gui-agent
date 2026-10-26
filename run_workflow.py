"""Run an explicit JSON workflow with screenshot-based postconditions."""

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import time

from gui_control.controller import DesktopController, StopRequested
from gui_workflow.observer import ScreenObserver
from gui_workflow.plan import validate_plan
from gui_workflow.runner import WorkflowRunner
from gui_workflow.verification import StateFile, failure_category


def main():
    parser = argparse.ArgumentParser(description='Observe, act once, then verify using fresh OCR')
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--device', choices=['auto', 'cuda', 'cpu'], default='auto')
    parser.add_argument('--output', type=Path, default=Path('output/friday'))
    parser.add_argument('--countdown', type=float, default=5)
    parser.add_argument('--validate-only', action='store_true', help='Check the plan without desktop access')
    parser.add_argument('--state-file', type=Path, help='Cooperative app state export; never an OCR report')
    args = parser.parse_args()
    if not math.isfinite(args.countdown) or args.countdown < 0:
        parser.error('countdown must be finite and nonnegative')
    try:
        plan = validate_plan(json.loads(args.plan.read_text(encoding='utf-8-sig')))
        if args.validate_only:
            print(f'Plan structure valid: {len(plan["steps"])} steps. No desktop input sent.')
            return 0
        if args.state_file is None:
            parser.error('--state-file is required for independent state verification')
        directory = args.output / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        directory.mkdir(parents=True, exist_ok=False)
        print(f'Focus {plan["window_title"]!r} within {args.countdown}s. Output: {directory.resolve()}', flush=True)
        time.sleep(args.countdown)
        with DesktopController(plan['window_title'], log_path=directory / 'actions.jsonl') as control:
            plan = validate_plan(plan, control.backend.KEYBOARD_KEYS)
            try:
                observer = ScreenObserver(control, directory, args.device, state_provider=StateFile(args.state_file, control))
            except (Exception, KeyboardInterrupt) as exc:
                aborted = isinstance(exc, (StopRequested, KeyboardInterrupt)) or type(exc).__name__ == 'FailSafeException'
                (directory / 'summary.json').write_text(json.dumps({
                    'status': 'aborted' if aborted else 'initialization_failed', 'success': False,
                    'error': {'code': type(exc).__name__, 'category': failure_category(exc), 'message': str(exc)},
                }, ensure_ascii=False, indent=2), encoding='utf-8')
                raise
            result = WorkflowRunner(control, observer, directory).run(plan)
        print(json.dumps({'status': result['status'], 'summary': str(directory / 'summary.json')}, ensure_ascii=True))
        return 0 if result['success'] else 1
    except (Exception, KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
