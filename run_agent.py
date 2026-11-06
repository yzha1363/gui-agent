"""Run observation -> planning -> guarded workflow -> independent goal verification."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time

from gui_agent import ChatClient, GuiPlanner, ModelConfig, ReplayClient
from gui_agent.session import AgentSession
from gui_agent.simulation import run_simulation
from gui_control.controller import DesktopController, StopRequested
from gui_workflow.observer import ScreenObserver
from gui_workflow.verification import StateFile, failure_category

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Fully simulated app/OCR/input; no desktop access or network')
    parser.add_argument('--scenario', choices=['normal', 'duplicate', 'missing', 'no_change', 'false_ocr', 'partial_input'], default='normal')
    parser.add_argument('--instruction')
    parser.add_argument('--window-title')
    parser.add_argument('--state-file', type=Path)
    parser.add_argument('--state-fields', nargs='+')
    parser.add_argument('--goal-file', type=Path, help='Caller-supplied OCR + independent state goal, required for execute')
    parser.add_argument('--execute', action='store_true', help='Dispatch real input to the locked foreground window')
    parser.add_argument('--replay', type=Path)
    parser.add_argument('--base-url', default=os.environ.get('GUI_AGENT_BASE_URL', 'http://127.0.0.1:8000/v1'))
    parser.add_argument('--model', default=os.environ.get('GUI_AGENT_MODEL'))
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--max-tokens', type=int, default=2048)
    parser.add_argument('--max-steps', type=int, default=12)
    parser.add_argument('--device', choices=['auto', 'cuda', 'cpu'], default='auto')
    parser.add_argument('--countdown', type=float, default=5)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--recovery-policy', type=Path, help='Opt in to bounded observation/lookup recovery with a caller policy')
    args = parser.parse_args()
    output = args.output or ROOT / ('output/week3-thursday' if args.recovery_policy else 'output/week2-thursday') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    if args.demo:
        if args.execute or args.instruction or args.window_title or args.state_file or args.goal_file or args.replay or args.state_fields or args.recovery_policy:
            parser.error('--demo is a fully simulated fixture; do not combine it with desktop options')
        try:
            result, _ = run_simulation(output, args.scenario)
        except (OSError, ValueError) as exc:
            print(json.dumps({'status': 'failed', 'code': type(exc).__name__, 'desktop_input_sent': False}))
            return 2
    else:
        if not args.instruction or not args.window_title or not args.state_file or not args.state_fields:
            parser.error('live mode requires --instruction, --window-title, --state-file and --state-fields')
        if args.execute and not args.goal_file:
            parser.error('--execute requires --goal-file with trusted OCR and state expectations')
        if not math.isfinite(args.countdown) or not 0 <= args.countdown <= 30:
            parser.error('countdown must be finite in 0..30')
        created = False
        session_started = False
        try:
            recovery_policy = None
            if args.recovery_policy:
                from gui_workflow.recovery import RecoveryPolicy
                recovery_policy = RecoveryPolicy.from_dict(json.loads(args.recovery_policy.read_text(encoding='utf-8')))
            client = ReplayClient(args.replay.read_text(encoding='utf-8')) if args.replay else ChatClient(
                ModelConfig(args.base_url, args.model, os.environ.get('GUI_AGENT_API_KEY', ''), args.timeout, args.max_tokens))
            planner = GuiPlanner(client, args.max_steps)
            goal = json.loads(args.goal_file.read_text(encoding='utf-8-sig')) if args.goal_file else None
            output.mkdir(parents=True, exist_ok=False)
            created = True
            print(f'Focus {args.window_title!r} within {args.countdown}s. Execute={args.execute}. Output: {output}', flush=True)
            time.sleep(args.countdown)
            from gui_control.unicode_input import click_button
            with DesktopController(args.window_title, log_path=output / 'actions.jsonl', click_sender=click_button) as control:
                observer = ScreenObserver(control, output / 'observations', args.device,
                                          state_provider=StateFile(args.state_file, control))
                session_started = True
                session_type = AgentSession
                session_options = {}
                if recovery_policy is not None:
                    from gui_agent.recovery import RecoverySession
                    session_type = RecoverySession
                    session_options['policy'] = recovery_policy
                result = session_type(control, observer, planner, output / 'session', **session_options,
                                      progress=lambda stage: print(f'Agent stage: {stage}', flush=True)).run(
                    args.instruction, args.window_title, args.state_fields, goal, args.execute)
        except (Exception, KeyboardInterrupt) as exc:
            aborted = isinstance(exc, (StopRequested, KeyboardInterrupt))
            result = {'status': 'aborted' if aborted else 'initialization_failed', 'success': False,
                      'task_verified': False, 'desktop_input_sent': bool(args.execute and session_started),
                      'desktop_input_uncertain': bool(args.execute and session_started),
                      'error': {'code': type(exc).__name__, 'category': failure_category(exc),
                                'message': str(exc) if isinstance(exc, (StopRequested, KeyboardInterrupt)) else 'Agent initialization failed; inspect parameters, window and state adapter.'}}
            if created:
                (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(result, ensure_ascii=True))
            return 130 if aborted else 2
    print(json.dumps({'status': result['status'], 'success': result['success'],
                      'environment': result.get('environment'), 'summary': str(output / 'session/summary.json'),
                      'desktop_input_sent': result['desktop_input_sent'], 'error': result.get('error')}, ensure_ascii=True))
    if result['status'] == 'aborted':
        return 130
    return 0 if result['success'] or result['status'] in ('planned', 'needs_clarification') else 1


if __name__ == '__main__':
    raise SystemExit(main())
