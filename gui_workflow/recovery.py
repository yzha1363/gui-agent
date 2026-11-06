"""Bounded observation/lookup recovery; a possible input dispatch is never replayed."""

from dataclasses import asdict, dataclass
import math
import time

from gui_control.controller import FocusLost, StopRequested
from gui_control.targets import TargetNotFound, select_text
from gui_workflow.observer import StaleTarget
from gui_workflow.runner import WorkflowError, WorkflowRunner, evaluate_observation
from gui_workflow.verification import StateUnavailable


class RecoveryError(ValueError):
    def __init__(self, code, message):
        super().__init__(message); self.code = code


@dataclass(frozen=True)
class RecoveryPolicy:
    observation_attempts: int = 3
    lookup_attempts: int = 3
    max_replans: int = 1
    max_observations: int = 80
    max_dispatches: int = 12
    session_timeout: float = 60
    observation_timeout: float = 5
    lookup_timeout: float = 8
    verification_timeout: float = 10
    retry_delay: float = .2
    max_snapshot_age: float = 2

    def __post_init__(self):
        for name, low, high in [('observation_attempts', 1, 5), ('lookup_attempts', 1, 5), ('max_replans', 0, 3),
                                ('max_observations', 1, 200), ('max_dispatches', 1, 32)]:
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high: raise ValueError('Invalid recovery ' + name)
        for name, low, high in [('session_timeout', .1, 600), ('observation_timeout', .1, 30), ('lookup_timeout', .1, 60),
                                ('verification_timeout', .1, 60), ('retry_delay', .01, 2), ('max_snapshot_age', .1, 10)]:
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('Invalid recovery ' + name)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError('Recovery policy requires exactly the documented fields.')
        return cls(**data)

    def to_dict(self): return asdict(self)


def retryable_observation(exc):
    current, seen = exc, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (PermissionError, StopRequested, FocusLost)) or getattr(current, 'winerror', None) == 5:
            return False
        current = current.__cause__
    return isinstance(exc, (StaleTarget, StateUnavailable)) and getattr(exc, 'retryable', True)


class RunBudget:
    """Monotonic cooperative limits: check again after every blocking operation."""
    def __init__(self, control, policy, event, clock=time.monotonic, sleep=time.sleep):
        self.control, self.policy, self.event = control, policy, event
        self.clock, self.sleep = clock, sleep
        self.started = clock(); self.deadline = self.started + policy.session_timeout
        self.identity = None; self.observations = 0; self.dispatches = 0

    def check(self, deadline=None, code='phase_timeout'):
        self.control.check()  # Stops/foreground errors take precedence over retry/time limits.
        identity = self.control.guard.active()
        if self.identity is None: self.identity = identity
        elif identity != self.identity: raise FocusLost('Window identity changed during recovery; no retargeting allowed.')
        if self.clock() >= self.deadline:
            raise RecoveryError('session_budget_exhausted', 'Session time budget exhausted; no further calls or input.')
        if deadline is not None and self.clock() >= deadline:
            raise RecoveryError(code, 'Recovery phase time budget exhausted.')

    def pause(self, seconds, deadline=None, code='phase_timeout'):
        end = min(self.clock() + seconds, self.deadline, deadline if deadline is not None else self.deadline)
        while self.clock() < end:
            self.check(deadline, code); self.sleep(min(.05, end - self.clock()))
        self.check(deadline, code)

    def observation(self, deadline, code='observation_timeout'):
        self.check(deadline, code)
        if self.observations >= self.policy.max_observations:
            raise RecoveryError('observation_budget_exhausted', 'Global observation count exhausted.')
        self.observations += 1

    def dispatch(self):
        self.check()
        if self.dispatches >= self.policy.max_dispatches:
            raise RecoveryError('dispatch_budget_exhausted', 'Global dispatch count exhausted.')
        self.dispatches += 1
        self.event('dispatch_entered', dispatch=self.dispatches)


class GuardedControl:
    def __init__(self, control, budget): self.control, self.budget = control, budget
    def __getattr__(self, name): return getattr(self.control, name)
    def check(self, *args, **kwargs):
        self.budget.check()
        if args or kwargs: return self.control.check(*args, **kwargs)
    def click(self, *args): self.budget.dispatch(); return self.control.click(*args)
    def type_text(self, *args): self.budget.dispatch(); return self.control.type_text(*args)
    def press(self, *args): self.budget.dispatch(); return self.control.press(*args)
    def hotkey(self, *args): self.budget.dispatch(); return self.control.hotkey(*args)


class RecoveringObserver:
    def __init__(self, observer, budget, environment='desktop', required_fields=()):
        self.observer, self.budget = observer, budget
        self.environment, self.required_fields = environment, set(required_fields)

    def validate(self, snapshot):
        source = snapshot.report.get('source', {})
        if (source.get('kind') != 'screen' or source.get('coordinate_space') != 'physical-desktop-pixels' or
            self.environment == 'desktop' and source.get('simulation')):
            raise RecoveryError('invalid_observation', 'Recovery requires the declared live/simulation observation source.')
        now = self.budget.clock(); captured = getattr(snapshot, 'captured_at', None)
        if type(captured) not in (int, float) or not math.isfinite(captured) or not 0 <= now - captured <= self.budget.policy.max_snapshot_age:
            raise StateUnavailable('Observation is expired or has an invalid capture time.')
        state = getattr(snapshot, 'state', None)
        if not state or not isinstance(state.get('values'), dict): raise StateUnavailable('Independent state missing.')
        if self.required_fields - set(state['values']): raise StateUnavailable('Declared independent state fields are missing.')

    def observe(self, label): return self.observe_until(label)

    def observe_until(self, label, phase_deadline=None, phase_code='observation_timeout'):
        budget, policy = self.budget, self.budget.policy
        local_deadline = budget.clock() + policy.observation_timeout
        code = phase_code if phase_deadline is not None and phase_deadline <= local_deadline else 'observation_timeout'
        deadline = min(budget.deadline, local_deadline,
                       phase_deadline if phase_deadline is not None else budget.deadline)
        for attempt in range(1, policy.observation_attempts + 1):
            budget.observation(deadline, code)
            try:
                snapshot = self.observer.observe(f'{label}_observe{attempt}')
                budget.check(deadline, code); self.validate(snapshot)
                budget.event('observation_ready', label=label, attempt=attempt, directory=str(snapshot.directory),
                             observation_count=budget.observations)
                return snapshot
            except (StaleTarget, StateUnavailable) as exc:
                budget.check(deadline, code)
                if not retryable_observation(exc) or attempt == policy.observation_attempts: raise
                budget.event('observation_retry', label=label, attempt=attempt, code=type(exc).__name__, message=str(exc))
                budget.pause(policy.retry_delay, deadline, code)

    def ensure_fresh(self, snapshot, target):
        self.budget.check(); self.validate(snapshot)
        result = self.observer.ensure_fresh(snapshot, target)
        self.budget.check(); self.validate(snapshot)
        return result


class RecoveringWorkflowRunner(WorkflowRunner):
    def __init__(self, control, observer, output, budget):
        self.budget = budget; self.last_summary = None
        super().__init__(control, observer, output, poll_interval=budget.policy.retry_delay, clock=budget.clock, sleep=budget.sleep)

    def persist(self, summary):
        self.last_summary = summary  # Keep uncertain dispatch markers even if persistence fails.
        super().persist(summary)

    def event(self, name, **fields):
        self.budget.event(name, **fields)

    def observe_until(self, label, row, deadline, phase_code='lookup_timeout'):
        snapshot = self.observer.observe_until(label, deadline, phase_code)
        row['observations'].append(str(snapshot.directory))
        return snapshot

    def execute_step(self, index, step, row, checkpoint):
        budget, policy = self.budget, self.budget.policy
        prefix, action = f'step{index:02d}', step['action']
        if action != 'wait_text':
            deadline = min(budget.deadline, self.clock() + policy.lookup_timeout)
            attempts = min(step['lookup_attempts'], policy.lookup_attempts)
            for attempt in range(1, attempts + 1):
                budget.check(deadline, 'lookup_timeout')
                try:
                    baseline = self.observe_until(f'{prefix}_before{attempt}', row, deadline)
                    evidence = evaluate_observation(baseline, step['expect'])
                    if step['expect']['transition'] and evidence['met']:
                        raise WorkflowError('condition_already_satisfied', 'Postcondition already holds; do not replay the action.')
                    if action == 'click_text':
                        row['target'] = select_text(baseline.report, step['text'], step['min_confidence'], step['contains'], step.get('occurrence'))
                    current = self.observe_until(f'{prefix}_predispatch{attempt}', row, deadline)
                    if baseline.state != current.state or baseline.report['source'].get('region') != current.report['source'].get('region'):
                        raise StaleTarget('Application state or bounds changed immediately before input.')
                    if action == 'click_text':
                        confirmed = select_text(current.report, step['text'], step['min_confidence'], step['contains'], step.get('occurrence'))
                        if confirmed['desktop_bbox'] != row['target']['desktop_bbox']:
                            raise StaleTarget('Latest OCR moved the target; reacquire instead of clicking old coordinates.')
                        row['target'] = confirmed
                        row['target_reconfirmed'] = True
                        row['freshness'] = self.observer.ensure_fresh(current, confirmed)
                    budget.check(deadline, 'lookup_timeout')
                    break
                except (TargetNotFound, StaleTarget) as exc:
                    self.event('lookup_retry', step=row['name'], attempt=attempt, code=type(exc).__name__, message=str(exc))
                    if attempt == attempts: raise WorkflowError('target_unavailable', str(exc)) from exc
                    budget.pause(policy.retry_delay, deadline, 'lookup_timeout')
            self.control.check()
            row['dispatch_started'] = True
            self.event('dispatch_started', step=row['name'], action=action); checkpoint()
            if action == 'click_text': self.control.click(*row['target']['desktop_center'])
            elif action == 'type_text': self.control.type_text(step['text'])
            elif action == 'press': self.control.press(step['key'])
            elif action == 'hotkey': self.control.hotkey(*step['keys'])
            row['action_sent'] = True; self.event('input_sent', step=row['name']); checkpoint()
        deadline = min(budget.deadline, self.clock() + min(step['timeout'], policy.verification_timeout))
        poll = 0
        while True:
            budget.check(deadline, 'verification_timeout'); poll += 1
            try: snapshot = self.observe_until(f'{prefix}_after{poll}', row, deadline, 'verification_timeout')
            except (StaleTarget, StateUnavailable) as exc:
                if not retryable_observation(exc): raise
                budget.pause(policy.retry_delay, deadline, 'verification_timeout'); continue
            evidence = evaluate_observation(snapshot, step['expect'])
            row['verification'] = {**evidence, 'directory': str(snapshot.directory), 'transition_required': step['expect']['transition'] if action != 'wait_text' else False}
            if evidence['met']: return
            budget.pause(min(policy.retry_delay, deadline - self.clock()), deadline, 'verification_timeout')
