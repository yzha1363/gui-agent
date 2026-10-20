"""Friday closed-loop demo. Runs only after BEGIN RUN is clicked."""

from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import tkinter as tk

from gui_perception.pipeline import enable_dpi_awareness

TITLE = 'GUI Agent Friday Demo'
ROOT = Path(__file__).resolve().parent


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    root.title(TITLE)
    root.geometry('1050x700+160+100')
    root.configure(bg='#f1f5f9')
    state = {'mode': 'normal', 'running': False, 'control': None}
    jobs = queue.Queue()
    condition_status = tk.StringVar(value='WAITING')
    submitted = tk.StringVar(value='')
    progress = tk.StringVar(value='Ready. Select a scenario, then begin.')
    mode_label = tk.StringVar(value='Scenario: normal')
    tk.Label(root, text='GUI Agent Friday Workflow', font=('Arial', 26), bg='#f1f5f9').place(x=24, y=20)
    tk.Label(root, textvariable=mode_label, font=('Arial', 16), bg='#f1f5f9').place(x=24, y=70)
    tk.Label(root, textvariable=condition_status, font=('Arial', 20), fg='#14532d', bg='#f1f5f9').place(x=24, y=105)
    entry = tk.Entry(root, font=('Microsoft YaHei', 22))
    entry.place(x=24, y=390, width=980, height=50)
    def focus_input():
        entry.focus_set()
        if state['mode'] != 'no_change':
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
        directory = ROOT / 'output' / 'friday-demo' / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        directory.mkdir(parents=True)
        try:
            plan = validate_plan(json.loads((ROOT / 'plans/friday_demo.json').read_text(encoding='utf-8')))
            with DesktopController(TITLE, log_path=directory / 'actions.jsonl') as control:
                state['control'] = control
                observer = ScreenObserver(control, directory, 'cuda')
                result = WorkflowRunner(control, observer, directory).run(plan)
            code = result.get('error', {}).get('code', '')
            jobs.put(('finished', f'{result["status"].upper()} {code} - {directory.name}'))
            print(json.dumps({'status': result['status'], 'summary': str(directory / 'summary.json')}, ensure_ascii=True), flush=True)
        except (Exception, KeyboardInterrupt) as exc:
            aborted = isinstance(exc, (StopRequested, KeyboardInterrupt)) or type(exc).__name__ == 'FailSafeException'
            error = {'status': 'aborted' if aborted else 'initialization_failed', 'success': False,
                     'error': {'code': type(exc).__name__, 'message': str(exc)}}
            (directory / 'summary.json').write_text(json.dumps(error, ensure_ascii=False, indent=2), encoding='utf-8')
            jobs.put(('finished', 'FAILED - ' + error['error']['message']))
        finally:
            state['control'] = None

    def begin():
        if state['running']:
            return
        reset()
        state['running'] = True
        progress.set('Running. Waiting for observed results after each action...')
        threading.Thread(target=worker, daemon=True).start()

    def stop():
        if state['control'] is not None:
            state['control'].stop()

    tk.Button(root, text='BEGIN RUN', font=('Arial', 16), command=begin).place(x=24, y=155, width=220, height=42)
    tk.Button(root, text='STOP', font=('Arial', 16), command=stop).place(x=270, y=155, width=150, height=42)
    def pump():
        while not jobs.empty():
            kind, message = jobs.get_nowait()
            progress.set(message)
            if kind == 'finished':
                state['running'] = False
        root.after(40, pump)
    reset()
    pump()
    root.mainloop()


if __name__ == '__main__':
    main()
