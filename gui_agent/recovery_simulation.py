"""Fault-injected fixtures. All screenshots, OCR, state and input are simulated."""

from dataclasses import replace
from copy import deepcopy
import json
from pathlib import Path

from gui_agent import GuiPlanner, ReplayClient
from gui_agent.recovery import RecoverySession
from gui_agent.simulation import FIELDS, INSTRUCTION, SimulatedApp, SimulatedObserver
from gui_workflow.observer import StaleTarget
from gui_workflow.recovery import RecoveryPolicy
from gui_workflow.verification import StateUnavailable

SCENARIOS = ('normal', 'transient_state', 'expired_snapshot', 'transient_target', 'stale_patch',
             'planning_change_once', 'planning_change_always', 'missing', 'duplicate', 'partial_input',
             'submitted_then_raise', 'false_ocr', 'state_unavailable', 'permission_denied', 'wrong_hwnd',
             'stop_during_delay', 'stop_during_model', 'stop_after_input', 'window_changed',
             'predispatch_changed', 'second_step_state_failure', 'observation_limit', 'dispatch_limit',
             'model_overrun', 'slow_observation', 'invalid_plan')

# Expected outcomes of injected fixtures, not model/desktop success claims.
EXPECTED = {
    name: ('completed', None, 1, 3) for name in ('normal', 'transient_state', 'expired_snapshot', 'transient_target', 'stale_patch', 'predispatch_changed')
}
EXPECTED.update({
    'planning_change_once': ('completed', None, 2, 3),
    'planning_change_always': ('failed', 'planning_context_changed', 2, 0),
    'missing': ('failed', 'target_unavailable', 2, 0), 'duplicate': ('failed', 'AmbiguousTarget', 1, 0),
    'partial_input': ('failed', 'RuntimeError', 1, 2), 'submitted_then_raise': ('failed', 'RuntimeError', 1, 3),
    'false_ocr': ('failed', 'verification_timeout', 1, 1), 'state_unavailable': ('failed', 'StateUnavailable', 0, 0),
    'permission_denied': ('failed', 'PermissionError', 0, 0), 'wrong_hwnd': ('failed', 'StateUnavailable', 0, 0),
    'stop_during_delay': ('aborted', 'StopRequested', 0, 0), 'stop_during_model': ('aborted', 'StopRequested', 1, 0),
    'stop_after_input': ('aborted', 'StopRequested', 1, 1), 'window_changed': ('failed', 'FocusLost', 1, 0),
    'second_step_state_failure': ('failed', 'StateUnavailable', 1, 1),
    'observation_limit': ('failed', 'observation_budget_exhausted', 1, 0),
    'dispatch_limit': ('failed', 'dispatch_budget_exhausted', 1, 2),
    'model_overrun': ('failed', 'session_budget_exhausted', 1, 0),
    'slow_observation': ('failed', 'observation_timeout', 0, 0), 'invalid_plan': ('failed', 'invalid_task_plan', 1, 0),
})


def run_recovery_simulation(output, scenario='normal', policy=None, execute=True):
    if scenario not in SCENARIOS: raise ValueError('Unknown recovery scenario.')
    root = Path(__file__).resolve().parents[1]; output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    policy = policy or RecoveryPolicy()
    if scenario == 'observation_limit': policy = replace(policy, max_observations=3)
    if scenario == 'dispatch_limit': policy = replace(policy, max_dispatches=1)
    if scenario == 'model_overrun': policy = replace(policy, session_timeout=.3)
    if scenario == 'slow_observation': policy = replace(policy, observation_timeout=.2)
    app = SimulatedApp(scenario if scenario in ('missing', 'duplicate', 'partial_input', 'false_ocr') else 'normal')
    observer = SimulatedObserver(app, output / 'observations')
    counters = {'observations': 0, 'freshness': 0, 'model_calls': 0}
    observe = observer.observe
    def injected_observe(label):
        counters['observations'] += 1
        count = counters['observations']
        if scenario == 'transient_state' and count <= 2: raise StateUnavailable('Injected transient stale state.')
        if scenario in ('state_unavailable', 'stop_during_delay'): raise StateUnavailable('Injected stale state.')
        if scenario == 'permission_denied': raise PermissionError('Injected state-file access denial.')
        if scenario == 'wrong_hwnd': raise StateUnavailable('Injected wrong HWND.', retryable=False)
        if scenario == 'second_step_state_failure' and 'step02_' in label:
            raise StateUnavailable('Injected adapter failure after focus.', retryable=False)
        if scenario == 'slow_observation': app.time += .3
        if scenario == 'predispatch_changed' and 'step01_predispatch1' in label: app.values['status'] = 'CHANGED'
        snapshot = observe(label)
        if scenario == 'expired_snapshot' and count == 1: snapshot.captured_at -= 3
        if scenario == 'transient_target' and 'step01_before1' in label:
            snapshot.report['results'] = [r for r in snapshot.report['results'] if r['text'] != 'INPUT FIELD']
            (snapshot.directory / 'results.json').write_text(json.dumps(snapshot.report, ensure_ascii=False, indent=2), encoding='utf-8')
        return snapshot
    observer.observe = injected_observe
    fresh = observer.ensure_fresh
    def injected_fresh(snapshot, target):
        counters['freshness'] += 1
        if scenario == 'stale_patch' and counters['freshness'] == 1: raise StaleTarget('Injected moving target patch.')
        return fresh(snapshot, target)
    observer.ensure_fresh = injected_fresh
    sleep = app.sleep
    def injected_sleep(seconds):
        sleep(seconds)
        if scenario == 'stop_during_delay': app.stopped = True
    app.sleep = injected_sleep
    click = app.click
    def injected_click(x, y):
        click(x, y)
        if scenario == 'stop_after_input': app.stopped = True
        if scenario == 'submitted_then_raise' and x >= 350: raise RuntimeError('Injected failure after submission; delivery uncertain.')
    app.click = injected_click
    value = json.loads((root / 'docs/agent-plan.example.json').read_text(encoding='utf-8'))
    for subtask in value['subtasks']:
        for step in subtask['steps']: step['timeout'] = .2
    if scenario == 'invalid_plan': value['subtasks'][-1]['steps'][0]['action'] = 'shell'
    client = ReplayClient(json.dumps(value, ensure_ascii=False)); invoke = client.invoke
    def injected_invoke(messages):
        counters['model_calls'] += 1; count = counters['model_calls']
        response = invoke(messages)
        if scenario == 'planning_change_always' or scenario == 'planning_change_once' and count == 1:
            app.values['status'] = f'CHANGED {count}'
        if scenario == 'stop_during_model': app.stopped = True
        if scenario == 'window_changed': app.identity = 102
        if scenario == 'model_overrun': app.time += 1
        return response
    client.invoke = injected_invoke
    goal = json.loads((root / 'docs/agent-goal.example.json').read_text(encoding='utf-8'))
    summary = RecoverySession(app, observer, GuiPlanner(client), output / 'session', policy,
                              environment='simulation', clock=lambda: app.time, sleep=app.sleep).run(
                                  INSTRUCTION, 'GUI Agent Friday Demo', FIELDS, goal, execute)
    (output / 'simulated_actions.json').write_text(json.dumps(app.calls, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'fixture.json').write_text(json.dumps({'kind': 'simulated_app_ocr_state_and_input', 'scenario': scenario,
        'counters': counters, 'policy': policy.to_dict(), 'final_state': deepcopy(app.values)}, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary, app
