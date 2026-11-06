"""Friday closed-loop demo. Runs only after BEGIN RUN is clicked."""

from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
import argparse
import ctypes
from ctypes import wintypes
import time

from gui_perception.pipeline import enable_dpi_awareness
from gui_workflow.verification import publish_state

TITLE = 'GUI Agent Friday Demo'
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', choices=['normal', 'duplicate', 'missing', 'no_change', 'false_ocr'], default='normal')
    parser.add_argument('--output', type=Path, default=ROOT / 'output' / 'friday-demo')
    parser.add_argument('--autorun', action='store_true', help='Run the controlled test app and close after saving results')
    parser.add_argument('--expected-scale', type=int, choices=[100, 125, 200])
    parser.add_argument('--agent-replay', type=Path, help='Use the integrated planner/session with an explicit replay reply')
    args = parser.parse_args()
    enable_dpi_awareness()
    root = tk.Tk()
    root.title(TITLE)
    root.geometry('1050x700+160+100')
    root.configure(bg='#f1f5f9')
    state = {'mode': args.scenario, 'running': False, 'control': None, 'state_write_denials': 0}
    jobs = queue.Queue()
    condition_status = tk.StringVar(value='WAITING')
    submitted = tk.StringVar(value='')
    progress = tk.StringVar(value='Ready. Select a scenario, then begin.')
    mode_label = tk.StringVar(value='Scenario: ' + args.scenario)
    tk.Label(root, text='GUI Agent Friday Workflow', font=('Arial', 26), bg='#f1f5f9').place(x=24, y=20)
    tk.Label(root, textvariable=mode_label, font=('Arial', 16), bg='#f1f5f9').place(x=24, y=70)
    tk.Label(root, textvariable=condition_status, font=('Arial', 20), fg='#14532d', bg='#f1f5f9').place(x=24, y=105)
    entry = tk.Entry(root, font=('Microsoft YaHei', 22))
    entry.place(x=24, y=390, width=980, height=50)
    def focus_input():
        if state['mode'] != 'false_ocr':
            entry.focus_set()
        if state['mode'] == 'false_ocr':
            condition_status.set('WRONG STATE')
        elif state['mode'] != 'no_change':
            condition_status.set('INPUT READY')
    target = tk.Button(root, text='INPUT FIELD', font=('Arial', 22), bg='white', command=focus_input)
    duplicate = tk.Button(root, text='INPUT FIELD', font=('Arial', 22), bg='white', command=focus_input)
    def submit():
        submitted.set('SUBMITTED ' + entry.get())
    submit_button = tk.Button(root, text='SUBMIT', font=('Arial', 22), bg='white', command=submit)
    submit_button.place(x=730, y=300, width=260, height=60)
    tk.Label(root, textvariable=submitted, font=('Microsoft YaHei', 20), bg='#f1f5f9').place(x=24, y=475)
    tk.Label(root, textvariable=progress, font=('Arial', 12), bg='#f1f5f9').place(x=24, y=540)
    tk.Label(root, text='Escape stops the workflow. Keep this test window in the foreground.',
             font=('Arial', 12), bg='#f1f5f9').place(x=24, y=610)

    def reset():
        entry.delete(0, 'end')
        condition_status.set('WAITING')
        submitted.set('')
        target.place_forget()
        duplicate.place_forget()
        if state['mode'] != 'missing':
            target.place(x=24, y=300, width=260, height=60)
        if state['mode'] == 'duplicate':
            duplicate.place(x=350, y=300, width=260, height=60)

    def set_mode(mode):
        if state['running']:
            return
        state['mode'] = mode
        mode_label.set('Scenario: ' + mode)
        reset()

    for index, mode in enumerate(['normal', 'duplicate', 'missing', 'no_change']):
        tk.Button(root, text=mode.upper().replace('_', ' '), font=('Arial', 14),
                  command=lambda selected=mode: set_mode(selected)).place(x=24 + index * 240, y=215, width=215, height=42)

    def worker():
        from gui_control.controller import DesktopController, StopRequested
        from gui_workflow.observer import ScreenObserver
        from gui_workflow.plan import validate_plan
        from gui_workflow.runner import WorkflowRunner
        from gui_workflow.verification import StateFile, failure_category
        directory = args.output / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        directory.mkdir(parents=True)
        try:
            display = display_snapshot()
            if args.expected_scale is not None and display['scale_percent'] != args.expected_scale:
                raise ValueError(f'Expected actual Windows scaling {args.expected_scale}%, got {display["scale_percent"]}%; no input sent.')
            plan = validate_plan(json.loads((ROOT / 'plans/friday_demo.json').read_text(encoding='utf-8')))
            if state['mode'] in {'no_change', 'false_ocr'}:
                plan['steps'][0]['timeout'] = 1
            from gui_control.unicode_input import click_button
            with DesktopController(TITLE, log_path=directory / 'actions.jsonl',
                                   click_sender=click_button if args.agent_replay else None) as control:
                state['control'] = control
                observer = ScreenObserver(control, directory, 'cuda', state_provider=StateFile(state_path, control))
                if state['mode'] == 'false_ocr':
                    original_observe = observer.observe
                    def injected_observe(label):
                        snapshot = original_observe(label)
                        if '_after' in label:
                            # Explicit fault injection: OCR says success although the entry lacks focus.
                            snapshot.report['results'].append({'id': 999, 'text': 'INPUT READY', 'confidence': 0.99})
                            snapshot.report['fault_injection'] = 'OCR false positive; independent widget state unchanged'
                            (snapshot.directory / 'results.json').write_text(json.dumps(snapshot.report, ensure_ascii=False, indent=2), encoding='utf-8')
                        return snapshot
                    observer.observe = injected_observe
                if args.agent_replay:
                    from gui_agent import GuiPlanner, ReplayClient
                    from gui_agent.session import AgentSession
                    from gui_agent.simulation import FIELDS, INSTRUCTION
                    reply = json.loads(args.agent_replay.read_text(encoding='utf-8'))
                    if state['mode'] in {'no_change', 'false_ocr'}:
                        reply['subtasks'][0]['steps'][0]['timeout'] = 1
                    planner = GuiPlanner(ReplayClient(json.dumps(reply, ensure_ascii=False)))
                    goal = json.loads((ROOT / 'docs/agent-goal.example.json').read_text(encoding='utf-8'))
                    result = AgentSession(control, observer, planner, directory / 'agent',
                                          progress=lambda stage: print('Agent stage: ' + stage, flush=True)).run(
                        INSTRUCTION, TITLE, FIELDS, goal, execute=True)
                else:
                    result = WorkflowRunner(control, observer, directory).run(plan)
                result['scenario'] = state['mode']
                result['display'] = display
                result['state_write_denials'] = state['state_write_denials']
                (directory / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            code = result.get('error', {}).get('code', '')
            jobs.put(('finished', f'{result["status"].upper()} {code} - {directory.name}'))
            print(json.dumps({'status': result['status'], 'summary': str(directory / 'summary.json')}, ensure_ascii=True), flush=True)
        except (Exception, KeyboardInterrupt) as exc:
            aborted = isinstance(exc, (StopRequested, KeyboardInterrupt)) or type(exc).__name__ == 'FailSafeException'
            error = {'status': 'aborted' if aborted else 'initialization_failed', 'success': False,
                     'error': {'code': type(exc).__name__, 'category': failure_category(exc), 'message': str(exc)}}
            (directory / 'summary.json').write_text(json.dumps(error, ensure_ascii=False, indent=2), encoding='utf-8')
            jobs.put(('finished', 'FAILED - ' + error['error']['message']))
        finally:
            state['control'] = None

    def begin():
        if state['running']:
            return
        reset()
        if args.agent_replay:
            root.lift()
            root.focus_force()
        state['running'] = True
        progress.set('Running. Waiting for observed results after each action...')
        threading.Thread(target=worker, daemon=True).start()

    def stop():
        if state['control'] is not None:
            state['control'].stop()

    tk.Button(root, text='BEGIN RUN', font=('Arial', 16), command=begin).place(x=24, y=155, width=220, height=42)
    tk.Button(root, text='STOP', font=('Arial', 16), command=stop).place(x=270, y=155, width=150, height=42)
    def pump():
        values = {'window_title': root.title(), 'input_focused': root.focus_get() == entry,
                  'entry_text': entry.get(), 'status': condition_status.get(),
                  'submitted': submitted.get(), 'submit_enabled': str(submit_button['state']) == 'normal'}
        export = {'timestamp': time.time(), 'hwnd': hwnd(), 'values': values}
        if not publish_state(state_path, export):
            state['state_write_denials'] += 1
        while not jobs.empty():
            kind, message = jobs.get_nowait()
            progress.set(message)
            if kind == 'finished':
                state['running'] = False
                if args.autorun:
                    root.after(300, root.destroy)
        root.after(40, pump)
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    def hwnd():
        return user32.GetAncestor(root.winfo_id(), 2)
    def display_snapshot():
        dpi = user32.GetDpiForWindow(hwnd())
        return {'dpi': dpi, 'scale_percent': round(dpi / 96 * 100),
                'screen_physical_pixels': [root.winfo_screenwidth(), root.winfo_screenheight()],
                'window_hwnd': hwnd(), 'dpi_source': 'GetDpiForWindow'}
    state_path = ROOT / 'output' / 'demo-state.json'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    root.update_idletasks()
    reset()
    pump()
    if args.autorun:
        root.after(1500, begin)
    root.mainloop()


if __name__ == '__main__':
    main()
