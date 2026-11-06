"""Recover only observation/lookup failures before any possible input dispatch."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from gui_agent.client import ChatClient
from gui_agent.session import AgentSession
from gui_control.controller import FocusLost, StopRequested
from gui_workflow.recovery import (GuardedControl, RecoveringObserver, RecoveringWorkflowRunner, RecoveryError,
                                    RecoveryPolicy, RunBudget)
from gui_workflow.runner import evaluate_observation
from gui_workflow.plan import validate_plan
from gui_workflow.verification import failure_category

REPLAN_CODES = {'planning_context_changed', 'target_unavailable', 'lookup_timeout'}
BUDGET_CODES = {'session_budget_exhausted', 'observation_budget_exhausted', 'dispatch_budget_exhausted',
                'observation_timeout', 'lookup_timeout'}


def category(error):
    code = error.get('code')
    if code in BUDGET_CODES: return 'recovery_budget_exhausted'
    if code == 'verification_timeout': return 'verification_failure'
    return error.get('category', 'execution_failure')


class BudgetClient:
    """One request at a time; no retry. Socket timeout is clamped to remaining time.

    The transport/OS operation is blocking: this is a cooperative deadline,
    not a promise that it can be interrupted at an exact wall-clock instant.
    """
    def __init__(self, client, budget): self.client, self.budget = client, budget
    def __getattr__(self, name): return getattr(self.client, name)
    def invoke(self, messages):
        self.budget.check()
        config = getattr(self.client, 'config', None)
        try:
            if isinstance(self.client, ChatClient):
                remaining = self.budget.deadline - self.budget.clock()
                self.client.config = replace(config, timeout=min(config.timeout, remaining))
            self.budget.event('planning_request', request_number=self.client.attempts + 1)
            response = self.client.invoke(messages)
            self.budget.check()
            return response
        finally:
            if isinstance(self.client, ChatClient): self.client.config = config


def subtask_ledger(plan, workflow):
    rows = {r['name']: r for r in (workflow or {}).get('steps', [])}
    ledger, completed = [], set()
    for subtask in plan.get('subtasks', []):
        names = subtask['step_names']
        verified = [name for name in names if rows.get(name, {}).get('verified') is True]
        failed = [name for name in names if rows.get(name, {}).get('status') in ('failed', 'aborted')]
        if len(verified) == len(names): status = 'verified'; completed.add(subtask['id'])
        elif failed: status = 'failed'
        elif set(subtask['depends_on']) - completed: status = 'blocked'
        else: status = 'pending'
        ledger.append({'id': subtask['id'], 'goal': subtask['goal'], 'depends_on': subtask['depends_on'],
                       'status': status, 'verified_steps': verified, 'failed_steps': failed, 'step_names': names})
    return ledger


class RecoverySession:
    def __init__(self, control, observer, planner, output, policy=None, environment='desktop', progress=None,
                 clock=time.monotonic, sleep=time.sleep):
        if environment not in ('desktop', 'simulation'): raise ValueError('Invalid environment.')
        self.control, self.observer, self.planner = control, observer, planner
        self.output, self.policy, self.environment = Path(output), policy or RecoveryPolicy(), environment
        self.progress, self.clock, self.sleep = progress, clock, sleep

    def run(self, instruction, window_title, state_fields, goal=None, execute=False, history=()):
        self.output.mkdir(parents=True, exist_ok=False)
        client = self.planner.client; initial_requests = client.attempts
        secret = getattr(getattr(client, 'config', None), 'api_key', '')
        def encode(value):
            text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
            return text.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[REDACTED]') if secret else text
        def save(name, value):
            path = self.output / name; pending = path.with_suffix('.tmp')
            pending.write_text(encode(value), encoding='utf-8'); pending.replace(path)
        def event(name, **fields):
            row = {'event': name, 'time_utc': datetime.now(timezone.utc).isoformat(), **fields}
            with (self.output / 'recovery-events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(encode(row).replace('\n', ' ') + '\n')
        budget = RunBudget(self.control, self.policy, event, self.clock, self.sleep)
        guarded = GuardedControl(self.control, budget)
        observer = RecoveringObserver(self.observer, budget, self.environment,
                                      state_fields if isinstance(state_fields, (list, tuple)) else ())
        summary = {'status': 'running', 'success': False, 'task_verified': False, 'environment': self.environment,
                   'client_kind': client.kind, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
                   'policy': self.policy.to_dict(), 'cycles': [], 'input_dispatches_started': 0,
                   'actions_completed': 0, 'desktop_input_sent': False, 'desktop_input_uncertain': False,
                   'replans_used': 0, 'real_model_validation': 'pending', 'diagnostic': None}
        runner_instances = []
        def runner_factory(control, watched, output):
            runner = RecoveringWorkflowRunner(control, watched, output, budget)
            runner_instances.append(runner); return runner
        self.planner.client = BudgetClient(client, budget)
        try:
            save('policy.json', self.policy.to_dict())
            for cycle in range(1, self.policy.max_replans + 2):
                budget.check(); event('cycle_started', cycle=cycle)
                destination = self.output / f'cycle-{cycle:02d}'
                previous_runner_count = len(runner_instances)
                result = AgentSession(guarded, observer, self.planner, destination, self.environment, runner_factory,
                                      self.progress).run(instruction, window_title, state_fields, goal, execute, history)
                workflows = [r.last_summary for r in runner_instances if r.last_summary is not None]
                rows = [row for workflow in workflows for row in workflow.get('steps', [])]
                dispatches = sum(bool(r.get('dispatch_started')) for r in rows)
                sent = sum(bool(r.get('action_sent')) for r in rows)
                # Counters remain conservative if AgentSession could not persist a workflow return.
                summary.update(input_dispatches_started=max(dispatches, budget.dispatches), actions_completed=sent,
                               desktop_input_sent=self.environment == 'desktop' and max(dispatches, budget.dispatches) > 0,
                               desktop_input_uncertain=self.environment == 'desktop' and max(dispatches, budget.dispatches) > sent)
                plan_path = destination / 'planning/task_plan.json'
                plan = json.loads(plan_path.read_text(encoding='utf-8')) if plan_path.exists() else {}
                workflow = runner_instances[-1].last_summary if len(runner_instances) > previous_runner_count else None
                ledger = subtask_ledger(plan, workflow)
                entry = {'cycle': cycle, 'status': result['status'], 'error': result.get('error'),
                         'summary': str(destination / 'summary.json'), 'subtasks': ledger}
                summary['cycles'].append(entry); save('summary.json', summary)
                if result['status'] in ('completed', 'already_satisfied', 'planned', 'needs_clarification'):
                    summary.update(status=result['status'], success=result['success'], task_verified=result['task_verified'])
                    summary.pop('error', None)
                    if 'goal_verification' in result: summary['goal_verification'] = result['goal_verification']
                    event('recovery_decision', decision='finished', status=result['status']); break
                error = result.get('error', {'code': 'unknown_failure', 'category': 'execution_failure'})
                error = {**error, 'category': category(error)}
                summary.update(status=result['status'], error=error)
                may_replan = (not summary['input_dispatches_started'] and error['code'] in REPLAN_CODES and
                              result['status'] != 'aborted' and summary['replans_used'] < self.policy.max_replans)
                if not may_replan:
                    summary['decision'] = 'stop_after_possible_input' if summary['input_dispatches_started'] else 'stop_without_replay'
                    event('recovery_decision', decision=summary['decision'], code=error['code']); break
                budget.check(); summary['replans_used'] += 1
                event('recovery_decision', decision='replan_before_any_input', code=error['code'], replan=summary['replans_used'])
                budget.pause(self.policy.retry_delay)
            if summary['status'] in ('failed', 'aborted') and summary['input_dispatches_started']:
                # Read-only diagnostic cannot turn an uncertain input failure into success.
                unsafe = summary.get('error', {}).get('category') in ('user_aborted', 'permission_denied', 'window_guard_failure', 'recovery_budget_exhausted')
                if not unsafe:
                    try:
                        snapshot = observer.observe('recovery_diagnostic')
                        condition = validate_plan({'window_title': window_title, 'steps': [
                            {'action': 'wait_text', 'expect': goal}]})['steps'][0]['expect'] if goal else None
                        evidence = evaluate_observation(snapshot, condition) if condition else None
                        summary['diagnostic'] = {'directory': str(snapshot.directory), 'goal': evidence,
                                                 'can_mark_success': False, 'action_replayed': False}
                        event('post_failure_observation', directory=str(snapshot.directory))
                    except (Exception, KeyboardInterrupt) as exc:
                        summary['diagnostic'] = {'unavailable': True, 'code': getattr(exc, 'code', type(exc).__name__)}
                else: summary['diagnostic'] = {'skipped': True, 'reason': 'stop_guard_permission_or_budget'}
        except (Exception, KeyboardInterrupt) as exc:
            aborted = isinstance(exc, (StopRequested, KeyboardInterrupt)) or type(exc).__name__ == 'FailSafeException'
            error = {'code': getattr(exc, 'code', type(exc).__name__), 'category': failure_category(exc), 'message': str(exc)}
            error['category'] = category(error)
            summary.update(status='aborted' if aborted else 'failed', success=False, task_verified=False, error=error)
        finally:
            self.planner.client = client
            rows = [row for r in runner_instances if r.last_summary for row in r.last_summary.get('steps', [])]
            dispatches = max(budget.dispatches, sum(bool(r.get('dispatch_started')) for r in rows))
            sent = sum(bool(r.get('action_sent')) for r in rows)
            summary.update(input_dispatches_started=dispatches, actions_completed=sent,
                           input_delivery_uncertain=dispatches > sent,
                           desktop_input_sent=self.environment == 'desktop' and dispatches > 0,
                           desktop_input_uncertain=self.environment == 'desktop' and dispatches > sent,
                           observation_attempts_used=budget.observations, controller_dispatch_entries=budget.dispatches,
                           request_attempts=client.attempts - initial_requests,
                           elapsed_budget_seconds=round(self.clock() - budget.started, 6),
                           finished_at_utc=datetime.now(timezone.utc).isoformat())
            save('feedback.json', {'cycles': summary['cycles'], 'diagnostic': summary['diagnostic'], 'status': summary['status'],
                                   'task_verified': summary['task_verified'], 'possible_input_dispatches': dispatches})
            save('summary.json', summary)
        return summary
