"""Connect fresh observation, planning, guarded execution and trusted goal checks."""

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from gui_agent.contracts import AgentError, AgentInput
from gui_control.controller import StopRequested
from gui_workflow.plan import validate_plan
from gui_workflow.runner import WorkflowRunner, evaluate_observation
from gui_workflow.verification import StateUnavailable, failure_category


@dataclass(frozen=True)
class LiveAgentInput(AgentInput):
    live_observation: dict = field(default_factory=dict)

    def prepare(self):
        prepared = super().prepare()
        try:
            encoded = json.dumps(self.live_observation, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            raise AgentError('invalid_observation', 'Live observation must be JSON data.') from None
        if len(encoded) > 24_000:
            raise AgentError('invalid_observation', 'Live observation context exceeds 24000 characters.')
        prepared['live_observation'] = deepcopy(self.live_observation)
        return prepared


class AgentSession:
    """One plan, fresh checks for every step, no automatic action/model replay."""
    def __init__(self, control, observer, planner, output, environment='desktop', runner_factory=WorkflowRunner, progress=None):
        if environment not in ('desktop', 'simulation'):
            raise ValueError('environment must be desktop or simulation.')
        self.control, self.observer, self.planner = control, observer, planner
        self.output, self.environment = Path(output), environment
        self.runner_factory, self.progress = runner_factory, progress

    def run(self, instruction, window_title, state_fields, goal=None, execute=False, history=()):
        self.output.mkdir(parents=True, exist_ok=False)
        secret = getattr(getattr(self.planner.client, 'config', None), 'api_key', '')

        def save(name, value):
            content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
            if secret:
                content = content.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[REDACTED]')
            path = self.output / name
            pending = path.with_suffix(path.suffix + '.tmp')
            pending.write_text(content, encoding='utf-8')
            pending.replace(path)

        started = time.perf_counter()
        attempts = self.planner.client.attempts
        summary = {'status': 'running', 'success': False, 'task_verified': False,
                   'environment': self.environment, 'execute_requested': execute, 'client_kind': self.planner.client.kind,
                   'desktop_input_sent': False, 'input_dispatches_started': 0, 'actions_completed': 0,
                   'started_at_utc': datetime.now(timezone.utc).isoformat(), 'observations': [],
                   'real_model_validation': 'pending', 'stage': 'initializing'}

        def stage(name):
            summary['stage'] = name
            save('summary.json', summary)
            if self.progress:
                self.progress(name)

        def observe(label):
            self.control.check()
            snapshot = self.observer.observe(label)
            summary['observations'].append(str(snapshot.directory))
            source = snapshot.report.get('source', {})
            if self.environment == 'desktop' and (source.get('kind') != 'screen' or
                source.get('coordinate_space') != 'physical-desktop-pixels' or source.get('simulation')):
                raise AgentError('invalid_observation', 'Desktop sessions require live physical-pixel captures.')
            state = getattr(snapshot, 'state', None)
            if not state or not isinstance(state.get('values'), dict):
                raise StateUnavailable('Independent application state is required.')
            return snapshot

        workflow = None
        try:
            if type(execute) is not bool:
                raise AgentError('invalid_config', 'execute must be boolean.')
            if execute and goal is None:
                raise AgentError('goal_required', 'Execution requires a caller-supplied OCR and independent-state goal.')
            if not isinstance(state_fields, (list, tuple)) or not state_fields or any(not isinstance(k, str) for k in state_fields):
                raise AgentError('invalid_config', 'Provide known independent adapter field names.')
            if getattr(self.control.guard, 'title', window_title) != window_title:
                raise AgentError('invalid_config', 'Controller window must match the caller window title.')
            condition = None
            if goal is not None:
                try:
                    condition = validate_plan({'window_title': window_title, 'steps': [
                        {'action': 'wait_text', 'expect': deepcopy(goal)}]})['steps'][0]['expect']
                except (ValueError, TypeError, KeyError):
                    raise AgentError('invalid_goal', 'Goal requires an explicit OCR condition and independent state.') from None
                if set(condition['state']) - set(state_fields):
                    raise AgentError('invalid_goal', 'Goal uses state fields not declared by the caller.')
            stage('observing_initial')
            initial = observe('agent_initial')
            identity = self.control.guard.active()
            values = initial.state['values']
            if set(state_fields) - set(values):
                raise AgentError('unsupported_state_field', 'Declared state fields are absent from the live adapter.')
            if condition is not None:
                evidence = evaluate_observation(initial, condition)
                if evidence['met']:
                    summary.update(status='already_satisfied', success=True, task_verified=True, goal_verification=evidence)
                    return summary
            # The file is written from the captured object, not a user-provided old screenshot path.
            image_path = self.output / 'planning-input.png'
            initial.image.save(image_path)
            ocr = [{'text': str(row['text'])[:200], 'confidence': float(row['confidence'])}
                   for row in initial.report['results'][:60]]
            live = {'ocr': ocr, 'ocr_truncated': len(initial.report['results']) > 60,
                    'independent_state': {k: values[k] for k in state_fields},
                    'state_source': initial.state.get('source'), 'goal': deepcopy(condition)}
            task = LiveAgentInput(instruction, image_path, window_title, tuple(history), tuple(state_fields), live)
            stage('planning')
            planning = self.planner.run(task, self.output / 'planning')
            summary['planning_summary'] = str(self.output / 'planning/summary.json')
            self.control.check()  # Honor a stop request even when the model failed or timed out.
            if planning['status'] in ('failed', 'aborted'):
                summary.update(status=planning['status'], error={**planning['error'], 'category':
                               'user_aborted' if planning['status'] == 'aborted' else 'planning_failure'})
                return summary
            stage('checking_planning_context')
            self.control.check()
            if self.control.guard.active() != identity:
                raise AgentError('planning_context_changed', 'Target window identity changed while planning.')
            current = observe('agent_after_planning')
            if (initial.report['source'].get('region') != current.report['source'].get('region') or
                initial.state != current.state):
                raise AgentError('planning_context_changed', 'Window bounds or independent state changed while planning; no input sent.')
            result = planning['task_plan']
            if result['status'] == 'clarify':
                summary.update(status='needs_clarification', clarification=result['reason'])
                return summary
            if result['status'] == 'done':
                raise AgentError('model_done_unverified', 'Model completion opinion does not satisfy the caller goal.')
            keys = getattr(getattr(self.control, 'backend', None), 'KEYBOARD_KEYS', None)
            candidate = validate_plan(result['workflow'], keys)
            if candidate['window_title'] != window_title:
                raise AgentError('planning_context_changed', 'Planner changed the caller window.')
            if not execute:
                summary.update(status='planned', candidate=str(self.output / 'planning/workflow.candidate.json'))
                return summary
            stage('executing')
            workflow = self.runner_factory(self.control, self.observer, self.output / 'workflow').run(candidate)
            summary['workflow_summary'] = str(self.output / 'workflow/summary.json')
            if not workflow['success']:
                summary.update(status=workflow['status'], error=workflow['error'])
                return summary
            stage('verifying_goal')
            final = observe('agent_goal')
            evidence = evaluate_observation(final, condition)
            summary['goal_verification'] = evidence
            if not evidence['met']:
                raise AgentError('goal_not_met', 'Steps passed but the caller goal did not satisfy both channels.')
            summary.update(status='completed', success=True, task_verified=True)
        except (Exception, KeyboardInterrupt) as exc:
            aborted = isinstance(exc, (StopRequested, KeyboardInterrupt)) or type(exc).__name__ == 'FailSafeException'
            code = getattr(exc, 'code', type(exc).__name__)
            category = {'planning_context_changed': 'context_changed', 'model_done_unverified': 'verification_failure',
                        'goal_not_met': 'verification_failure', 'invalid_goal': 'configuration_failure',
                        'goal_required': 'configuration_failure', 'invalid_config': 'configuration_failure',
                        'invalid_observation': 'observation_failure', 'unsupported_state_field': 'configuration_failure'}.get(code, failure_category(exc))
            summary.update(status='aborted' if aborted else 'failed', error={'code': code, 'category': category, 'message': str(exc)})
        finally:
            rows = workflow.get('steps', []) if workflow else []
            dispatches = sum(bool(row.get('dispatch_started')) for row in rows)
            sent = sum(bool(row.get('action_sent')) for row in rows)
            summary.update(input_dispatches_started=dispatches, actions_completed=sent,
                           desktop_input_sent=self.environment == 'desktop' and dispatches > 0,
                           desktop_input_uncertain=self.environment == 'desktop' and dispatches > sent,
                           request_attempts=self.planner.client.attempts - attempts,
                           elapsed_seconds=round(time.perf_counter() - started, 6),
                           finished_at_utc=datetime.now(timezone.utc).isoformat())
            save('feedback.json', {'workflow_steps': rows, 'goal': summary.get('goal_verification'),
                                   'status': summary['status'], 'task_verified': summary['task_verified']})
            save('summary.json', summary)
        return summary
