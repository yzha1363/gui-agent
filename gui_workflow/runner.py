"""Execute once, then verify by fresh screenshots; never retry dispatched input."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from gui_control.controller import StopRequested
from gui_control.targets import normalized_text, select_text, TargetNotFound
from gui_workflow.observer import StaleTarget
from gui_workflow.plan import validate_plan


class WorkflowError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def evaluate_condition(report, condition):
    query = normalized_text(condition['text'])
    matches = []
    for item in report['results']:
        confidence = float(item['confidence'])
        if not math.isfinite(confidence) or confidence < condition['min_confidence']:
            continue
        candidate = normalized_text(item['text'])
        matched = query in candidate if condition['contains'] else query == candidate
        if matched:
            matches.append({'id': item['id'], 'text': item['text'], 'confidence': confidence})
    met = bool(matches) if condition['present'] else not matches
    return {'met': met, 'matches': matches}


class WorkflowRunner:
    """Coordinate observation, one input dispatch, and explicit OCR verification.

    Injected observers/controllers and clock functions allow tests to exercise
    failure paths without touching the desktop. Only observations are retried;
    dispatched input is never replayed automatically.
    """
    def __init__(self, control, observer, output, poll_interval=0.5, clock=time.monotonic, sleep=time.sleep):
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError('poll_interval must be positive and finite.')
        self.control, self.observer = control, observer
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.poll_interval, self.clock, self.sleep = poll_interval, clock, sleep

    def persist(self, summary):
        """Replace the last checkpoint atomically within this run's directory."""
        pending = self.output / 'summary.tmp'
        pending.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(self.output / 'summary.json')

    def event(self, name, **fields):
        with (self.output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'time_utc': datetime.now(timezone.utc).isoformat(),
                                     'event': name, **fields}, ensure_ascii=False) + '\n')

    def pause(self, seconds):
        end = self.clock() + seconds
        while self.clock() < end:
            self.control.check()
            self.sleep(max(0, min(0.05, end - self.clock())))

    def observe(self, label, row):
        snapshot = self.observer.observe(label)
        row['observations'].append(str(snapshot.directory))
        self.event('observation', step=row['name'], directory=str(snapshot.directory))
        return snapshot

    def run(self, plan):
        """Execute a validated plan and return its persisted final summary."""
        keys = getattr(getattr(self.control, 'backend', None), 'KEYBOARD_KEYS', None)
        plan = validate_plan(plan, keys)
        summary = {'status': 'running', 'success': False,
                   'started_at_utc': datetime.now(timezone.utc).isoformat(),
                   'window_title': plan['window_title'], 'steps': []}
        self.persist(summary)
        try:
            for index, step in enumerate(plan['steps'], 1):
                row = {'name': step['name'], 'action': step['action'], 'status': 'running',
                       'action_sent': False, 'verified': False, 'observations': []}
                summary['steps'].append(row)
                self.persist(summary)
                self.event('step_started', step=step['name'], action=step['action'])
                self.execute_step(index, step, row, lambda: self.persist(summary))
                row['status'] = 'verified'
                row['verified'] = True
                self.persist(summary)
                self.event('step_verified', step=step['name'])
            summary.update(status='completed', success=True)
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, (StopRequested, KeyboardInterrupt)) or type(exc).__name__ == 'FailSafeException'
            summary['status'] = 'aborted' if interrupted else 'failed'
            summary['error'] = {'code': getattr(exc, 'code', type(exc).__name__), 'message': str(exc)}
            if summary['steps']:
                summary['steps'][-1]['status'] = summary['status']
                summary['steps'][-1]['error'] = summary['error']
            self.event('workflow_stopped', status=summary['status'], error=summary['error'])
        finally:
            summary['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
            self.persist(summary)
        return summary

    def execute_step(self, index, step, row, checkpoint):
        action = step['action']
        prefix = f'step{index:02d}'
        self.control.check()
        baseline = None
        if action != 'wait_text':
            for attempt in range(1, step['lookup_attempts'] + 1):
                self.control.check()
                try:
                    baseline = self.observe(f'{prefix}_before{attempt}', row)
                    initial = evaluate_condition(baseline.report, step['expect'])
                    if step['expect']['transition'] and initial['met']:
                        raise WorkflowError('condition_already_satisfied', 'Expected condition already holds before the action; no input was sent.')
                    if action == 'click_text':
                        target = select_text(baseline.report, step['text'], step['min_confidence'],
                                             step['contains'], step.get('occurrence'))
                        row['target'] = target
                        row['freshness'] = self.observer.ensure_fresh(baseline, target)
                    break
                except (TargetNotFound, StaleTarget) as exc:
                    self.event('observation_retry', step=step['name'], attempt=attempt, error=str(exc))
                    if attempt == step['lookup_attempts']:
                        raise WorkflowError('target_unavailable', str(exc)) from exc
                    self.pause(self.poll_interval)
            self.control.check()
            # Set before dispatch: a partial input failure is uncertain and must not be retried.
            row['dispatch_started'] = True
            self.event('dispatch_started', step=step['name'], action=action)
            checkpoint()
            if action == 'click_text':
                self.control.click(*row['target']['desktop_center'])
            elif action == 'type_text':
                self.control.type_text(step['text'])
            elif action == 'press':
                self.control.press(step['key'])
            elif action == 'hotkey':
                self.control.hotkey(*step['keys'])
            row['action_sent'] = True
            self.event('input_sent', step=step['name'])
            checkpoint()
        deadline = self.clock() + step['timeout']
        poll = 0
        while True:
            self.control.check()
            poll += 1
            observation = self.observe(f'{prefix}_after{poll}', row)
            evidence = evaluate_condition(observation.report, step['expect'])
            row['verification'] = {**evidence, 'directory': str(observation.directory),
                                   'transition_required': step['expect']['transition'] if action != 'wait_text' else False}
            if evidence['met']:
                return
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise WorkflowError('verification_timeout', 'Fresh screenshots did not meet the expected condition; input will not be repeated.')
            self.pause(min(remaining, self.poll_interval))
